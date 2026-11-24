# -*- coding: utf-8 -*-
"""Unit tests for persistent bottom panel, pagination, minimal mode, TXT timestamp, and reactions."""

import asyncio
import importlib.util
import os
import re
import sys
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(ROOT_DIR, "app")
for p in (ROOT_DIR, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import init
from telegram import Update, ReactionTypeEmoji, ReplyParameters
from telegram.constants import ReactionEmoji
from telegram.ext import Application, TypeHandler
from app.handlers.menu_handler import (
    build_home_panel,
    build_help_text,
    build_status_text,
    build_tasks_text,
    build_tasks_view,
    build_task_detail_view,
    build_batch_group_detail_view,
    build_retry_cap_view,
    _build_retry_message,
    build_cloud_tasks_view,
    build_cloud_item_view,
    extract_quota_reset_info,
    prepare_log_snapshot,
    get_chat_panel_state,
    reset_panel_states,
    register_menu_handlers,
    register_fallback_handlers,
    handle_panel_callback,
    handle_unrecognized_message,
)
from app.handlers.download_handler import (
    register_download_handlers,
    handle_txt_file,
    handle_category_selection_global,
    extract_txt_created_time_str,
    extract_message_text_and_entity_links,
    DOWNLOAD_LINK_FILTER,
    remember_user_last_save_path,
    clear_user_last_save_path,
)
from app.utils.reaction import (
    REACTION_PROCESSING,
    REACTION_DONE,
    REACTION_ERROR,
    set_message_reaction_safe,
    clear_message_reaction_safe,
)
from app.utils import task_tracker


class _DummyLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warn(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg, **kwargs): pass


def test_reaction_emojis_and_clear():
    init.logger = _DummyLogger()
    valid = {e.value for e in ReactionEmoji}
    assert REACTION_PROCESSING in valid
    assert REACTION_DONE in valid
    assert REACTION_ERROR in valid

    bot_ok = MagicMock()
    bot_ok.set_message_reaction = AsyncMock(return_value=True)
    assert asyncio.run(set_message_reaction_safe(bot_ok, 100, 200, REACTION_PROCESSING)) is True
    assert asyncio.run(clear_message_reaction_safe(bot_ok, 100, 200)) is True
    assert bot_ok.set_message_reaction.await_count == 2
    assert bot_ok.set_message_reaction.await_args_list[1].kwargs["reaction"] == []


def test_panel_pagination_and_help_without_subscription():
    p1_text, p1_markup = build_home_panel(page=1, minimal_mode=False)
    p1_cbs = [btn.callback_data for row in p1_markup.inline_keyboard for btn in row]
    assert "第 1/2 页" in p1_text
    assert p1_cbs == [
        "panel:status", "panel:tasks",
        "panel:cloud:1", "panel:help",
        "panel:log", "panel:page:2",
    ]

    p2_text, p2_markup = build_home_panel(page=2, minimal_mode=True, retry_cap_hours=48)
    p2_cbs = [btn.callback_data for row in p2_markup.inline_keyboard for btn in row]
    assert "第 2/2 页" in p2_text
    assert "已开启" in p2_text
    assert "48小时（2天）" in p2_text
    assert p2_cbs == [
        "panel:reload", "panel:reset_path",
        "panel:auth", "panel:toggle_minimal",
        "panel:retry_cap", "panel:page:1",
    ]
    assert any("重试上限：2天" in btn.text for row in p2_markup.inline_keyboard for btn in row)

    help_text = build_help_text()
    assert "/sm" not in help_text
    assert "订阅电影" not in help_text
    for expected in ("/start", "/status", "/tasks", "/cloud_tasks", "/log", "/minimal", "/help", "/auth", "/rl", ".txt", "👀", "👌"):
        assert expected in help_text, f"Missing '{expected}' in help text"


def test_quota_reset_and_status_text():
    init.logger = _DummyLogger()
    future_ts = int(time.time()) + 3 * 86400 + 7200
    quota_payload = {
        "count": 5000,
        "used": 1028,
        "surplus": 3972,
        "max_size": 500,
        "package": [
            {
                "name": "VIP配额",
                "surplus": 3972,
                "expire_info": [{"surplus": 3972, "expire_time": future_ts}],
            }
        ],
    }
    reset_lines = extract_quota_reset_info(quota_payload)
    assert len(reset_lines) == 1
    assert "VIP配额" in reset_lines[0]
    assert "3天" in reset_lines[0]

    fake_115 = MagicMock()
    fake_115.get_user_info.return_value = {
        "user_id": 10001,
        "user_name": "测试用户",
        "vip_info": {"level_name": "长期VIP(至尊版)", "expire": 253402271999},
        "rt_space_info": {
            "all_total": {"size_format": "5.08PB"},
            "all_use": {"size_format": "3.34PB"},
            "all_remain": {"size_format": "1.73PB"},
        },
    }
    fake_115.get_quota_info.return_value = quota_payload
    init.openapi_115 = fake_115
    init.bot_config = {
        "category_folder": [
            {"name": "av", "display_name": "AV", "path_map": [{"name": "AV", "path": "/115BotDownload/AV"}]}
        ]
    }
    clear_user_last_save_path()
    remember_user_last_save_path(42, "/115BotDownload/AV")

    status_text = build_status_text(42)
    assert "测试用户" in status_text
    assert "长期 / 永久" in status_text
    assert "5.08PB" in status_text
    assert "3972" in status_text
    assert "/115BotDownload/AV" in status_text
    clear_user_last_save_path()


def test_tasks_text_fast_page1_and_idle_usage_hint():
    init.logger = _DummyLogger()
    task_tracker.clear_tasks()

    fake_115 = MagicMock()
    fake_115.get_offline_tasks_by_page.return_value = {
        "count": 1,
        "tasks": [{"name": "done.mp4", "status": 2, "percentDone": 100}],
    }
    init.openapi_115 = fake_115
    with patch("app.handlers.menu_handler._get_local_batch_db_stats", return_value={
        "pending_retry": 0, "dead": 0, "active_groups": 0, "offline_retry": 0
    }), patch("app.handlers.menu_handler._load_queue_groups", return_value=[]):
        idle_text = build_tasks_text()

    fake_115.get_offline_tasks_by_page.assert_called_once_with(page=1)
    fake_115.get_offline_tasks.assert_not_called()
    assert "下载 / 上传使用说明" in idle_text


def test_bottom_single_panel_and_minimal_mode_behavior():
    init.logger = _DummyLogger()
    init.bot_config = {"allowed_user": [42]}
    reset_panel_states()

    msg_counter = [100]

    async def _fake_send_message(**kwargs):
        msg_counter[0] += 1
        return MagicMock(message_id=msg_counter[0])

    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=_fake_send_message)
    bot.delete_message = AsyncMock(return_value=True)
    bot.set_message_reaction = AsyncMock(return_value=True)

    def _make_cb(action, panel_id):
        q = MagicMock()
        q.data = action
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.message = MagicMock(chat_id=100, message_id=panel_id)
        upd = MagicMock()
        upd.callback_query = q
        upd.effective_user = MagicMock(id=42)
        upd.effective_chat = MagicMock(id=100)
        ctx = MagicMock(bot=bot)
        return upd, ctx, q

    state = get_chat_panel_state(100)
    assert state["minimal_mode"] is True  # minimal mode defaults to ON
    state["panel_message_id"] = 90

    with patch("app.handlers.menu_handler.build_status_text", return_value="status_msg"), \
         patch("app.handlers.menu_handler.build_tasks_text", return_value="tasks_msg"), \
         patch("app.handlers.menu_handler._send_log_file", new=AsyncMock(return_value=True)) as mock_send_log:

        # 1. Flip to page 2 -> edits in-place, sets 👀 then removes reaction []
        upd, ctx, q = _make_cb("panel:page:2", 90)
        asyncio.run(handle_panel_callback(upd, ctx))
        q.edit_message_text.assert_awaited_once()
        assert state["panel_page"] == 2
        assert bot.set_message_reaction.await_args_list[-2].kwargs["reaction"] == [ReactionTypeEmoji(REACTION_PROCESSING)]
        assert bot.set_message_reaction.await_args_list[-1].kwargs["reaction"] == []

        # 2. First status click in default minimal mode -> sends status (101), deletes old panel (90), sends bottom panel (102)
        upd, ctx, _ = _make_cb("panel:status", 90)
        asyncio.run(handle_panel_callback(upd, ctx))
        assert state["minimal_mode"] is True
        assert state["last_result_message_id"] == 101
        assert state["panel_message_id"] == 102
        deleted_ids = [c.kwargs["message_id"] for c in bot.delete_message.await_args_list]
        assert 90 in deleted_ids

        # 3. Second status click in minimal mode -> deletes previous non-log result (101) AND old panel (102), sends status (103) + bottom panel (104)
        bot.delete_message.reset_mock()
        upd, ctx, _ = _make_cb("panel:status", 102)
        asyncio.run(handle_panel_callback(upd, ctx))
        deleted_ids = [c.kwargs["message_id"] for c in bot.delete_message.await_args_list]
        assert 101 in deleted_ids
        assert 102 in deleted_ids
        assert state["last_result_message_id"] == 103
        assert state["panel_message_id"] == 104

        # 4. Click log twice in minimal mode -> NEVER deletes previous result/logs, only moves panel to bottom!
        bot.delete_message.reset_mock()
        upd, ctx, _ = _make_cb("panel:log", 104)
        asyncio.run(handle_panel_callback(upd, ctx))
        deleted_ids = [c.kwargs["message_id"] for c in bot.delete_message.await_args_list]
        assert deleted_ids == [104]  # only old panel deleted, NOT 103 or previous logs
        assert state["last_result_message_id"] == 103
        assert state["panel_message_id"] == 105

        bot.delete_message.reset_mock()
        upd, ctx, _ = _make_cb("panel:log", 105)
        asyncio.run(handle_panel_callback(upd, ctx))
        deleted_ids = [c.kwargs["message_id"] for c in bot.delete_message.await_args_list]
        assert deleted_ids == [105]
        assert mock_send_log.await_count == 2

        # 5. Toggle minimal mode OFF -> minimal_mode becomes False
        upd, ctx, _ = _make_cb("panel:toggle_minimal", 106)
        asyncio.run(handle_panel_callback(upd, ctx))
        assert state["minimal_mode"] is False


def test_txt_created_time_extraction():
    # Forwarded TXT message: prefers forward_origin.date (UTC -> UTC+8)
    fwd_msg = MagicMock()
    fwd_msg.forward_origin = MagicMock(date=datetime(2025, 10, 8, 6, 30, tzinfo=timezone.utc))
    fwd_msg.date = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)
    assert extract_txt_created_time_str(fwd_msg) == "20251008_1430"

    # Direct uploaded TXT message: uses message.date (UTC -> UTC+8)
    direct_msg = MagicMock()
    direct_msg.forward_origin = None
    direct_msg.forward_date = None
    direct_msg.date = datetime(2026, 11, 24, 13, 45, tzinfo=timezone.utc)
    assert extract_txt_created_time_str(direct_msg) == "20261124_2145"


def test_handler_groups_and_bot_menu_without_sm():
    init.logger = _DummyLogger()
    spec = importlib.util.spec_from_file_location("bot_entry_panel_test", os.path.join(APP_DIR, "115bot.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    menu_cmds = [c.command for c in mod.get_bot_menu()]
    for removed_cmd in ("sm", "csh", "cjav"):
        assert removed_cmd not in menu_cmds
    for cmd in ("start", "status", "tasks", "cloud_tasks", "log", "minimal", "help"):
        assert cmd in menu_cmds

    import app.core.scheduler as sched
    task_ids = [t["id"] for t in sched.tasks]
    assert task_ids == ["batch_task_retry"]

    app = Application.builder().token("123456:TESTTOKEN").build()
    app.add_handler(TypeHandler(Update, mod.log_raw_update), group=-100)
    app.add_handler(TypeHandler(Update, mod.react_on_incoming_message), group=-99)
    register_menu_handlers(app)
    register_download_handlers(app)

    assert any(getattr(h, "callback", None) is mod.log_raw_update for h in app.handlers[-100])
    assert any(getattr(h, "callback", None) is mod.react_on_incoming_message for h in app.handlers[-99])
    assert any(getattr(h, "callback", None) is handle_txt_file for h in app.handlers[-1])

    g0_callbacks = [getattr(h, "callback", None) for h in app.handlers[0]]
    assert g0_callbacks.index(handle_panel_callback) < g0_callbacks.index(handle_category_selection_global)


def test_forwarded_caption_and_entity_magnet_detection():
    # 1. Forwarded photo with magnet in caption (uppercase)
    caption_msg = MagicMock(
        text=None,
        caption="作品标题\nMAGNET:?xt=urn:btih:0123456789ABCDEF0123456789ABCDEF01234567",
        document=None,
        entities=[],
        caption_entities=[],
    )
    assert DOWNLOAD_LINK_FILTER.filter(caption_msg) is True
    assert "MAGNET:?xt=" in extract_message_text_and_entity_links(caption_msg)

    # 2. Text message with magnet inside text_link entity
    ent = MagicMock(url="magnet:?xt=urn:btih:abcdef1234567890abcdef1234567890abcdef12")
    entity_msg = MagicMock(
        text="点我下载",
        caption=None,
        document=None,
        entities=[ent],
        caption_entities=[],
    )
    assert DOWNLOAD_LINK_FILTER.filter(entity_msg) is True
    assert "magnet:?xt=urn:btih:abcdef" in extract_message_text_and_entity_links(entity_msg)

    # 3. .txt document with magnet in caption must NOT be hijacked by DOWNLOAD_LINK_FILTER
    txt_doc_msg = MagicMock(
        text=None,
        caption="magnet:?xt=urn:btih:1234",
        document=MagicMock(file_name="list.txt"),
        entities=[],
        caption_entities=[],
    )
    assert DOWNLOAD_LINK_FILTER.filter(txt_doc_msg) is False


def test_unrecognized_message_fallback_always_responds():
    init.logger = _DummyLogger()
    init.bot_config = {"allowed_user": [42]}
    reset_panel_states()

    cases = [
        (MagicMock(message_id=1, text="/unknown_cmd", caption=None, document=None, photo=[]), "未识别的命令"),
        (MagicMock(message_id=2, text=None, caption=None, document=MagicMock(file_name="data.zip", mime_type="application/zip"), photo=[]), "暂不支持处理该文件"),
        (MagicMock(message_id=3, text=None, caption="只有图片描述没有磁力", document=None, photo=[MagicMock()]), "未在配文中检测到"),
        (MagicMock(message_id=4, text="随便说句话", caption=None, document=None, photo=[]), "未能识别该消息内容"),
    ]

    for msg, expected_substr in cases:
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=500))
        bot.set_message_reaction = AsyncMock(return_value=True)
        upd = MagicMock(message=msg, effective_user=MagicMock(id=42), effective_chat=MagicMock(id=100))
        ctx = MagicMock(bot=bot, user_data={})
        asyncio.run(handle_unrecognized_message(upd, ctx))

        bot.send_message.assert_awaited_once()
        sent_text = bot.send_message.await_args.kwargs["text"]
        assert expected_substr in sent_text, f"Expected '{expected_substr}' in '{sent_text}'"
        assert bot.set_message_reaction.await_args.kwargs["reaction"] == [ReactionTypeEmoji(REACTION_ERROR)]

    # Must skip .txt document (handled in group=-1) and active rename_data text (handled in group=1)
    for skip_msg, udata in [
        (MagicMock(message_id=5, text=None, caption=None, document=MagicMock(file_name="a.txt", mime_type="text/plain"), photo=[]), {}),
        (MagicMock(message_id=6, text="新电影名", caption=None, document=None, photo=[]), {"rename_data": {"old": "x"}}),
    ]:
        bot = MagicMock()
        bot.send_message = AsyncMock()
        upd = MagicMock(message=skip_msg, effective_user=MagicMock(id=42), effective_chat=MagicMock(id=100))
        ctx = MagicMock(bot=bot, user_data=udata)
        asyncio.run(handle_unrecognized_message(upd, ctx))
        bot.send_message.assert_not_awaited()


def test_task_tracker_items_and_detail_pagination():
    init.logger = _DummyLogger()
    task_tracker.clear_tasks()

    assert task_tracker.extract_display_name_from_link(
        "ed2k://|file|Sample.Movie.mkv|123456|ABCDEF1234567890|/"
    ) == "Sample.Movie.mkv"
    assert task_tracker.extract_display_name_from_link(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=Test%20Show%201080p"
    ) == "Test Show 1080p"
    assert task_tracker.extract_display_name_from_link(
        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
    ) == "0123456789abcdef0123456789abcdef01234567"

    items = [
        {"index": i, "link": f"magnet:?xt=urn:btih:{i:040x}", "name": f"Item_{i}.mp4", "status": "pending", "percent": 0}
        for i in range(1, 19)
    ]
    task_id = "dl_test_18"
    task_tracker.register_task(
        task_id,
        "download",
        "TXT批量下载 (18条)",
        total=18,
        save_path="/115BotDownload/Test",
        items=items,
        chat_id=100,
        origin_message_id=888,
    )
    task_tracker.update_task_item(task_id, 1, name="Item_1_Renamed.mp4", status="success", percent=100)
    task_tracker.update_task_item(task_id, 2, status="running", percent=65)
    task_tracker.update_task_item(task_id, 3, status="failed", percent=0, error="提交超时")
    task_tracker.finish_task(task_id, status="completed", note="完成")

    saved = task_tracker.get_task(task_id)
    assert saved is not None
    assert saved["origin_message_id"] == 888
    assert saved["completed"] == 1
    assert saved["failed"] == 1
    assert len(task_tracker.list_queue_tasks()) == 1

    fake_115 = MagicMock()
    fake_115.get_offline_tasks_by_page.return_value = {"count": 0, "tasks": []}
    init.openapi_115 = fake_115
    with patch("app.handlers.menu_handler._get_local_batch_db_stats", return_value={
        "pending_retry": 0, "dead": 0, "active_groups": 0, "offline_retry": 0
    }), patch("app.handlers.menu_handler._load_queue_groups", return_value=[]):
        q_text, q_markup = build_tasks_view()
    q_cbs = [btn.callback_data for row in q_markup.inline_keyboard for btn in row]
    q_labels = [btn.text for row in q_markup.inline_keyboard for btn in row]
    assert f"panel:td:{task_id}:1:all" in q_cbs
    assert "panel:tq:all" in q_cbs
    assert "panel:tq:retry" in q_cbs
    assert "panel:cloud_edit:1" in q_cbs
    # Task records appear ONLY as interactive buttons (not duplicated in the text)
    assert "TXT批量下载" not in q_text
    assert any("TXT批量下载" in lbl and "第1次" in lbl for lbl in q_labels)

    # Page 1 of 2 (15 items per page -> 18 items => 2 pages)
    d1_text, d1_markup = build_task_detail_view(task_id, page=1, mode="all")
    d1_cbs = [btn.callback_data for row in d1_markup.inline_keyboard for btn in row]
    assert "第 1/2 页" in d1_text
    assert "Item_1_Renamed.mp4" in d1_text
    assert "65%" in d1_text
    assert "提交超时" in d1_text
    assert f"panel:td:{task_id}:2:all" in d1_cbs
    assert f"panel:td:{task_id}:1:failed" in d1_cbs

    # Page 2 shows items 16..18
    d2_text, _ = build_task_detail_view(task_id, page=2, mode="all")
    assert "第 2/2 页" in d2_text
    assert "Item_18.mp4" in d2_text

    # Failed-only filter shows only Item_3
    df_text, _ = build_task_detail_view(task_id, page=1, mode="failed")
    assert "仅失败项" in df_text
    assert "Item_3.mp4" in df_text
    assert "Item_1_Renamed.mp4" not in df_text

    task_tracker.clear_tasks()


def test_cloud_tasks_manager_and_callbacks():
    init.logger = _DummyLogger()
    init.bot_config = {"allowed_user": [42]}
    reset_panel_states()

    fake_115 = MagicMock()
    fake_115.get_offline_tasks_by_page.return_value = {
        "count": 2,
        "page_count": 2,
        "tasks": [
            {"info_hash": "hash_done_1", "name": "MovieA.mkv", "status": 2, "percentDone": 100, "url": "magnet:?xt=urn:btih:aaa"},
            {"info_hash": "hash_fail_2", "name": "MovieB.mkv", "status": -1, "percentDone": 0, "url": "magnet:?xt=urn:btih:bbb"},
        ],
    }
    fake_115.del_faild_offline_task.return_value = True
    fake_115.clear_cloud_task.return_value = True
    init.openapi_115 = fake_115

    text, markup = build_cloud_tasks_view(chat_id=100, page=1)
    cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert "MovieA.mkv" in text
    assert "MovieB.mkv" in text
    assert "panel:cloud_item:1:1" in cbs
    assert "panel:cloud_item:1:2" in cbs
    assert "panel:cloud_edit:2" in cbs
    assert "panel:cloud_clear:4:1" in cbs
    assert "panel:cloud_clear:2:1" in cbs
    assert "panel:cloud_clear_ask:1:1" in cbs

    item_text, item_markup = build_cloud_item_view(chat_id=100, page=1, idx_str="1")
    item_cbs = [btn.callback_data for row in item_markup.inline_keyboard for btn in row]
    assert "hash_done_1" in item_text
    assert "panel:cloud_del:1:1:0" in item_cbs
    assert "panel:cloud_del:1:1:1" in item_cbs

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=301))
    bot.delete_message = AsyncMock(return_value=True)
    bot.set_message_reaction = AsyncMock(return_value=True)

    def _run_cb(action):
        q = MagicMock()
        q.data = action
        q.answer = AsyncMock()
        q.edit_message_text = AsyncMock()
        q.message = MagicMock(chat_id=100, message_id=200)
        upd = MagicMock(callback_query=q, effective_user=MagicMock(id=42), effective_chat=MagicMock(id=100))
        ctx = MagicMock(bot=bot)
        asyncio.run(handle_panel_callback(upd, ctx))
        return q

    # 1. Open cloud list from panel
    _run_cb("panel:cloud:1")
    assert bot.send_message.await_count >= 2  # cloud list + bottom panel

    # 2. Click item #1 -> edits in place to item detail view
    q = _run_cb("panel:cloud_item:1:1")
    assert "MovieA.mkv" in q.edit_message_text.await_args.kwargs["text"]

    # 3. Delete record only -> del_faild_offline_task("hash_done_1", False)
    q = _run_cb("panel:cloud_del:1:1:0")
    fake_115.del_faild_offline_task.assert_called_with("hash_done_1", False)
    assert "仅移除记录" in q.edit_message_text.await_args.kwargs["text"]

    # 4. Delete with source file -> del_faild_offline_task("hash_done_1", True)
    q = _run_cb("panel:cloud_del:1:1:1")
    fake_115.del_faild_offline_task.assert_called_with("hash_done_1", True)
    assert "含源文件" in q.edit_message_text.await_args.kwargs["text"]

    # 5. Ask confirm clear all -> shows confirmation button panel:cloud_clear:1:1
    q = _run_cb("panel:cloud_clear_ask:1:1")
    confirm_cbs = [btn.callback_data for row in q.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard for btn in row]
    assert "panel:cloud_clear:1:1" in confirm_cbs

    # 6. Clear completed -> clear_cloud_task(4)
    q = _run_cb("panel:cloud_clear:4:1")
    fake_115.clear_cloud_task.assert_called_with(4)
    assert "已清空 115 云端已完成任务" in q.edit_message_text.await_args.kwargs["text"]


def test_message_queue_reply_to_origin_message():
    init.logger = _DummyLogger()
    from app.utils import message_queue as mq

    async def _run():
        mq.message_queue = None
        mq.global_loop = None
        mq.global_bot = None
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=999))
        loop = asyncio.get_running_loop()

        with patch("app.utils.message_queue.Bot", return_value=bot):
            worker = asyncio.create_task(mq.queue_worker(loop, "123456:TESTTOKEN"))
            # Wait briefly for queue_worker to initialize mq.message_queue
            for _ in range(50):
                if mq.message_queue is not None and mq.global_loop is not None:
                    break
                await asyncio.sleep(0.01)

            ok = await asyncio.to_thread(
                mq.add_task_to_queue,
                100,
                None,
                "✅ 下载完成",
                None,
                0,
                777,
            )
            assert ok is True
            await asyncio.wait_for(mq.message_queue.join(), timeout=5)
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        return bot

    bot = asyncio.run(_run())
    bot.send_message.assert_awaited_once()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 100
    assert kwargs["text"] == "✅ 下载完成"
    rp = kwargs["reply_parameters"]
    assert isinstance(rp, ReplyParameters)
    assert rp.message_id == 777
    assert rp.allow_sending_without_reply is True


def test_logger_accepts_exc_info_and_document_task_detail_callback():
    from app.utils.logger import Logger
    import logging

    # 1. Verify Logger.error/info/warn accept exc_info kwargs without TypeError
    fake_std_logger = MagicMock()
    lg = Logger.__new__(Logger)
    lg.logger = fake_std_logger
    lg.error("boom", exc_info=True)
    fake_std_logger.error.assert_called_once_with("boom", exc_info=True)

    # 2. Clicking panel:td on a document message (text=None) must NOT crash edit_message_text,
    #    instead sends a new detail message while preserving the document message.
    init.logger = _DummyLogger()
    init.bot_config = {"allowed_user": [42]}
    reset_panel_states()
    task_tracker.clear_tasks()
    task_tracker.register_task(
        "dl_doc1",
        "download",
        "DocBatch",
        total=1,
        items=[{"index": 1, "link": "magnet:?xt=urn:btih:1234", "name": "Test.mp4", "status": "failed", "percent": 0}],
    )

    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=701))
    bot.delete_message = AsyncMock(return_value=True)
    bot.set_message_reaction = AsyncMock(return_value=True)

    q = MagicMock()
    q.data = "panel:td:dl_doc1:1:all"
    q.answer = AsyncMock()
    q.edit_message_text = AsyncMock()
    q.message = MagicMock(chat_id=100, message_id=600, text=None, caption="错误日志汇总")
    upd = MagicMock(callback_query=q, effective_user=MagicMock(id=42), effective_chat=MagicMock(id=100))
    ctx = MagicMock(bot=bot)

    asyncio.run(handle_panel_callback(upd, ctx))
    q.edit_message_text.assert_not_awaited()
    bot.send_message.assert_awaited_once()
    assert "DocBatch" in bot.send_message.await_args.kwargs["text"]
    task_tracker.clear_tasks()


def test_clickable_persisted_task_queue_and_retry_list():
    """Merged task queue: filters, buttons-only records, attempt counts, next-retry info, legacy /rl migration."""
    init.logger = _DummyLogger()
    init.bot_config = {"allowed_user": [42]}
    reset_panel_states()
    task_tracker.clear_tasks()

    os.makedirs(os.path.join(ROOT_DIR, "tmp"), exist_ok=True)
    test_db = os.path.join(ROOT_DIR, "tmp", "test_clickable_queue_retry.db")
    if os.path.exists(test_db):
        os.remove(test_db)

    old_db = init.DB_FILE
    try:
        init.DB_FILE = test_db
        from app.utils.sqlitelib import SqlLiteLib
        from app.core import batch_task_manager as btm
        from app.handlers.download_handler import save_failed_download_to_db

        m1 = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
        m2 = "magnet:?xt=urn:btih:fedcba9876543210fedcba9876543210fedcba98"
        m3 = "magnet:?xt=urn:btih:1111111111111111111111111111111111111111"
        m4 = "magnet:?xt=urn:btih:2222222222222222222222222222222222222222"

        # Legacy /rl table with one pending row -> must be migrated into the unified queue
        with SqlLiteLib() as sqlite:
            sqlite.execute_sql(
                """
                CREATE TABLE IF NOT EXISTS offline_task (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    save_path TEXT,
                    magnet TEXT,
                    is_download TINYINT DEFAULT 0,
                    retry_count INTEGER DEFAULT 1,
                    completed_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            sqlite.execute_sql(
                "INSERT INTO offline_task (title, magnet, save_path, created_at) VALUES (?, ?, ?, ?)",
                ("未知资源_1", m4, "/legacy", "2026-11-24 15:22:40"),
            )
        btm.initialize_batch_task_tables(reset_sql=False)
        with SqlLiteLib() as sqlite:
            assert sqlite.query_one("SELECT is_download FROM offline_task WHERE magnet = ?", (m4,)) == 2

        # Batch group: 1 success + 1 failed (pending retry, 3rd attempt)
        gid = btm.create_batch_task_group("20261124 235000+2V", "/dl", 2, chat_id=100, origin_message_id=501)
        btm.save_batch_task_items(gid, [{"link": m1}, {"link": m2}])
        btm.update_task_item_status(m1, gid, "success", "DoneMovie.mp4")
        btm.update_task_item_status(m2, gid, "failed", "")
        with SqlLiteLib() as sqlite:
            item_id = sqlite.query_one("SELECT id FROM batch_task_item WHERE magnet = ? AND group_id = ?", (m2, gid))
        btm.schedule_retry_attempt(item_id, 2, 2400)
        btm.update_task_group_counts(gid)

        # Single-link failure goes into the same unified queue
        single_gid = save_failed_download_to_db("未知资源_1", m3, "/dl")
        assert single_gid

        fake_115 = MagicMock()
        fake_115.get_offline_tasks_by_page.return_value = {"count": 0, "tasks": []}
        init.openapi_115 = fake_115

        # 1. "All" view: filter buttons + task buttons; records NOT duplicated in text
        q_text, q_markup = build_tasks_view("all")
        q_cbs = [btn.callback_data for row in q_markup.inline_keyboard for btn in row]
        q_labels = [btn.text for row in q_markup.inline_keyboard for btn in row]
        for key in ("all", "run", "retry", "done", "fail"):
            assert f"panel:tq:{key}" in q_cbs
        assert f"panel:bd:{gid}:1:failed:all" in q_cbs
        assert f"panel:bd:{single_gid}:1:failed:all" in q_cbs
        assert "待重试 <b>3</b>" in q_text  # batch group + single + migrated legacy
        assert "magnet:?xt=urn:btih" not in q_text
        assert any(re.match(r"^🔁 \d{14}\+2个磁力 \(1/2·第3次\)$", lbl) for lbl in q_labels)

        # 2. Retry filter (also used by /rl and _build_retry_message alias)
        r_text, r_markup = _build_retry_message()
        r_cbs = [btn.callback_data for row in r_markup.inline_keyboard for btn in row]
        assert "筛选：<b>待重试</b>" in r_text
        assert f"panel:bd:{gid}:1:failed:retry" in r_cbs

        # 3. Detail view: attempt number + next auto retry (relative + absolute time)
        bd_text, bd_markup = build_batch_group_detail_view(gid, page=1, mode="all", back="retry")
        bd_cbs = [btn.callback_data for row in bd_markup.inline_keyboard for btn in row]
        assert "第 3 次尝试任务" in bd_text
        assert "下次自动重试：（" in bd_text and "后）" in bd_text
        assert "DoneMovie.mp4" in bd_text
        assert f"panel:bg_retry:{gid}:retry" in bd_cbs
        assert f"panel:bg_del:{gid}:retry" in bd_cbs
        assert "panel:tq:retry" in bd_cbs

        # 4. Retry-cap settings view shows doubling chain and options up to 16 days
        cap_text, cap_markup = build_retry_cap_view()
        cap_cbs = [btn.callback_data for row in cap_markup.inline_keyboard for btn in row]
        assert "10分钟 → 20分钟 → 40分钟" in cap_text
        assert "panel:retry_cap_set:384" in cap_cbs

        # 5. Callbacks: filter switch, set cap, delete group
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=801))
        bot.delete_message = AsyncMock(return_value=True)
        bot.set_message_reaction = AsyncMock(return_value=True)

        def _cb(action):
            q = MagicMock()
            q.data = action
            q.answer = AsyncMock()
            q.edit_message_text = AsyncMock()
            q.message = MagicMock(chat_id=100, message_id=700, text="queue")
            upd = MagicMock(callback_query=q, effective_user=MagicMock(id=42), effective_chat=MagicMock(id=100))
            ctx = MagicMock(bot=bot)
            asyncio.run(handle_panel_callback(upd, ctx))
            return q

        q1 = _cb("panel:tq:done")
        assert "筛选：<b>已完成</b>" in q1.edit_message_text.await_args.kwargs["text"]
        q2 = _cb("panel:retry_cap_set:96")
        assert "96小时（4天）" in q2.edit_message_text.await_args.kwargs["text"]
        from app.utils.bot_settings import get_retry_max_wait_hours
        assert get_retry_max_wait_hours() == 96
        q3 = _cb(f"panel:bg_del:{gid}:retry")
        assert "已删除批量任务组记录" in q3.edit_message_text.await_args.kwargs["text"]
        assert btm.get_batch_task_group_info(gid) is None
    finally:
        init.DB_FILE = old_db
        if os.path.exists(test_db):
            os.remove(test_db)


if __name__ == "__main__":
    test_reaction_emojis_and_clear()
    test_panel_pagination_and_help_without_subscription()
    test_quota_reset_and_status_text()
    test_tasks_text_fast_page1_and_idle_usage_hint()
    test_bottom_single_panel_and_minimal_mode_behavior()
    test_txt_created_time_extraction()
    test_forwarded_caption_and_entity_magnet_detection()
    test_unrecognized_message_fallback_always_responds()
    test_handler_groups_and_bot_menu_without_sm()
    test_task_tracker_items_and_detail_pagination()
    test_cloud_tasks_manager_and_callbacks()
    test_message_queue_reply_to_origin_message()
    test_logger_accepts_exc_info_and_document_task_detail_callback()
    test_clickable_persisted_task_queue_and_retry_list()
    print("All panel, task-queue, 115-cloud-manager, reply-quoting, and fallback tests passed!")
