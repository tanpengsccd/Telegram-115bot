# -*- coding: utf-8 -*-
"""Unit tests for ETA-aware adaptive polling, targeted 115 paging/cache, notification deduplication, and quoted retry summaries."""

import os
import sys
import sqlite3
import threading
import time
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(ROOT_DIR, "app")
for p in (ROOT_DIR, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import init
from app.core.open_115 import OpenAPI_115
from app.utils.poll_scheduler import (
    AdaptivePollController,
    DEFAULT_STALL_TIMEOUT_SECONDS,
    MIN_POLL_INTERVAL,
    STALL_BACKOFF_SCHEDULE,
)
from app.handlers.download_handler import _download_tasks_batch_impl
from app.core import batch_task_manager as btm
from app.core import batch_task_retry as btr


class _DummyLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warn(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg, **kwargs): pass


def test_adaptive_poll_controller_eta_and_stall_early_stop():
    assert DEFAULT_STALL_TIMEOUT_SECONDS == 600.0
    ctrl = AdaptivePollController(stall_timeout=600.0, start_time=1000.0)

    # Round 1: initial observation at 10% (progress > 0) -> 15s wait
    wait, stop, _ = ctrl.next_wait({"m1": 10}, now=1003.0)
    assert stop is False
    assert wait == 15.0

    # Round 2: steady progress 10% -> 30% in 20s (rate=1%/s, ETA=70s -> 70*0.4 = 28s)
    wait, stop, _ = ctrl.next_wait({"m1": 30}, now=1023.0)
    assert stop is False
    assert 25.0 <= wait <= 30.0

    # Round 3: near-completion sprint 30% -> 95% in 20s (rate=3.25%/s, ETA=1.5s -> clamped to MIN_POLL_INTERVAL=5s)
    wait, stop, _ = ctrl.next_wait({"m1": 95}, now=1043.0)
    assert stop is False
    assert wait == MIN_POLL_INTERVAL

    # Round 4+: stalled at 95% -> exponential backoff 15 -> 30 -> 60 -> 120 -> 180 -> early stop at 600s
    t = 1043.0
    waits = []
    stopped = False
    for _ in range(10):
        t += (waits[-1] if waits else 5.0)
        w, stop, reason = ctrl.next_wait({"m1": 95}, now=t)
        if stop:
            stopped = True
            assert "提前结束等待" in reason
            break
        waits.append(w)
    assert stopped is True
    assert waits[:4] == list(STALL_BACKOFF_SCHEDULE[:4])
    assert (t - 1043.0) >= 600.0

    # Dropping a completed task resets stall timer even if remaining task is at 0%
    ctrl2 = AdaptivePollController(stall_timeout=600.0, start_time=2000.0)
    ctrl2.next_wait({"m1": 50, "m2": 0}, now=2003.0)
    w, stop, _ = ctrl2.next_wait({"m2": 0}, now=2500.0)
    assert stop is False
    assert w == 15.0
    assert ctrl2.stall_rounds == 0


def test_targeted_offline_paging_and_cache():
    init.logger = _DummyLogger()
    client = OpenAPI_115.__new__(OpenAPI_115)
    client._offline_page1_cache_lock = threading.Lock()
    client._offline_page1_cache_ts = 0.0
    client._offline_page1_cache_data = None
    client._offline_page1_cache_ttl = 3.0

    pages = {
        1: {
            "page_count": 30,
            "tasks": [
                {"url": "magnet:?xt=urn:btih:111", "name": "A", "status": 2, "percentDone": 100, "info_hash": "h1"},
                {"url": "magnet:?xt=urn:btih:222", "name": "B", "status": 1, "percentDone": 50, "info_hash": "h2"},
            ],
        },
        2: {
            "page_count": 30,
            "tasks": [
                {"url": "magnet:?xt=urn:btih:333", "name": "C", "status": 2, "percentDone": 100, "info_hash": "h3"},
            ],
        },
        3: {"page_count": 30, "tasks": []},
    }
    client.get_offline_tasks_by_page = MagicMock(side_effect=lambda page=1: pages.get(page, {"page_count": 30, "tasks": []}))

    # 1. Targets all on page 1 -> ONLY 1 API request (does NOT crawl 30 pages!)
    res = client.get_offline_tasks_for_links(["magnet:?xt=urn:btih:111", "magnet:?xt=urn:btih:222"], use_cache=True)
    assert len(res) == 2
    assert client.get_offline_tasks_by_page.call_count == 1

    # 2. Concurrent call within 3s hits cache -> STILL only 1 API request!
    res2 = client.get_offline_tasks_for_links(["magnet:?xt=urn:btih:111"], use_cache=True)
    assert len(res2) == 2
    assert client.get_offline_tasks_by_page.call_count == 1

    # 3. Cache invalidation forces fresh fetch
    client.invalidate_offline_page1_cache()
    client.get_offline_tasks_for_links(["magnet:?xt=urn:btih:111"], use_cache=True)
    assert client.get_offline_tasks_by_page.call_count == 2

    # 4. Target on page 2 stops at page 2 (never crawls pages 3..30)
    client.get_offline_tasks_by_page.reset_mock()
    client.invalidate_offline_page1_cache()
    res3 = client.get_offline_tasks_for_links(["magnet:?xt=urn:btih:333"], use_cache=False)
    assert any(t["url"] == "magnet:?xt=urn:btih:333" for t in res3)
    assert [c.kwargs["page"] for c in client.get_offline_tasks_by_page.call_args_list] == [1, 2]

    # 5. Missing target stops at max_pages cap (3 pages for small batch), NOT 30!
    client.get_offline_tasks_by_page.reset_mock()
    client.get_offline_tasks_for_links(["magnet:?xt=urn:btih:missing"], use_cache=False)
    assert client.get_offline_tasks_by_page.call_count == 3


def test_batch_completion_single_deduped_notice_and_error_doc():
    init.logger = _DummyLogger()
    init.bot_config = {"create_strm": False, "emby_server": "", "api_key": ""}

    # Case A: 2 links, both succeed -> exactly 1 quoted text notice, 0 legacy progress messages, 0 full get_offline_tasks calls
    fake_115 = MagicMock()
    fake_115.offline_download_specify_path_batch.return_value = True
    fake_115.get_file_info.return_value = {"file_id": "cid_1"}
    fake_115.get_file_list.return_value = []
    fake_115.get_offline_tasks_for_links.return_value = [
        {"url": "magnet:?xt=urn:btih:aaa", "name": "A.mp4", "status": 2, "percentDone": 100, "info_hash": "ha"},
        {"url": "magnet:?xt=urn:btih:bbb", "name": "B.mp4", "status": 2, "percentDone": 100, "info_hash": "hb"},
    ]
    fake_115.batch_process_downloads.return_value = {
        "A.mp4": {"path": "/dl/test/A.mp4", "files": ["A.mp4"]},
        "B.mp4": {"path": "/dl/test/B.mp4", "files": ["B.mp4"]},
    }
    init.openapi_115 = fake_115

    links_ok = [{"link": "magnet:?xt=urn:btih:aaa"}, {"link": "magnet:?xt=urn:btih:bbb"}]
    with patch("app.handlers.download_handler.time.sleep"), \
         patch("app.core.batch_task_manager.create_batch_task_group", return_value="grp_ok") as mock_create_grp, \
         patch("app.core.batch_task_manager.save_batch_task_items"), \
         patch("app.core.batch_task_manager.update_task_item_status"), \
         patch("app.core.batch_task_manager.update_task_group_counts"), \
         patch("app.core.batch_task_manager.generate_magnet_txt_file", return_value=True), \
         patch("app.utils.message_queue.add_task_to_queue") as mock_queue, \
         patch("app.utils.message_queue.send_document_via_bot") as mock_doc:
        _download_tasks_batch_impl(
            links_ok, "/dl", 100, custom_folder_name="MyFolder", tracker_id="dl_ok1", origin_message_id=555
        )

    fake_115.get_offline_tasks.assert_not_called()
    assert fake_115.get_offline_tasks_for_links.call_count == 1
    assert mock_create_grp.call_args.kwargs["origin_message_id"] == 555
    assert mock_create_grp.call_args.kwargs["chat_id"] == 100
    assert mock_queue.call_count == 1
    assert mock_doc.call_count == 0
    assert mock_queue.call_args.kwargs["reply_to_message_id"] == 555
    kb = mock_queue.call_args.kwargs["keyboard"]
    assert kb.inline_keyboard[0][0].callback_data == "panel:td:dl_ok1:1:all"

    # Case B: 7 links, all fail -> merged into 1 single send_document_via_bot with reply_to_message_id=666, 0 duplicate text notice
    links_fail = [{"link": f"magnet:?xt=urn:btih:{i:040x}"} for i in range(1, 8)]
    fake_115.get_offline_tasks_for_links.reset_mock()
    fake_115.get_offline_tasks_for_links.return_value = [
        {"url": item["link"], "name": f"F{i}", "status": -1, "percentDone": 0, "info_hash": f"hf{i}"}
        for i, item in enumerate(links_fail, 1)
    ]
    fake_115.batch_process_downloads.return_value = {}

    with patch("app.handlers.download_handler.time.sleep"), \
         patch("app.core.batch_task_manager.create_batch_task_group", return_value="grp_fail"), \
         patch("app.core.batch_task_manager.save_batch_task_items"), \
         patch("app.core.batch_task_manager.update_task_item_status"), \
         patch("app.core.batch_task_manager.update_task_group_counts"), \
         patch("app.core.batch_task_manager.generate_magnet_txt_file", return_value=True), \
         patch("app.core.batch_task_manager.get_batch_task_group_info", return_value={"created_at": int(time.time()) - 30}), \
         patch("app.utils.message_queue.add_task_to_queue") as mock_queue2, \
         patch("app.utils.message_queue.send_document_via_bot") as mock_doc2:
        _download_tasks_batch_impl(
            links_fail, "/dl", 100, custom_folder_name="FailFolder", tracker_id="dl_fail7", origin_message_id=666
        )

    assert mock_doc2.call_count == 1
    assert mock_queue2.call_count == 0  # NO duplicate summary text message!
    assert mock_doc2.call_args.kwargs["reply_to_message_id"] == 666
    assert mock_doc2.call_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "panel:td:dl_fail7:1:all"


def test_batch_task_db_migration_and_quoted_retry_completion():
    init.logger = _DummyLogger()
    os.makedirs(os.path.join(ROOT_DIR, "tmp"), exist_ok=True)
    test_db = os.path.join(ROOT_DIR, "tmp", "test_batch_retry_migration.db")
    if os.path.exists(test_db):
        os.remove(test_db)

    old_db_file = init.DB_FILE
    try:
        # 1. Create legacy batch_task_group table WITHOUT chat_id / origin_message_id
        conn = sqlite3.connect(test_db)
        conn.execute(
            """
            CREATE TABLE batch_task_group (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT UNIQUE NOT NULL,
                folder_name TEXT NOT NULL,
                save_path TEXT NOT NULL,
                total_count INTEGER NOT NULL,
                success_count INTEGER DEFAULT 0,
                failed_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

        init.DB_FILE = test_db
        btm.initialize_batch_task_tables(reset_sql=False)

        # Verify columns migrated cleanly
        conn = sqlite3.connect(test_db)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(batch_task_group)").fetchall()}
        conn.close()
        assert "chat_id" in cols
        assert "origin_message_id" in cols

        # 2. Create group with origin_message_id=999 and mark all items succeeded
        gid = btm.create_batch_task_group("RetryFolder", "/dl", 2, chat_id=100, origin_message_id=999)
        btm.save_batch_task_items(gid, [{"link": "magnet:?xt=urn:btih:1"}, {"link": "magnet:?xt=urn:btih:2"}])
        btm.update_task_item_status("magnet:?xt=urn:btih:1", gid, "success", "File1.mp4")
        btm.update_task_item_status("magnet:?xt=urn:btih:2", gid, "success", "File2.mp4")
        btm.update_task_group_counts(gid)

        # All completed -> sends quoted completion notice
        with patch("app.core.batch_task_retry.add_task_to_queue") as mock_q:
            btr.send_retry_summary(gid, "RetryFolder", newly_succeeded=1)
        mock_q.assert_called_once()
        assert mock_q.call_args.args[0] == 100
        assert "已全部补全" in mock_q.call_args.args[2]
        assert mock_q.call_args.kwargs["reply_to_message_id"] == 999

        # Partial retry with newly_succeeded=1, failed=1 -> sends quoted progress notice
        gid2 = btm.create_batch_task_group("PartialFolder", "/dl", 2, chat_id=100, origin_message_id=1001)
        btm.save_batch_task_items(gid2, [{"link": "magnet:?xt=urn:btih:3"}, {"link": "magnet:?xt=urn:btih:4"}])
        btm.update_task_item_status("magnet:?xt=urn:btih:3", gid2, "success", "File3.mp4")
        btm.update_task_item_status("magnet:?xt=urn:btih:4", gid2, "failed", "")
        btm.update_task_group_counts(gid2)

        with patch("app.core.batch_task_retry.add_task_to_queue") as mock_q2:
            btr.send_retry_summary(gid2, "PartialFolder", newly_succeeded=1)
        mock_q2.assert_called_once()
        assert "本次补全 1 条" in mock_q2.call_args.args[2]
        assert mock_q2.call_args.kwargs["reply_to_message_id"] == 1001

        # Partial retry with newly_succeeded=0, failed=1 -> stays quiet (no spam)
        with patch("app.core.batch_task_retry.add_task_to_queue") as mock_q3:
            btr.send_retry_summary(gid2, "PartialFolder", newly_succeeded=0)
        mock_q3.assert_not_called()
    finally:
        init.DB_FILE = old_db_file
        if os.path.exists(test_db):
            os.remove(test_db)


def test_retry_fallback_naming_and_retry_list_display():
    init.logger = _DummyLogger()
    from datetime import datetime, timezone, timedelta
    from app.utils.task_tracker import format_retry_task_name, is_fallback_retry_name
    from app.handlers.menu_handler import _build_retry_message

    tz_cn = timezone(timedelta(hours=8))
    ts = int(datetime(2026, 11, 24, 23, 22, 40, tzinfo=tz_cn).timestamp())
    m1 = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
    m2 = "magnet:?xt=urn:btih:fedcba9876543210fedcba9876543210fedcba98"

    # 1. Single link without extracted filename -> yyyyMMDDHHmmss-磁力链接
    single_name = format_retry_task_name("", m1, created_at=ts)
    assert single_name == f"20261124232240-{m1}"
    assert is_fallback_retry_name(single_name) is True

    # 2. Legacy placeholder "未知资源_1" also triggers fallback
    assert format_retry_task_name("未知资源_1", m1, created_at="2026-11-24 15:22:40") == f"20261124232240-{m1}"

    # 3. 2+ links without extracted filename (or auto folder placeholder) -> 时间+N个磁力 (mixed kinds -> 链接)
    multi_name = format_retry_task_name("20261124 232240+2V", [m1, m2], created_at=ts)
    assert multi_name == "20261124232240+2个磁力"
    assert format_retry_task_name("", [m1, "ed2k://|file|A.mp4|1|ABCDEF1234567890ABCDEF1234567890|/"], created_at=ts) == "20261124232240+2个链接"
    assert is_fallback_retry_name(multi_name) is True

    # 4. Real extracted filename (or dn=/ed2k filename) is preserved directly
    assert format_retry_task_name("Movie.1080p.mkv", m1, created_at=ts) == "Movie.1080p.mkv"
    assert format_retry_task_name("", f"{m1}&dn=Named.Show.mp4", created_at=ts) == "Named.Show.mp4"
    assert format_retry_task_name("", "ed2k://|file|Ed2kMovie.mp4|100|ABCDEF1234567890|/", created_at=ts) == "Ed2kMovie.mp4"

    # 5. Merged queue retry filter renders fallback names inside interactive buttons with attempt count
    task_tracker_mod = __import__("app.utils.task_tracker", fromlist=["clear_tasks"])
    task_tracker_mod.clear_tasks()
    fake_115 = MagicMock()
    fake_115.get_offline_tasks_by_page.return_value = {"count": 0, "tasks": []}
    init.openapi_115 = fake_115
    with patch("app.handlers.menu_handler._load_queue_groups", return_value=[
        {"group_id": "g1", "display_name": "20261124232240+2个磁力", "category": "retry",
         "s": 0, "f": 2, "d": 0, "p": 0, "total": 2, "attempt": 2, "next_at": None, "cur_wait": 1200, "created_at": ts},
    ]):
        msg, markup = _build_retry_message()
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any(lbl.startswith("🔁 20261124232240+2个磁力") and "第2次" in lbl for lbl in labels)


def test_exponential_retry_doubling_until_cap():
    """Each re-added magnet waits 2x the previous wait until the wait reaches the cap (default 24h), then fails."""
    init.logger = _DummyLogger()
    init.bot_config = {"allowed_user": [100]}
    os.makedirs(os.path.join(ROOT_DIR, "tmp"), exist_ok=True)
    test_db = os.path.join(ROOT_DIR, "tmp", "test_exponential_retry.db")
    if os.path.exists(test_db):
        os.remove(test_db)

    old_db = init.DB_FILE
    try:
        init.DB_FILE = test_db
        from app.utils.sqlitelib import SqlLiteLib
        from app.utils.bot_settings import set_retry_max_wait_hours, get_retry_max_wait_seconds
        btm.initialize_batch_task_tables(reset_sql=False)
        assert get_retry_max_wait_seconds() == 24 * 3600  # default cap 24h

        fake_115 = MagicMock()
        fake_115.get_quota_info.return_value = {"count": 1000, "used": 0}
        fake_115.offline_download_specify_path.return_value = True
        fake_115.get_offline_tasks_for_links.return_value = []
        init.openapi_115 = fake_115

        magnet = "magnet:?xt=urn:btih:3333333333333333333333333333333333333333"
        gid = btm.create_single_retry_group("", magnet, "/dl", chat_id=100, origin_message_id=321)

        def _item():
            with SqlLiteLib() as sqlite:
                return sqlite.query_all("SELECT * FROM batch_task_item WHERE group_id = ?", (gid,))[0]

        waits = []
        with patch("app.core.batch_task_retry.time.sleep"), \
             patch("app.core.batch_task_retry.add_task_to_queue") as mock_q, \
             patch("app.core.batch_task_retry.send_document_via_bot"):
            for _ in range(20):
                btr.execute_batch_retry()
                it = _item()
                if it["status"] == "dead":
                    break
                waits.append(int(it["current_wait"]))
                with SqlLiteLib() as sqlite:  # fast-forward: make next check due now
                    sqlite.execute_sql("UPDATE batch_task_item SET next_retry_at = 0 WHERE id = ?", (it["id"],))

        assert waits == [1200, 2400, 4800, 9600, 19200, 38400, 76800, 86400]
        final = _item()
        assert final["status"] == "dead"
        assert final["retry_count"] == len(waits)
        assert mock_q.call_count == 1
        assert "已标记失败" in mock_q.call_args.args[2]
        assert mock_q.call_args.kwargs["reply_to_message_id"] == 321

        # Raising the cap (e.g. 48h) lets doubling continue beyond 24h
        set_retry_max_wait_hours(48)
        assert btr.compute_next_wait(86400, get_retry_max_wait_seconds()) == 172800
        assert btr.compute_next_wait(76800, get_retry_max_wait_seconds()) == 153600

        # Success path: cloud reports done on the next check -> success + quoted completion notice
        gid2 = btm.create_single_retry_group("", "magnet:?xt=urn:btih:4444444444444444444444444444444444444444", "/dl", chat_id=100, origin_message_id=654)
        fake_115.get_offline_tasks_for_links.return_value = [
            {"url": "magnet:?xt=urn:btih:4444444444444444444444444444444444444444", "name": "Got.mp4",
             "status": 2, "percentDone": 100, "info_hash": "h4"},
        ]
        with patch("app.core.batch_task_retry.time.sleep"), \
             patch("app.core.batch_task_retry.add_task_to_queue") as mock_q2:
            btr.execute_batch_retry()
        with SqlLiteLib() as sqlite:
            st = sqlite.query_one("SELECT status FROM batch_task_item WHERE group_id = ?", (gid2,))
        assert st == "success"
        assert "已全部补全" in mock_q2.call_args.args[2]
        assert mock_q2.call_args.kwargs["reply_to_message_id"] == 654
    finally:
        init.DB_FILE = old_db
        if os.path.exists(test_db):
            os.remove(test_db)


if __name__ == "__main__":
    test_adaptive_poll_controller_eta_and_stall_early_stop()
    test_targeted_offline_paging_and_cache()
    test_batch_completion_single_deduped_notice_and_error_doc()
    test_batch_task_db_migration_and_quoted_retry_completion()
    test_retry_fallback_naming_and_retry_list_display()
    test_exponential_retry_doubling_until_cap()
    print("All adaptive polling, targeted paging/cache, deduped notice, retry naming, and quoted retry tests passed!")
