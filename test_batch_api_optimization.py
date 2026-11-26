# -*- coding: utf-8 -*-
"""Unit tests for the 4 batch API post-processing & speed optimizations:
1. Skip batch_process_downloads when create_strm is False (keep intact when True).
2. Flat recursive find_all_junk_files pagination fix + batch_auto_clean fast path (is_dedicated_folder=True)
   vs safe scoped path (is_dedicated_folder=False).
3. Batch cloud completed-record cleanup via clear_cloud_task(4) with concurrent-task safety +
   skipping redundant pre/post directory scans.
4. Enqueue quoted completion notification immediately before slow post-processing, and run
   generate_magnet_txt_file AFTER batch_auto_clean.
"""

import os
import sys
import threading
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(ROOT_DIR, "app")
for p in (ROOT_DIR, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import init
from app.core.open_115 import OpenAPI_115
from app.handlers.download_handler import _download_tasks_batch_impl
from app.utils import task_tracker


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


def test_skip_batch_process_downloads_when_create_strm_disabled():
    task_tracker.clear_tasks()
    init.logger = _DummyLogger()
    init.bot_config = {"create_strm": False, "emby_server": "", "api_key": ""}

    links = [{"link": f"magnet:?xt=urn:btih:{i:040x}"} for i in range(1, 6)]
    cloud_tasks = [
        {"url": item["link"], "name": f"Folder_{i}", "status": 2, "percentDone": 100, "info_hash": f"ih_{i}"}
        for i, item in enumerate(links, 1)
    ]

    fake_115 = MagicMock()
    fake_115.offline_download_specify_path_batch.return_value = True
    fake_115.get_offline_tasks_for_links.return_value = cloud_tasks
    fake_115.clear_cloud_task.return_value = True
    init.openapi_115 = fake_115

    with patch("app.handlers.download_handler.time.sleep"), \
         patch("app.handlers.download_handler.create_strm_file") as mock_strm, \
         patch("app.handlers.download_handler.notice_emby_scan_library") as mock_emby, \
         patch("app.core.batch_task_manager.create_batch_task_group", return_value="grp_nostrm"), \
         patch("app.core.batch_task_manager.save_batch_task_items"), \
         patch("app.core.batch_task_manager.update_task_item_status"), \
         patch("app.core.batch_task_manager.update_task_group_counts"), \
         patch("app.core.batch_task_manager.generate_magnet_txt_file", return_value=True), \
         patch("app.utils.message_queue.add_task_to_queue") as mock_queue:
        _download_tasks_batch_impl(
            links, "/dl", 100, custom_folder_name="NoStrmBatch", tracker_id="dl_nostrm", origin_message_id=101
        )

    # create_strm=False -> 0 calls to batch_process_downloads & create_strm_file
    fake_115.batch_process_downloads.assert_not_called()
    mock_strm.assert_not_called()
    mock_emby.assert_called_once()
    assert mock_queue.call_count == 1
    assert "5/5" in mock_queue.call_args.kwargs["message"]

    # create_strm=True -> batch_process_downloads & create_strm_file ARE called as expected
    init.bot_config = {"create_strm": True, "emby_server": "", "api_key": ""}
    fake_115.reset_mock()
    fake_115.offline_download_specify_path_batch.return_value = True
    fake_115.get_offline_tasks_for_links.return_value = cloud_tasks[:2]
    fake_115.batch_process_downloads.return_value = {
        "Folder_1": {"path": "/dl/WithStrm/Folder_1", "files": ["a.mp4"]},
        "Folder_2": {"path": "/dl/WithStrm/Folder_2", "files": ["b.mkv"]},
    }
    fake_115.clear_cloud_task.return_value = True

    with patch("app.handlers.download_handler.time.sleep"), \
         patch("app.handlers.download_handler.create_strm_file") as mock_strm2, \
         patch("app.handlers.download_handler.notice_emby_scan_library"), \
         patch("app.core.batch_task_manager.create_batch_task_group", return_value="grp_strm"), \
         patch("app.core.batch_task_manager.save_batch_task_items"), \
         patch("app.core.batch_task_manager.update_task_item_status"), \
         patch("app.core.batch_task_manager.update_task_group_counts"), \
         patch("app.core.batch_task_manager.generate_magnet_txt_file", return_value=True), \
         patch("app.utils.message_queue.add_task_to_queue"):
        _download_tasks_batch_impl(
            links[:2], "/dl", 100, custom_folder_name="WithStrm", tracker_id="dl_strm", origin_message_id=102
        )

    fake_115.batch_process_downloads.assert_called_once_with("/dl/WithStrm", ["Folder_1", "Folder_2"])
    assert mock_strm2.call_count == 2


def test_find_all_junk_files_pagination_and_fast_dedicated_batch_auto_clean():
    # 1. find_all_junk_files stops immediately when len(current_files) < limit (no extra API call, no sleep!)
    client = _make_openapi_client()
    client.get_file_list = MagicMock(
        return_value=[
            {"fid": "j1", "fn": "poster.jpg", "fs": 50 * 1024, "fc": "1"},
            {"fid": "j2", "fn": "readme.url", "fs": 1024, "fc": "1"},
        ]
    )
    with patch("app.core.open_115.time.sleep") as mock_sleep:
        res = client.find_all_junk_files("cid_root", 0, 10 * 1024 * 1024, limit=1150)
    assert [f["fid"] for f in res] == ["j1", "j2"]
    assert client.get_file_list.call_count == 1
    mock_sleep.assert_not_called()

    # 2. find_all_junk_files paginates with sleep(1) ONLY when len(current_files) >= limit and last_file_size < byte_size
    client2 = _make_openapi_client()
    page1 = [{"fid": f"p1_{i}", "fn": f"a{i}.jpg", "fs": 100, "fc": "1"} for i in range(3)]
    page2 = [
        {"fid": "p2_0", "fn": "small.nfo", "fs": 200, "fc": "1"},
        {"fid": "p2_1", "fn": "movie.mp4", "fs": 999999999, "fc": "1"},
    ]
    client2.get_file_list = MagicMock(side_effect=[page1, page2])
    with patch("app.core.open_115.time.sleep") as mock_sleep2:
        res2 = client2.find_all_junk_files("cid_root", 0, 1024 * 1024, limit=3)
    assert [f["fid"] for f in res2] == ["p1_0", "p1_1", "p1_2", "p2_0"]
    assert client2.get_file_list.call_count == 2
    assert client2.get_file_list.call_args_list[1].args[0]["offset"] == 3
    assert client2.get_file_list.call_args_list[1].args[0]["limit"] == 3
    mock_sleep2.assert_called_once_with(1)

    # 3. batch_auto_clean(is_dedicated_folder=True) uses flat find_all_junk_files + _batch_delete_files,
    #    does NOT loop child folders, and respects case-insensitive exclude_filenames
    init.bot_config = {"clean_policy": {"switch": "on", "less_than": "50m"}}
    client3 = _make_openapi_client()
    client3.get_file_info = MagicMock(return_value={"file_id": "cid_batch"})
    client3.get_file_list = MagicMock(
        return_value=[
            {"fid": "f_junk1", "fn": "ad.jpg", "fs": 1024, "fc": "1"},
            {"fid": "f_backup", "fn": "MyBatch.TXT", "fs": 512, "fc": "1"},
            {"fid": "f_junk2", "fn": "sample.nfo", "fs": 2048, "fc": "1"},
            {"fid": "f_video", "fn": "video.mp4", "fs": 800 * 1024 * 1024, "fc": "1"},
        ]
    )
    client3._batch_delete_files = MagicMock()

    with patch("app.core.open_115.time.sleep") as mock_sleep3:
        client3.batch_auto_clean(
            "/dl/MyBatch",
            [f"Sub_{i}" for i in range(100)],
            is_dedicated_folder=True,
            exclude_filenames=["mybatch.txt"],
        )

    client3.get_file_info.assert_called_once_with("/dl/MyBatch")
    # Flat recursive query ran ONCE (show_dir=0, limit=1150) — did NOT loop 100 child folders!
    assert client3.get_file_list.call_count == 1
    query_params = client3.get_file_list.call_args.args[0]
    assert query_params["cid"] == "cid_batch"
    assert query_params["show_dir"] == 0
    assert query_params["limit"] == 1150
    mock_sleep3.assert_not_called()
    # Excluded MyBatch.TXT (case-insensitive) and big video.mp4; deleted only f_junk1 & f_junk2
    client3._batch_delete_files.assert_called_once_with(["f_junk1", "f_junk2"], batch_size=100)

    # 4. Missing clean_policy in bot_config never raises KeyError
    init.bot_config = {}
    client4 = _make_openapi_client()
    client4.get_file_info = MagicMock()
    client4.batch_auto_clean("/dl/MyBatch", ["Sub_1"], is_dedicated_folder=True)
    client4.get_file_info.assert_not_called()


def test_batch_auto_clean_safe_scoped_mode_for_shared_directory():
    init.bot_config = {"clean_policy": {"switch": "on", "less_than": "10M"}}
    client = _make_openapi_client()
    client.get_file_info = MagicMock(return_value={"file_id": "cid_shared"})
    client.find_all_junk_files = MagicMock()
    client._batch_delete_files = MagicMock()

    def _fake_list(params):
        if params["cid"] == "cid_shared":
            return [
                {"fid": "dir_target", "fn": "TargetMovie", "fc": "0"},
                {"fid": "dir_other", "fn": "OtherUserFolder", "fc": "0"},
                {"fid": "top_small", "fn": "shared_readme.txt", "fc": "1", "fs": 100},
            ]
        if params["cid"] == "dir_target":
            return [
                {"fid": "t_junk", "fn": "promo.jpg", "fc": "1", "fs": 2048},
                {"fid": "t_video", "fn": "movie.mkv", "fc": "1", "fs": 500 * 1024 * 1024},
            ]
        raise AssertionError(f"Should never inspect cid={params['cid']}")

    client.get_file_list = MagicMock(side_effect=_fake_list)

    client.batch_auto_clean("/dl/SharedMovies", ["TargetMovie"], is_dedicated_folder=False)

    # MUST NOT call find_all_junk_files on shared parent directory!
    client.find_all_junk_files.assert_not_called()
    assert client.get_file_list.call_count == 2
    # Only deletes junk inside TargetMovie, never OtherUserFolder or shared_readme.txt
    client._batch_delete_files.assert_called_once_with(["t_junk"], batch_size=100)


def test_clear_cloud_task_4_and_concurrent_download_safety():
    init.logger = _DummyLogger()
    init.bot_config = {"create_strm": False, "emby_server": "", "api_key": ""}

    links = [{"link": f"magnet:?xt=urn:btih:{i:040x}"} for i in range(1, 11)]
    cloud_tasks = [
        {"url": item["link"], "name": f"Vid_{i}.mp4", "status": 2, "percentDone": 100, "info_hash": f"ih_{i}"}
        for i, item in enumerate(links, 1)
    ]

    # Case A: All-completed 10-link batch with NO other active download task (only self registered + an upload task)
    # -> calls clear_cloud_task(4) ONCE and does NOT call del_faild_offline_task 10 times!
    task_tracker.clear_tasks()
    task_tracker.register_task("dl_self", task_type="download", title="self", total=10)
    task_tracker.register_task("up_other", task_type="upload", title="upload", total=1)
    fake_115 = MagicMock()
    fake_115.offline_download_specify_path_batch.return_value = True
    fake_115.get_offline_tasks_for_links.return_value = cloud_tasks
    fake_115.clear_cloud_task.return_value = True
    init.openapi_115 = fake_115

    try:
        with patch("app.handlers.download_handler.time.sleep"), \
             patch("app.core.batch_task_manager.create_batch_task_group", return_value="grp_bulk"), \
             patch("app.core.batch_task_manager.save_batch_task_items"), \
             patch("app.core.batch_task_manager.update_task_item_status"), \
             patch("app.core.batch_task_manager.update_task_group_counts"), \
             patch("app.core.batch_task_manager.generate_magnet_txt_file", return_value=True), \
             patch("app.utils.message_queue.add_task_to_queue"):
            _download_tasks_batch_impl(
                links, "/dl", 100, custom_folder_name="Bulk10", tracker_id="dl_self", origin_message_id=201
            )

        fake_115.clear_cloud_task.assert_called_once_with(4)
        fake_115.del_faild_offline_task.assert_not_called()

        # Case B: Another active download task IS running concurrently -> MUST NOT call clear_cloud_task(4)!
        # With 10 (>3) links, also does NOT loop del_faild_offline_task 10 times (leaves completed records cleanly).
        task_tracker.register_task("dl_concurrent", task_type="download", title="other", total=5)
        fake_115.reset_mock()
        fake_115.offline_download_specify_path_batch.return_value = True
        fake_115.get_offline_tasks_for_links.return_value = cloud_tasks

        with patch("app.handlers.download_handler.time.sleep"), \
             patch("app.core.batch_task_manager.create_batch_task_group", return_value="grp_conc"), \
             patch("app.core.batch_task_manager.save_batch_task_items"), \
             patch("app.core.batch_task_manager.update_task_item_status"), \
             patch("app.core.batch_task_manager.update_task_group_counts"), \
             patch("app.core.batch_task_manager.generate_magnet_txt_file", return_value=True), \
             patch("app.utils.message_queue.add_task_to_queue"):
            _download_tasks_batch_impl(
                links, "/dl", 100, custom_folder_name="Conc10", tracker_id="dl_self", origin_message_id=202
            )

        fake_115.clear_cloud_task.assert_not_called()
        fake_115.del_faild_offline_task.assert_not_called()

        # Case C: Mixed batch (8 success + 2 failed, all_completed=True, no other active download)
        # -> calls clear_cloud_task(4) ONCE for completed AND deletes ONLY the 2 failed tasks with delete_source_file=True
        task_tracker.clear_tasks()
        task_tracker.register_task("dl_mixed", task_type="download", title="mixed", total=10)
        mixed_cloud = list(cloud_tasks[:8]) + [
            {"url": links[8]["link"], "name": "Bad9", "status": -1, "percentDone": 0, "info_hash": "ih_9"},
            {"url": links[9]["link"], "name": "Bad10", "status": -1, "percentDone": 0, "info_hash": "ih_10"},
        ]
        fake_115.reset_mock()
        fake_115.offline_download_specify_path_batch.return_value = True
        fake_115.get_offline_tasks_for_links.return_value = mixed_cloud
        fake_115.clear_cloud_task.return_value = True

        with patch("app.handlers.download_handler.time.sleep"), \
             patch("app.core.batch_task_manager.create_batch_task_group", return_value="grp_mixed"), \
             patch("app.core.batch_task_manager.save_batch_task_items"), \
             patch("app.core.batch_task_manager.update_task_item_status"), \
             patch("app.core.batch_task_manager.update_task_group_counts"), \
             patch("app.core.batch_task_manager.generate_magnet_txt_file", return_value=True), \
             patch("app.utils.message_queue.add_task_to_queue"):
            _download_tasks_batch_impl(
                links, "/dl", 100, custom_folder_name="Mixed10", tracker_id="dl_mixed", origin_message_id=203
            )

        fake_115.clear_cloud_task.assert_called_once_with(4)
        del_calls = {c.args[0]: c.kwargs["delete_source_file"] for c in fake_115.del_faild_offline_task.call_args_list}
        assert del_calls == {"ih_9": True, "ih_10": True}

        # Case D: Single-link success -> deletes only that 1 record with delete_source_file=False (does NOT call clear_cloud_task)
        task_tracker.clear_tasks()
        fake_115.reset_mock()
        fake_115.offline_download_specify_path_batch.return_value = True
        fake_115.get_file_info.return_value = {"file_id": "cid_shared"}
        fake_115.get_file_list.return_value = []
        fake_115.get_offline_tasks_for_links.return_value = [cloud_tasks[0]]

        with patch("app.handlers.download_handler.time.sleep"), \
             patch("app.core.batch_task_manager.create_batch_task_group", return_value="grp_one"), \
             patch("app.core.batch_task_manager.save_batch_task_items"), \
             patch("app.core.batch_task_manager.update_task_item_status"), \
             patch("app.core.batch_task_manager.update_task_group_counts"), \
             patch("app.core.batch_task_manager.generate_magnet_txt_file", return_value=True) as mock_txt, \
             patch("app.utils.message_queue.add_task_to_queue"):
            _download_tasks_batch_impl(
                links[:1], "/dl", 100, custom_folder_name="", tracker_id="dl_one", origin_message_id=204
            )

        fake_115.clear_cloud_task.assert_not_called()
        fake_115.del_faild_offline_task.assert_called_once_with("ih_1", delete_source_file=False)
        mock_txt.assert_not_called()
    finally:
        task_tracker.clear_tasks()


def test_notification_before_postprocessing_and_txt_after_clean():
    task_tracker.clear_tasks()
    init.logger = _DummyLogger()
    init.bot_config = {"create_strm": False, "emby_server": "", "api_key": ""}

    events = []
    links = [
        {"link": "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        {"link": "magnet:?xt=urn:btih:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
    ]
    cloud_tasks = [
        {"url": links[0]["link"], "name": "A", "status": 2, "percentDone": 100, "info_hash": "ha"},
        {"url": links[1]["link"], "name": "B", "status": 2, "percentDone": 100, "info_hash": "hb"},
    ]

    fake_115 = MagicMock()
    fake_115.offline_download_specify_path_batch.return_value = True
    fake_115.get_offline_tasks_for_links.return_value = cloud_tasks
    fake_115.batch_auto_clean.side_effect = lambda *a, **kw: events.append(("batch_auto_clean", kw.get("is_dedicated_folder")))
    fake_115.clear_cloud_task.side_effect = lambda flag: (events.append(("clear_cloud_task", flag)) or True)
    init.openapi_115 = fake_115

    with patch("app.handlers.download_handler.time.sleep"), \
         patch("app.handlers.download_handler.notice_emby_scan_library", side_effect=lambda: events.append(("emby", None))), \
         patch("app.core.batch_task_manager.create_batch_task_group", return_value="grp_order"), \
         patch("app.core.batch_task_manager.save_batch_task_items"), \
         patch("app.core.batch_task_manager.update_task_item_status"), \
         patch("app.core.batch_task_manager.update_task_group_counts", side_effect=lambda gid: events.append(("update_counts", gid))), \
         patch("app.core.batch_task_manager.generate_magnet_txt_file", side_effect=lambda *a, **kw: (events.append(("generate_txt", a[2])) or True)), \
         patch("app.utils.message_queue.add_task_to_queue", side_effect=lambda *a, **kw: events.append(("notify", kw.get("reply_to_message_id")))):
        _download_tasks_batch_impl(
            links, "/dl", 100, custom_folder_name="OrderFolder", tracker_id="dl_order", origin_message_id=301
        )

    names = [e[0] for e in events]
    assert names == [
        "update_counts",
        "notify",
        "batch_auto_clean",
        "clear_cloud_task",
        "generate_txt",
        "emby",
    ], f"Unexpected execution order: {events}"
    # Verify dedicated folder flag & reply_to_message_id & flag=4
    assert events[1] == ("notify", 301)
    assert events[2] == ("batch_auto_clean", True)
    assert events[3] == ("clear_cloud_task", 4)
    assert events[4] == ("generate_txt", "OrderFolder")


def test_skip_pre_and_post_dir_scans_for_dedicated_folder():
    task_tracker.clear_tasks()
    init.logger = _DummyLogger()
    init.bot_config = {"create_strm": False, "emby_server": "", "api_key": ""}

    links = [
        {"link": "magnet:?xt=urn:btih:1111111111111111111111111111111111111111"},
        {"link": "magnet:?xt=urn:btih:2222222222222222222222222222222222222222"},
    ]
    cloud_tasks = [
        {"url": links[0]["link"], "name": "One.mp4", "status": 2, "percentDone": 100, "info_hash": "h1"},
        {"url": links[1]["link"], "name": "Two.mp4", "status": 2, "percentDone": 100, "info_hash": "h2"},
    ]

    # Case A: Newly-created dedicated batch folder + all links found in cloud list -> 0 get_file_info & 0 get_file_list calls!
    fake_115 = MagicMock()
    fake_115.offline_download_specify_path_batch.return_value = True
    fake_115.get_offline_tasks_for_links.return_value = cloud_tasks
    fake_115.clear_cloud_task.return_value = True
    init.openapi_115 = fake_115

    with patch("app.handlers.download_handler.time.sleep"), \
         patch("app.core.batch_task_manager.create_batch_task_group", return_value="grp_noscan"), \
         patch("app.core.batch_task_manager.save_batch_task_items"), \
         patch("app.core.batch_task_manager.update_task_item_status"), \
         patch("app.core.batch_task_manager.update_task_group_counts"), \
         patch("app.core.batch_task_manager.generate_magnet_txt_file", return_value=True), \
         patch("app.utils.message_queue.add_task_to_queue"):
        _download_tasks_batch_impl(
            links, "/dl", 100, custom_folder_name="FreshBatch", tracker_id="dl_noscan", origin_message_id=401
        )

    fake_115.get_file_info.assert_not_called()
    fake_115.get_file_list.assert_not_called()

    # Case B: One link missing from offline_task_status -> post-download directory scan IS executed as fallback!
    fake_115_b = MagicMock()
    fake_115_b.offline_download_specify_path_batch.return_value = True
    fake_115_b.get_offline_tasks_for_links.return_value = [cloud_tasks[0]]  # link[1] absent from cloud list
    fake_115_b.get_file_info.return_value = {"file_id": "cid_fresh"}
    fake_115_b.get_file_list.return_value = [{"fn": "FallbackTwo.mp4", "fc": "1", "fs": 1000}]
    fake_115_b.clear_cloud_task.return_value = True
    init.openapi_115 = fake_115_b

    with patch("app.handlers.download_handler.time.sleep"), \
         patch("app.core.batch_task_manager.create_batch_task_group", return_value="grp_fallback"), \
         patch("app.core.batch_task_manager.save_batch_task_items"), \
         patch("app.core.batch_task_manager.update_task_item_status") as mock_st, \
         patch("app.core.batch_task_manager.update_task_group_counts"), \
         patch("app.core.batch_task_manager.generate_magnet_txt_file", return_value=True), \
         patch("app.utils.message_queue.add_task_to_queue"):
        _download_tasks_batch_impl(
            links, "/dl", 100, custom_folder_name="FallbackBatch", tracker_id="dl_fb", origin_message_id=402
        )

    # Pre-download scan was skipped, post-download scan ran ONCE (1 get_file_info + 1 get_file_list)
    fake_115_b.get_file_info.assert_called_once_with("/dl/FallbackBatch")
    assert fake_115_b.get_file_list.call_count == 1
    statuses = {c.args[0]: (c.args[2], c.args[3]) for c in mock_st.call_args_list}
    assert statuses[links[1]["link"]] == ("success", "FallbackTwo.mp4")

    # Case C: Shared folder (single link, is_dedicated_folder=False) -> pre-download scan runs (1 call),
    #         post-download scan is skipped because link was found in cloud list!
    fake_115_c = MagicMock()
    fake_115_c.offline_download_specify_path_batch.return_value = True
    fake_115_c.get_offline_tasks_for_links.return_value = [cloud_tasks[0]]
    fake_115_c.get_file_info.return_value = {"file_id": "cid_shared"}
    fake_115_c.get_file_list.return_value = [{"fn": "existing.mp4", "fc": "1"}]
    init.openapi_115 = fake_115_c

    with patch("app.handlers.download_handler.time.sleep"), \
         patch("app.core.batch_task_manager.create_batch_task_group", return_value="grp_shared"), \
         patch("app.core.batch_task_manager.save_batch_task_items"), \
         patch("app.core.batch_task_manager.update_task_item_status"), \
         patch("app.core.batch_task_manager.update_task_group_counts"), \
         patch("app.utils.message_queue.add_task_to_queue"):
        _download_tasks_batch_impl(
            links[:1], "/dl/Shared", 100, custom_folder_name="", tracker_id="dl_sh", origin_message_id=403
        )

    fake_115_c.get_file_info.assert_called_once_with("/dl/Shared")
    assert fake_115_c.get_file_list.call_count == 1
    # batch_auto_clean received is_dedicated_folder=False for shared directory
    assert fake_115_c.batch_auto_clean.call_args.kwargs["is_dedicated_folder"] is False


if __name__ == "__main__":
    test_skip_batch_process_downloads_when_create_strm_disabled()
    test_find_all_junk_files_pagination_and_fast_dedicated_batch_auto_clean()
    test_batch_auto_clean_safe_scoped_mode_for_shared_directory()
    test_clear_cloud_task_4_and_concurrent_download_safety()
    test_notification_before_postprocessing_and_txt_after_clean()
    test_skip_pre_and_post_dir_scans_for_dedicated_folder()
    print("All batch API optimization tests passed!")
