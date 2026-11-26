# -*- coding: utf-8 -*-
"""Unit tests for the 3 runtime bugfixes:
1. code 10008 (duplicate offline task) with data: [] (no info_hash) in OpenAPI_115 and batch_task_retry.
2. generate_magnet_txt_file() calling OpenAPI_115.upload_file() with valid kwargs and cleaning temp file.
3. Canonical URL matching for URL-encoded/space-variant ed2k:// and case/base32-variant magnet: links.
"""

import base64
import hashlib
import os
import shutil
import sys
import threading
import time
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(ROOT_DIR, "app")
for p in (ROOT_DIR, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import init
from app.core.open_115 import (
    OpenAPI_115,
    NonRetryableAPIError,
    canonical_offline_key,
    build_offline_task_index,
    lookup_offline_task,
)
from app.core import batch_task_manager as btm
from app.core import batch_task_retry as btr
from app.handlers.download_handler import _download_tasks_batch_impl
from app.utils.sqlitelib import SqlLiteLib


class _DummyLogger:
    def debug(self, msg, *a, **kw): pass
    def info(self, msg, *a, **kw): pass
    def warn(self, msg, *a, **kw): pass
    def warning(self, msg, *a, **kw): pass
    def error(self, msg, *a, **kw): pass


def _make_openapi_client() -> OpenAPI_115:
    init.logger = _DummyLogger()
    client = OpenAPI_115.__new__(OpenAPI_115)
    client.access_token = "test_access"
    client.refresh_token = "test_refresh"
    client.base_url = "https://proapi.115.com"
    client._rate_limit_lock = threading.Lock()
    client._last_request_time = 0
    client._rate_limit_interval = 0.0
    client._offline_page1_cache_lock = threading.Lock()
    client._offline_page1_cache_ts = 0.0
    client._offline_page1_cache_data = None
    client._offline_page1_cache_ttl = 3.0
    return client


def test_canonical_offline_key_and_index_matching():
    # 1. URL-encoded / space-variant / case-variant ed2k links match via MD4 hash
    ed2k_encoded = "ed2k://|file|My%20Movie%20%5B1080p%5D.mp4|1048576|ABCDEF0123456789ABCDEF0123456789|/"
    ed2k_decoded = "ed2k://|file|My Movie [1080p].mp4|1048576|abcdef0123456789abcdef0123456789|"
    assert canonical_offline_key(ed2k_encoded) == "ed2k:abcdef0123456789abcdef0123456789"
    assert canonical_offline_key(ed2k_encoded) == canonical_offline_key(ed2k_decoded)

    # 2. Case-variant, dn-variant, and Base32 vs Hex magnet links match via BTIH
    hex_hash = "0123456789abcdef0123456789abcdef01234567"
    b32_hash = base64.b32encode(bytes.fromhex(hex_hash)).decode("ascii")
    m_upper_dn = f"MAGNET:?xt=urn:btih:{hex_hash.upper()}&dn=Some%20Movie&tr=http://tracker"
    m_lower_bare = f"magnet:?xt=urn:btih:{hex_hash}"
    m_base32 = f"magnet:?xt=urn:btih:{b32_hash}"
    assert canonical_offline_key(m_upper_dn) == f"btih:{hex_hash}"
    assert canonical_offline_key(m_lower_bare) == f"btih:{hex_hash}"
    assert canonical_offline_key(m_base32) == f"btih:{hex_hash}"

    # 3. build_offline_task_index + lookup_offline_task matches across variants
    cloud_tasks = [
        {"url": ed2k_decoded, "name": "My Movie [1080p].mp4", "status": 2, "percentDone": 100, "info_hash": "ih_ed2k"},
        {"url": m_lower_bare, "name": "Some Movie", "status": 1, "percentDone": 50, "info_hash": hex_hash},
    ]
    idx = build_offline_task_index(cloud_tasks)
    assert lookup_offline_task(idx, ed2k_encoded)["info_hash"] == "ih_ed2k"
    assert lookup_offline_task(idx, m_upper_dn)["name"] == "Some Movie"
    assert lookup_offline_task(idx, m_base32)["name"] == "Some Movie"

    # 4. get_offline_tasks_for_links stops early on page 1 when canonical keys match
    client = _make_openapi_client()
    client.get_offline_tasks_by_page = MagicMock(
        side_effect=lambda page=1: {
            "page_count": 20,
            "tasks": cloud_tasks if page == 1 else [],
        }
    )
    res = client.get_offline_tasks_for_links([ed2k_encoded, m_upper_dn], use_cache=False)
    assert len(res) == 2
    assert client.get_offline_tasks_by_page.call_count == 1


def test_10008_single_url_without_info_hash():
    resp_10008 = {
        "state": False,
        "message": "任务已存在，请勿输入重复的链接地址",
        "code": 10008,
        "data": [],
    }
    resp_ok = {"state": True, "message": "ok", "code": 0, "data": []}
    target_url = "ed2k://|file|Ep%2001.mp4|12345|0123456789ABCDEF0123456789ABCDEF|/"
    cloud_url = "ed2k://|file|Ep 01.mp4|12345|0123456789abcdef0123456789abcdef|/"

    # Case A: Existing duplicate on page 12 is FAILED (status == -1) -> delete(source=True), wait 1s, resubmit once -> True
    client = _make_openapi_client()
    client.get_file_info = MagicMock(return_value={"file_id": "cid_save"})
    client._make_api_request = MagicMock(side_effect=[resp_10008, resp_ok])
    client.del_faild_offline_task = MagicMock(return_value=True)

    def _paged_failed(page=1):
        if page == 12:
            return {
                "page_count": 25,
                "tasks": [{"url": cloud_url, "name": "Ep 01.mp4", "status": -1, "percentDone": 0, "info_hash": "ih_fail12"}],
            }
        return {"page_count": 25, "tasks": [{"url": f"magnet:?xt=urn:btih:{page:040x}", "name": f"P{page}", "status": 2, "percentDone": 100, "info_hash": f"p{page}"}]}

    client.get_offline_tasks_by_page = MagicMock(side_effect=_paged_failed)
    with patch("app.core.open_115.time.sleep") as mock_sleep:
        ok = client.offline_download_specify_path(target_url, "/dl/save")
    assert ok is True
    # Stopped early at page 12 instead of crawling all 25 pages
    assert client.get_offline_tasks_by_page.call_count == 12
    client.del_faild_offline_task.assert_called_once_with("ih_fail12", delete_source_file=True)
    mock_sleep.assert_called_once_with(1)
    assert client._make_api_request.call_count == 2

    # Case B: Existing duplicate on page 10 is ALREADY COMPLETED (status == 2) -> return True WITHOUT deleting source file
    client2 = _make_openapi_client()
    client2.get_file_info = MagicMock(return_value={"file_id": "cid_save"})
    client2._make_api_request = MagicMock(return_value=resp_10008)
    client2.del_faild_offline_task = MagicMock(return_value=True)
    client2.get_offline_tasks_by_page = MagicMock(
        side_effect=lambda page=1: {
            "page_count": 15,
            "tasks": (
                [{"url": cloud_url, "name": "Ep 01.mp4", "status": 2, "percentDone": 100, "info_hash": "ih_done10"}]
                if page == 10 else []
            ),
        }
    )
    with patch("app.core.open_115.time.sleep") as mock_sleep2:
        ok2 = client2.offline_download_specify_path(target_url, "/dl/save")
    assert ok2 is True
    client2.del_faild_offline_task.assert_not_called()
    mock_sleep2.assert_not_called()
    assert client2._make_api_request.call_count == 1

    # Case C: Unrecoverable 10008 (not found in cloud) -> raises NonRetryableAPIError and @handle_token_expiry does NOT retry twice
    client3 = _make_openapi_client()
    client3.get_file_info = MagicMock(return_value={"file_id": "cid_save"})
    client3._make_api_request = MagicMock(return_value=resp_10008)
    client3.get_offline_tasks_by_page = MagicMock(return_value={"page_count": 2, "tasks": []})
    raised = None
    try:
        client3.offline_download_specify_path(target_url, "/dl/save")
    except NonRetryableAPIError as e:
        raised = e
    assert raised is not None and raised.code == 10008
    # Crucially: POST was called only ONCE (not retried by @handle_token_expiry)
    assert client3._make_api_request.call_count == 1


def test_10008_batch_without_info_hash():
    resp_10008 = {
        "state": False,
        "message": "任务已存在，请勿输入重复的链接地址",
        "code": 10008,
        "data": [],
    }
    resp_ok = {"state": True, "message": "ok", "code": 0, "data": []}
    u_done = "magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    u_fail = "magnet:?xt=urn:btih:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
    u_new = "magnet:?xt=urn:btih:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"

    # Case A: Mixed batch [u_done (completed), u_fail (failed), u_new (not in cloud)]
    # -> u_done deleted with delete_source_file=False (keeps file!), u_fail deleted with delete_source_file=True, resubmit once -> True
    client = _make_openapi_client()
    client.get_file_info = MagicMock(return_value={"file_id": "cid_batch"})
    client._make_api_request = MagicMock(side_effect=[resp_10008, resp_ok])
    client.del_faild_offline_task = MagicMock(return_value=True)
    client.get_offline_tasks_by_page = MagicMock(
        side_effect=lambda page=1: {
            "page_count": 5,
            "tasks": (
                [
                    {"url": u_done.lower(), "name": "Done.mp4", "status": 2, "percentDone": 100, "info_hash": "ih_done"},
                    {"url": u_fail.lower(), "name": "Fail.mp4", "status": -1, "percentDone": 0, "info_hash": "ih_fail"},
                ]
                if page == 5 else []
            ),
        }
    )
    with patch("app.core.open_115.time.sleep"):
        ok = client.offline_download_specify_path_batch([u_done, u_fail, u_new], "/dl/batch")
    assert ok is True
    del_calls = {c.args[0]: c.kwargs["delete_source_file"] for c in client.del_faild_offline_task.call_args_list}
    assert del_calls == {"ih_done": False, "ih_fail": True}
    assert client._make_api_request.call_count == 2

    # Case B: All URLs in batch are already completed -> returns True directly WITHOUT deleting any record or source file
    client2 = _make_openapi_client()
    client2.get_file_info = MagicMock(return_value={"file_id": "cid_batch"})
    client2._make_api_request = MagicMock(return_value=resp_10008)
    client2.del_faild_offline_task = MagicMock(return_value=True)
    client2.get_offline_tasks_by_page = MagicMock(
        return_value={
            "page_count": 1,
            "tasks": [
                {"url": u_done.lower(), "name": "Done.mp4", "status": 2, "percentDone": 100, "info_hash": "ih_done"},
            ],
        }
    )
    ok2 = client2.offline_download_specify_path_batch([u_done], "/dl/batch")
    assert ok2 is True
    client2.del_faild_offline_task.assert_not_called()
    assert client2._make_api_request.call_count == 1


def test_batch_task_retry_handles_deep_page_duplicates_and_canonical_ed2k():
    init.logger = _DummyLogger()
    init.bot_config = {"allowed_user": [100]}
    os.makedirs(os.path.join(ROOT_DIR, "tmp"), exist_ok=True)
    test_db = os.path.join(ROOT_DIR, "tmp", "test_runtime_bugfixes_retry.db")
    if os.path.exists(test_db):
        os.remove(test_db)

    old_db = init.DB_FILE
    try:
        init.DB_FILE = test_db
        btm.initialize_batch_task_tables(reset_sql=False)
        from app.utils.bot_settings import set_auto_retry_enabled
        set_auto_retry_enabled(True)

        # Case A: Previously submitted item (last_retry_at set) completed on page 12 (missed by fast _cloud_map)
        ed2k_sub = "ed2k://|file|Deep%20File.mkv|9999|11112222333344445555666677778888|/"
        ed2k_cloud = "ed2k://|file|Deep File.mkv|9999|11112222333344445555666677778888|"
        gid1 = btm.create_single_retry_group("", ed2k_sub, "/dl", chat_id=100, origin_message_id=701)
        with SqlLiteLib() as sqlite:
            sqlite.execute_sql(
                "UPDATE batch_task_item SET last_retry_at = ?, next_retry_at = 0 WHERE group_id = ?",
                (int(time.time()) - 1800, gid1),
            )

        fake_115 = MagicMock()
        fake_115.get_quota_info.return_value = {"count": 1000, "used": 0}
        # Fast lookup returns [] (not in pages 1-3), deep lookup finds it completed on page 12
        fake_115.get_offline_tasks_for_links.return_value = []
        fake_115.find_offline_tasks_by_urls.return_value = {
            ed2k_sub: {"url": ed2k_cloud, "name": "Deep File.mkv", "status": 2, "percentDone": 100, "info_hash": "ih_deep_done"}
        }
        init.openapi_115 = fake_115

        with patch("app.core.batch_task_retry.time.sleep"), \
             patch("app.core.batch_task_retry.add_task_to_queue") as mock_q1:
            btr.execute_batch_retry()

        with SqlLiteLib() as sqlite:
            st1 = sqlite.query_one("SELECT status FROM batch_task_item WHERE group_id = ?", (gid1,))
        assert st1 == "success"
        fake_115.del_faild_offline_task.assert_called_once_with("ih_deep_done", delete_source_file=False)
        fake_115.offline_download_specify_path.assert_not_called()
        assert mock_q1.call_count == 1

        # Case B: Fresh retry item (last_retry_at=NULL), first submit fails (e.g. 10008), deep recovery deletes stale failed duplicate & resubmits
        m_stale = "magnet:?xt=urn:btih:9999999999999999999999999999999999999999"
        gid2 = btm.create_single_retry_group("", m_stale, "/dl", chat_id=100, origin_message_id=702)
        with SqlLiteLib() as sqlite:
            sqlite.execute_sql("UPDATE batch_task_item SET next_retry_at = 0 WHERE group_id = ?", (gid2,))

        fake_115_b = MagicMock()
        fake_115_b.get_quota_info.return_value = {"count": 1000, "used": 0}
        fake_115_b.get_offline_tasks_for_links.return_value = []
        # First submit raises 10008, second submit after deleting stale duplicate succeeds
        fake_115_b.offline_download_specify_path.side_effect = [NonRetryableAPIError("任务已存在", code=10008), True]
        fake_115_b.find_offline_tasks_by_urls.side_effect = [
            {m_stale: {"url": m_stale, "name": "Stale.mp4", "status": -1, "percentDone": 0, "info_hash": "ih_stale"}},
            {},  # quick probe deep fallback
        ]
        init.openapi_115 = fake_115_b

        with patch("app.core.batch_task_retry.time.sleep"), \
             patch("app.core.batch_task_retry.add_task_to_queue"):
            btr.execute_batch_retry()

        fake_115_b.del_faild_offline_task.assert_called_once_with("ih_stale", delete_source_file=True)
        assert fake_115_b.offline_download_specify_path.call_count == 2
        with SqlLiteLib() as sqlite:
            row2 = sqlite.query_all("SELECT retry_count, last_retry_at FROM batch_task_item WHERE group_id = ?", (gid2,))[0]
        assert row2["retry_count"] == 1
        assert row2["last_retry_at"] is not None
    finally:
        init.DB_FILE = old_db
        if os.path.exists(test_db):
            os.remove(test_db)


def test_generate_magnet_txt_file_kwargs_and_temp_cleanup():
    init.logger = _DummyLogger()
    os.makedirs(os.path.join(ROOT_DIR, "tmp"), exist_ok=True)
    test_db = os.path.join(ROOT_DIR, "tmp", "test_runtime_bugfixes_txt.db")
    custom_temp = os.path.join(ROOT_DIR, "tmp", "_nonexistent_temp_dir_for_txt_test")
    if os.path.exists(test_db):
        os.remove(test_db)
    if os.path.exists(custom_temp):
        shutil.rmtree(custom_temp)

    old_db = init.DB_FILE
    old_temp = init.TEMP
    try:
        init.DB_FILE = test_db
        init.TEMP = custom_temp
        btm.initialize_batch_task_tables(reset_sql=False)

        gid = btm.create_batch_task_group("MyBatchFolder", "/dl", 2, chat_id=100)
        btm.save_batch_task_items(gid, [
            {"link": "magnet:?xt=urn:btih:1111111111111111111111111111111111111111"},
            {"link": "ed2k://|file|Test.mp4|100|0123456789abcdef0123456789abcdef|/"},
        ])

        captured = {}

        def _fake_upload_ok(**kwargs):
            # Must ONLY be called with keyword arguments, and file must exist at call time with matching size & sha1
            assert os.path.exists(kwargs["file_path"])
            with open(kwargs["file_path"], "rb") as f:
                raw = f.read()
            assert kwargs["file_size"] == len(raw)
            assert kwargs["fileid"] == hashlib.sha1(raw).hexdigest()
            captured.update(kwargs)
            return True, False

        fake_115 = MagicMock()
        fake_115.upload_file.side_effect = _fake_upload_ok
        init.openapi_115 = fake_115

        # 1. Success tuple (True, False) -> returns True, creates missing init.TEMP, cleans temp file
        ok = btm.generate_magnet_txt_file(gid, "/dl/MyBatchFolder", "MyBatchFolder")
        assert ok is True
        assert os.path.isdir(custom_temp)
        assert captured["target"] == "/dl/MyBatchFolder"
        assert captured["file_name"] == "MyBatchFolder.txt"
        assert captured["request_times"] == 1
        assert not os.path.exists(captured["file_path"])

        # 2. Failure tuple (False, False) must NOT be treated as truthy! And temp file is cleaned up
        captured_fail = {}

        def _fake_upload_false(**kwargs):
            captured_fail.update(kwargs)
            return False, False

        fake_115.upload_file.side_effect = _fake_upload_false
        ok_false = btm.generate_magnet_txt_file(gid, "/dl/MyBatchFolder", "MyBatchFolder")
        assert ok_false is False
        assert not os.path.exists(captured_fail["file_path"])

        # 3. Exception during upload_file -> returns False AND still cleans temp file in finally
        captured_err = {}

        def _fake_upload_boom(**kwargs):
            captured_err.update(kwargs)
            raise RuntimeError("OSS timeout")

        fake_115.upload_file.side_effect = _fake_upload_boom
        ok_err = btm.generate_magnet_txt_file(gid, "/dl/MyBatchFolder", "MyBatchFolder")
        assert ok_err is False
        assert not os.path.exists(captured_err["file_path"])
    finally:
        init.DB_FILE = old_db
        init.TEMP = old_temp
        if os.path.exists(test_db):
            os.remove(test_db)
        if os.path.exists(custom_temp):
            shutil.rmtree(custom_temp)


def test_download_handler_canonical_matching_ed2k_and_magnet():
    from app.utils import task_tracker

    task_tracker.clear_tasks()
    init.logger = _DummyLogger()
    init.bot_config = {"create_strm": False, "emby_server": "", "api_key": ""}

    ed2k_submitted = "ed2k://|file|Video%20Clip%2001.mp4|2048|ABCDEF1234567890ABCDEF1234567890|/"
    ed2k_from_115 = "ed2k://|file|Video Clip 01.mp4|2048|abcdef1234567890abcdef1234567890|"
    mag_submitted = "MAGNET:?xt=urn:btih:FEDCBA9876543210FEDCBA9876543210FEDCBA98&dn=Clip%2002"
    mag_from_115 = "magnet:?xt=urn:btih:fedcba9876543210fedcba9876543210fedcba98"

    fake_115 = MagicMock()
    fake_115.offline_download_specify_path_batch.return_value = True
    fake_115.get_file_info.return_value = {"file_id": "cid_dl"}
    fake_115.get_file_list.return_value = []
    fake_115.clear_cloud_task.return_value = True
    fake_115.get_offline_tasks_for_links.return_value = [
        {"url": ed2k_from_115, "name": "Video Clip 01.mp4", "status": 2, "percentDone": 100, "info_hash": "ih_e1"},
        {"url": mag_from_115, "name": "Clip 02.mkv", "status": 2, "percentDone": 100, "info_hash": "ih_m2"},
    ]
    init.openapi_115 = fake_115

    links = [{"link": ed2k_submitted}, {"link": mag_submitted}]
    with patch("app.handlers.download_handler.time.sleep"), \
         patch("app.core.batch_task_manager.create_batch_task_group", return_value="grp_canon"), \
         patch("app.core.batch_task_manager.save_batch_task_items"), \
         patch("app.core.batch_task_manager.update_task_item_status") as mock_item_status, \
         patch("app.core.batch_task_manager.update_task_group_counts"), \
         patch("app.core.batch_task_manager.generate_magnet_txt_file", return_value=True), \
         patch("app.utils.message_queue.add_task_to_queue") as mock_queue:
        _download_tasks_batch_impl(
            links, "/dl", 100, custom_folder_name="F", tracker_id="dl_canon", origin_message_id=888
        )

    statuses = {c.args[0]: c.args[2] for c in mock_item_status.call_args_list}
    assert statuses == {ed2k_submitted: "success", mag_submitted: "success"}
    # No concurrent active downloads -> uses single clear_cloud_task(4) preserving source files
    fake_115.clear_cloud_task.assert_called_once_with(4)
    fake_115.del_faild_offline_task.assert_not_called()
    assert mock_queue.call_count == 1
    assert "2/2" in mock_queue.call_args.kwargs["message"]

    # With another active download task running -> falls back to individual canonical cleanup preserving source files
    fake_115.reset_mock()
    task_tracker.register_task("dl_other", task_type="download", title="other", total=1)
    try:
        with patch("app.handlers.download_handler.time.sleep"), \
             patch("app.core.batch_task_manager.create_batch_task_group", return_value="grp_canon2"), \
             patch("app.core.batch_task_manager.save_batch_task_items"), \
             patch("app.core.batch_task_manager.update_task_item_status"), \
             patch("app.core.batch_task_manager.update_task_group_counts"), \
             patch("app.core.batch_task_manager.generate_magnet_txt_file", return_value=True), \
             patch("app.utils.message_queue.add_task_to_queue"):
            _download_tasks_batch_impl(
                links, "/dl", 100, custom_folder_name="F2", tracker_id="dl_canon2", origin_message_id=889
            )
        fake_115.clear_cloud_task.assert_not_called()
        del_calls = {c.args[0]: c.kwargs["delete_source_file"] for c in fake_115.del_faild_offline_task.call_args_list}
        assert del_calls == {"ih_e1": False, "ih_m2": False}
    finally:
        task_tracker.clear_tasks()


if __name__ == "__main__":
    test_canonical_offline_key_and_index_matching()
    test_10008_single_url_without_info_hash()
    test_10008_batch_without_info_hash()
    test_batch_task_retry_handles_deep_page_duplicates_and_canonical_ed2k()
    test_generate_magnet_txt_file_kwargs_and_temp_cleanup()
    test_download_handler_canonical_matching_ed2k_and_magnet()
    print("All runtime bugfix tests passed!")
