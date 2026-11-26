# -*- coding: utf-8 -*-
"""Interactive persistent bottom panel, task queue with magnet details, 115 cloud manager, log export."""

import asyncio
import html
import json
import os
import shutil
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

import init
from app.utils.reaction import (
    REACTION_PROCESSING,
    REACTION_DONE,
    REACTION_ERROR,
    set_message_reaction_safe,
    clear_message_reaction_safe,
)
from app.utils.sqlitelib import SqlLiteLib
from app.utils.task_tracker import (
    get_active_tasks,
    get_recent_tasks,
    get_task,
    list_queue_tasks,
    format_retry_task_name,
)

TZ_CN = timezone(timedelta(hours=8))
BOT_VERSION = "v3.2.13"
MAX_TG_LOG_BYTES = 45 * 1024 * 1024   # Telegram Bot API limit is 50MB
TRUNCATED_LOG_TAIL_BYTES = 10 * 1024 * 1024
TASK_DETAIL_PAGE_SIZE = 15
CLOUD_PAGE_DISPLAY_LIMIT = 10
DEFAULT_PANEL_REFRESH_DEBOUNCE_SEC = 3.0


def get_panel_refresh_debounce_sec() -> float:
    """Return bottom-panel refresh debounce delay in seconds (defaults to 3.0s, matching message merge interval)."""
    cfg = getattr(init, "bot_config", None) or {}
    if isinstance(cfg, dict) and "panel_refresh_debounce_sec" in cfg:
        try:
            return max(0.0, float(cfg.get("panel_refresh_debounce_sec")))
        except (TypeError, ValueError):
            pass
    return DEFAULT_PANEL_REFRESH_DEBOUNCE_SEC


def _ensure_panel_state_map() -> Dict[int, Dict]:
    if not hasattr(init, "bot_session") or not isinstance(init.bot_session, dict):
        init.bot_session = {}
    state_map = init.bot_session.setdefault("panel_state", {})
    if not isinstance(state_map, dict):
        state_map = {}
        init.bot_session["panel_state"] = state_map
    return state_map


def get_chat_panel_state(chat_id: int) -> Dict:
    """Return mutable per-chat panel state dict (minimal_mode defaults to True)."""
    state_map = _ensure_panel_state_map()
    state = state_map.get(chat_id)
    if not isinstance(state, dict):
        state = {
            "panel_message_id": None,
            "panel_page": 1,
            "minimal_mode": True,
            "last_result_message_id": None,
            "cloud_cache": {},
        }
        state_map[chat_id] = state
    state.setdefault("cloud_cache", {})
    return state


def reset_panel_states() -> None:
    """Clear all per-chat panel states (useful for unit tests)."""
    state_map = _ensure_panel_state_map()
    state_map.clear()


def _fmt_ts(ts: Optional[int]) -> str:
    if not ts or ts <= 0:
        return "未知"
    if ts >= 253402200000:
        return "长期 / 永久"
    try:
        return datetime.fromtimestamp(int(ts), tz=TZ_CN).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}时{m}分{s}秒"
    if m > 0:
        return f"{m}分{s}秒"
    return f"{s}秒"


def _fmt_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.2f} MB"


def _cloud_status_icon(status: Optional[int], percent: Optional[int] = None) -> Tuple[str, str]:
    if status == 2 or percent == 100:
        return "✅", "已完成"
    if status == -1:
        return "❌", "失败"
    if status == 1:
        pct = percent if percent is not None else 0
        return "⏳", f"下载中 {pct}%"
    return "🕒", "等待中"


async def _safe_delete_message(bot, chat_id: Optional[int], message_id: Optional[int]) -> bool:
    if not bot or not chat_id or not message_id:
        return False
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except Exception as e:
        if init.logger:
            init.logger.debug(f"删除消息失败 (chat={chat_id}, msg={message_id}): {e}")
        return False


def extract_quota_reset_info(quota_info: Optional[Dict]) -> List[str]:
    """Extract quota reset/expiry timestamps from 115 get_quota_info() payload."""
    if not isinstance(quota_info, dict):
        return []
    now = int(time.time())
    lines: List[str] = []
    for pkg in quota_info.get("package") or []:
        if not isinstance(pkg, dict):
            continue
        pkg_name = pkg.get("name") or "配额"
        expire_list = pkg.get("expire_info")
        if not isinstance(expire_list, list):
            continue
        for item in expire_list:
            if not isinstance(item, dict):
                continue
            exp_ts = item.get("expire_time")
            if not exp_ts:
                continue
            try:
                exp_int = int(exp_ts)
            except (TypeError, ValueError):
                continue
            exp_str = _fmt_ts(exp_int)
            remain_sec = exp_int - now
            if remain_sec > 0:
                days, rem = divmod(remain_sec, 86400)
                hours = rem // 3600
                countdown = f"（约 {days}天{hours}小时后重置/到期）" if days > 0 else f"（约 {hours}小时后重置/到期）"
            else:
                countdown = "（已到期）"
            surplus = item.get("surplus")
            surplus_hint = f" [剩余 {surplus}]" if surplus is not None else ""
            lines.append(f"• {html.escape(str(pkg_name))}{surplus_hint}: <code>{exp_str}</code> {countdown}")
    return lines


def build_home_panel(
    page: int = 1,
    minimal_mode: bool = True,
    retry_cap_hours: Optional[int] = None,
    auto_retry_enabled: Optional[bool] = None,
) -> Tuple[str, InlineKeyboardMarkup]:
    """Build main persistent bottom panel (Page 1: core actions, Page 2: low-frequency/settings)."""
    page = 2 if page == 2 else 1
    minimal_label = "已开启" if minimal_mode else "已关闭"

    if page == 1:
        text = (
            f"<b>🍿 Telegram-115Bot {BOT_VERSION} 控制面板（第 1/2 页）</b>\n\n"
            "点击下方按钮执行操作，或直接发送 <b>磁力 / 种子 / ed2k / 迅雷链接</b>、<b>.txt / .csv / .html / .htm / Excel / .torrent 文件</b> 或 <b>视频</b>。\n"
            f"对话极简模式：<b>{minimal_label}</b>（主面板始终保持在最底部）"
        )
        keyboard = [
            [
                InlineKeyboardButton("📊 账号与状态", callback_data="panel:status"),
                InlineKeyboardButton("📥 任务队列", callback_data="panel:tasks"),
            ],
            [
                InlineKeyboardButton("☁️ 115云端任务", callback_data="panel:cloud:1"),
                InlineKeyboardButton("❓ 帮助", callback_data="panel:help"),
            ],
            [
                InlineKeyboardButton("📄 获取日志", callback_data="panel:log"),
                InlineKeyboardButton("➡️ 下一页", callback_data="panel:page:2"),
            ],
        ]
    else:
        from app.utils.bot_settings import (
            get_retry_max_wait_hours,
            format_retry_cap_label,
            is_auto_retry_enabled,
        )
        cap_hours = retry_cap_hours if retry_cap_hours is not None else get_retry_max_wait_hours()
        cap_short = f"{cap_hours}小时" if cap_hours <= 24 else f"{cap_hours // 24}天"
        auto_retry = is_auto_retry_enabled() if auto_retry_enabled is None else bool(auto_retry_enabled)
        auto_retry_label = "已开启" if auto_retry else "已关闭"
        auto_retry_btn = "🔁 自动重试：开启" if auto_retry else "🔁 自动重试：关闭"
        minimal_btn = "✨ 对话极简：开启" if minimal_mode else "✨ 对话极简：关闭"
        text = (
            f"<b>⚙️ Telegram-115Bot {BOT_VERSION} 更多功能（第 2/2 页）</b>\n\n"
            "低频维护与系统设置功能：\n"
            f"• 对话极简模式：<b>{minimal_label}</b>（开启后除日志外只保留最新 1 条反馈消息）\n"
            f"• 自动重试机制：<b>{auto_retry_label}</b>（最长等待：{format_retry_cap_label(cap_hours)}）"
        )
        keyboard = [
            [
                InlineKeyboardButton("🔄 重载配置", callback_data="panel:reload"),
                InlineKeyboardButton("🗂 重置默认目录", callback_data="panel:reset_path"),
            ],
            [
                InlineKeyboardButton("🔑 115 扫码授权", callback_data="panel:auth"),
                InlineKeyboardButton(minimal_btn, callback_data="panel:toggle_minimal"),
            ],
            [
                InlineKeyboardButton(auto_retry_btn, callback_data="panel:toggle_auto_retry"),
                InlineKeyboardButton(f"⏱ 重试上限：{cap_short}", callback_data="panel:retry_cap"),
            ],
            [
                InlineKeyboardButton("⬅️ 上一页", callback_data="panel:page:1"),
            ],
        ]

    return text, InlineKeyboardMarkup(keyboard)


def build_help_text() -> str:
    """Build accurate command & usage reference (subscription/crawler features removed)."""
    return (
        f"<b>📖 Telegram-115Bot {BOT_VERSION} 完整使用说明</b>\n\n"
        "<b>🧭 控制面板与任务管理</b>\n"
        "• <code>/start</code> 或 <code>/menu</code> - 打开常驻底部主控面板\n"
        "• <code>/status</code> - 查看用户名、VIP、离线配额与重置时间、网盘容量及已配置下载目录\n"
        "• <code>/tasks</code> - 打开统一任务队列（已合并重试列表）：按 全部/进行中/待重试/已完成/已失败 筛选，点击任务查看逐条磁力明细、第几次尝试与下次自动重试时间\n"
        "• <code>/cloud_tasks</code> - 管理 115 官方云端磁力任务（分页浏览、单条删除、清空已完成/失败/全部）\n"
        "• <code>/log</code> - 导出并发送最新运行日志文件（<code>115bot.log</code>，极简模式下也保留历史日志）\n"
        "• <code>/minimal</code> - 切换「对话极简模式」（开启后除日志外仅保留最新 1 条结果消息）\n"
        "• <code>/help</code> - 查看本使用说明\n\n"
        "<b>📥 离线下载与批量任务</b>\n"
        "• <b>直接发送/转发链接</b>：支持 <code>magnet:</code> / <code>ed2k://</code> / <code>thunder://</code>（支持纯文本、图片配文、超链接实体及短时间多条消息合并）\n"
        "• <b>自动创建目录</b>：批量链接文本的首行+尾行非链接文字会自动作为 115 保存文件夹名\n"
        "• <b>直接发送链接文档</b>（<code>.txt</code> / <code>.csv</code> / <code>.html</code> / <code>.htm</code> / <code>.xlsx</code> / <code>.xls</code> / <code>.torrent</code>，无 500KB 限制）：自动提取其中的磁力、种子、ed2k、迅雷链接；当匹配链接超过 1 个时，会先回传 <code>原文件名+数量V+时间.txt</code> 供你确认，可直接一键下载至默认文件夹或选其他文件夹，若 30 秒内未操作将自动下载至默认文件夹（可点击「取消倒计时」按钮关闭本次自动下载）\n"
        "• <b>完成引用通知</b>：任务完成后自动发送结果汇总，并直接引用你添加该任务的原消息\n"
        "• <code>/reset_save_path</code> - 重置已记住的默认保存目录，下次下载重新选择分类\n"
        "• <code>/rl</code> - 直接打开任务队列的「待重试」筛选；自动重试开关默认关闭，可在面板第 2 页开启（开启时会发送完整说明），开启后未完成的磁力会按 2 倍退避自动重试直到达到最长等待上限（默认 24 小时，可选 2/4/8/16 天）\n\n"
        "<b>🎬 其他功能命令</b>\n"
        "• <code>/av 番号</code> - 自动检索番号磁力并提交离线下载\n"
        "• <code>/sync</code> - 同步指定目录并创建 <code>.strm</code> 软链（⚠️ 会清理目标目录旧文件）\n"
        "• <b>转发视频给机器人</b> - 选择保存目录后自动转存到 115 网盘\n\n"
        "<b>⚙️ 系统与会话管理</b>\n"
        "• <code>/auth</code> - 115 开放平台扫码授权（Token 失效或解除授权后使用）\n"
        "• <code>/reload</code> - 热重载 <code>config/config.yaml</code> 配置\n"
        "• <code>/q</code> - 取消当前交互会话\n\n"
        "<b>👀 表情反馈说明</b>\n"
        "• 发送消息或文件后立即标记 <code>👀</code>，处理完成后自动变为 <code>👌</code>（异常或无法识别为 <code>😢</code>）\n"
        "• 点击面板按钮时先标记 <code>👀</code>，完成后自动移除并将唯一主面板保持在最底部"
    )


def build_status_text(user_id: int) -> str:
    """Gather 115 account, quota, reset time, storage capacity, and download directories."""
    from app.handlers.download_handler import get_user_last_save_path

    lines = ["<b>📊 当前状态与账号信息</b>", ""]

    user_info = None
    quota_info = None
    if init.openapi_115 is not None:
        try:
            user_info = init.openapi_115.get_user_info()
            if isinstance(user_info, dict) and "user_name" in user_info:
                quota_info = init.openapi_115.get_quota_info()
        except Exception as e:
            init.logger.warn(f"状态查询获取115信息异常: {e}")

    if isinstance(user_info, dict) and "user_name" in user_info:
        user_name = html.escape(str(user_info.get("user_name", "未知")))
        uid = html.escape(str(user_info.get("user_id", "-")))
        vip_info = user_info.get("vip_info") or {}
        vip_level = html.escape(str(vip_info.get("level_name") or "普通用户"))
        vip_expire = _fmt_ts(vip_info.get("expire"))

        rt_space = user_info.get("rt_space_info") or {}
        total_space = html.escape(str(rt_space.get("all_total", {}).get("size_format", "未知")))
        used_space = html.escape(str(rt_space.get("all_use", {}).get("size_format", "未知")))
        remain_space = html.escape(str(rt_space.get("all_remain", {}).get("size_format", "未知")))

        lines.append("<b>👤 115 账号</b>")
        lines.append(f"• 用户名：<b>{user_name}</b>（ID: <code>{uid}</code>）")
        lines.append(f"• 会员等级：{vip_level}（到期：<code>{vip_expire}</code>）")
        lines.append("")
        lines.append("<b>💾 网盘容量</b>")
        lines.append(f"• 总容量：<code>{total_space}</code>｜已用：<code>{used_space}</code>｜剩余：<code>{remain_space}</code>")
    else:
        lines.append("<b>👤 115 账号</b>")
        lines.append("• ⚠️ 115 尚未授权或 Token 已失效，请到第 2 页点击「🔑 115 扫码授权」或发送 <code>/auth</code>。")

    lines.append("")
    lines.append("<b>🎫 离线下载份额</b>")
    if isinstance(quota_info, dict) and "count" in quota_info:
        total_q = quota_info.get("count", 0)
        used_q = quota_info.get("used", 0)
        surplus_q = quota_info.get("surplus", max(0, total_q - used_q))
        max_size = quota_info.get("max_size")
        size_hint = f"｜单次上限：<code>{max_size}</code>" if max_size else ""
        lines.append(f"• 剩余：<b>{surplus_q}</b> / {total_q}（已用 {used_q}）{size_hint}")
        reset_lines = extract_quota_reset_info(quota_info)
        if reset_lines:
            lines.append("• <b>配额重置 / 到期时间</b>：")
            lines.extend(reset_lines)
        else:
            lines.append("• 配额重置时间：暂未返回到期信息")
    else:
        lines.append("• 暂无法获取离线配额信息")

    lines.append("")
    lines.append("<b>📁 已设置的下载目录</b>")
    last_path = get_user_last_save_path(user_id)
    if last_path:
        lines.append(f"• 当前默认目录：<code>{html.escape(str(last_path))}</code>")
    else:
        lines.append("• 当前默认目录：<i>未记住（下次发送链接时选择）</i>")

    categories = init.bot_config.get("category_folder") or []
    if categories:
        for cat in categories:
            display = html.escape(str(cat.get("display_name") or cat.get("name") or "未命名分类"))
            for pm in cat.get("path_map") or []:
                pm_name = html.escape(str(pm.get("name") or ""))
                pm_path = html.escape(str(pm.get("path") or ""))
                label = f"{display}/{pm_name}" if pm_name and pm_name != display else display
                lines.append(f"• {label}：<code>{pm_path}</code>")
    else:
        lines.append("• 尚未在 config.yaml 中配置分类目录")

    lines.append(f"\n<i>查询时间：{datetime.now(TZ_CN).strftime('%H:%M:%S')}</i>")
    return "\n".join(lines)


def _get_local_batch_db_stats() -> Dict[str, int]:
    stats = {"pending_retry": 0, "dead": 0, "active_groups": 0, "offline_retry": 0}
    try:
        with SqlLiteLib() as sqlite:
            stats["pending_retry"] = sqlite.query_one(
                "SELECT COUNT(*) FROM batch_task_item WHERE status = 'failed'"
            ) or 0
            stats["dead"] = sqlite.query_one(
                "SELECT COUNT(*) FROM batch_task_item WHERE status = 'dead'"
            ) or 0
            stats["active_groups"] = sqlite.query_one(
                "SELECT COUNT(*) FROM batch_task_group WHERE status IN ('pending', 'partial')"
            ) or 0
            stats["offline_retry"] = sqlite.query_one(
                "SELECT COUNT(*) FROM offline_task WHERE is_download = 0"
            ) or 0
    except Exception as e:
        init.logger.debug(f"读取任务数据库统计失败: {e}")
    return stats


def _fetch_cloud_tasks_page(page: int = 1) -> Tuple[Optional[List[Dict]], int, int]:
    """Fetch a single page of 115 official cloud offline tasks."""
    if init.openapi_115 is None:
        return None, 0, 1
    try:
        page_data = init.openapi_115.get_offline_tasks_by_page(page=page)
        if isinstance(page_data, dict):
            tasks = page_data.get("tasks") or []
            total = int(page_data.get("count", len(tasks)) or 0)
            page_count = max(1, int(page_data.get("page_count", 1) or 1))
            return tasks, total, page_count
    except Exception as e:
        init.logger.warn(f"查询115云端离线任务第{page}页异常: {e}")
    return None, 0, 1


QUEUE_FILTERS = {
    "all": "全部",
    "run": "进行中",
    "retry": "待重试",
    "done": "已完成",
    "fail": "已失败",
}
QUEUE_BUTTON_LIMIT = 10
_QUEUE_ICONS = {"run": "⏳", "retry": "🔁", "done": "✅", "fail": "☠️"}


def _norm_queue_filter(key: Optional[str]) -> str:
    """Normalize queue filter keys (keeps backward compatibility with old 'tasks'/'retry' back tokens)."""
    if key in QUEUE_FILTERS:
        return key
    if key == "failed":
        return "fail"
    return "all"


def _fmt_wait(seconds) -> str:
    from app.utils.bot_settings import format_wait_duration
    return format_wait_duration(seconds)


def _load_queue_groups(limit: int = 200) -> List[Dict]:
    """Load persisted task groups (batch + single-link) with aggregated status & retry info (single SQL query)."""
    result: List[Dict] = []
    try:
        with SqlLiteLib() as sqlite:
            rows = sqlite.query_all(
                """
                SELECT g.group_id, g.folder_name, g.save_path, g.total_count, g.created_at,
                       SUM(CASE WHEN i.status = 'success' THEN 1 ELSE 0 END) AS s_cnt,
                       SUM(CASE WHEN i.status = 'failed' THEN 1 ELSE 0 END) AS f_cnt,
                       SUM(CASE WHEN i.status = 'dead' THEN 1 ELSE 0 END) AS d_cnt,
                       SUM(CASE WHEN i.status = 'pending' THEN 1 ELSE 0 END) AS p_cnt,
                       COUNT(i.id) AS item_cnt,
                       MAX(COALESCE(i.retry_count, 0)) AS max_rc,
                       MAX(CASE WHEN i.status IN ('failed', 'dead', 'pending') THEN COALESCE(i.retry_count, 0) END) AS max_rc_unfinished,
                       MIN(CASE WHEN i.status = 'failed' THEN i.next_retry_at END) AS next_at,
                       MAX(CASE WHEN i.status = 'failed' THEN i.current_wait END) AS cur_wait,
                       SUM(CASE WHEN LOWER(i.magnet) LIKE 'magnet:%' THEN 1 ELSE 0 END) AS k_magnet,
                       SUM(CASE WHEN LOWER(i.magnet) LIKE 'ed2k://%' THEN 1 ELSE 0 END) AS k_ed2k,
                       SUM(CASE WHEN LOWER(i.magnet) LIKE 'thunder://%' THEN 1 ELSE 0 END) AS k_thunder,
                       SUM(CASE WHEN LOWER(i.magnet) LIKE 'http%.torrent%' THEN 1 ELSE 0 END) AS k_torrent,
                       (SELECT magnet FROM batch_task_item WHERE group_id = g.group_id ORDER BY id ASC LIMIT 1) AS first_magnet,
                       (SELECT resource_name FROM batch_task_item WHERE group_id = g.group_id ORDER BY id ASC LIMIT 1) AS first_name
                FROM batch_task_group g
                LEFT JOIN batch_task_item i ON g.group_id = i.group_id
                GROUP BY g.group_id
                ORDER BY g.created_at DESC, g.id DESC
                LIMIT ?
                """,
                (limit,),
            ) or []
        for row in rows:
            s_cnt = int(row.get("s_cnt") or 0)
            f_cnt = int(row.get("f_cnt") or 0)
            d_cnt = int(row.get("d_cnt") or 0)
            p_cnt = int(row.get("p_cnt") or 0)
            item_cnt = int(row.get("item_cnt") or 0)
            if f_cnt:
                category = "retry"
            elif p_cnt:
                category = "run"
            elif d_cnt:
                category = "fail"
            else:
                category = "done"
            rc = row.get("max_rc_unfinished")
            if rc is None:
                rc = row.get("max_rc") or 0
            extracted = row.get("folder_name") or (row.get("first_name") if item_cnt == 1 else None)
            link_kinds = [
                kind for kind, col in (("magnet", "k_magnet"), ("ed2k", "k_ed2k"), ("thunder", "k_thunder"), ("torrent", "k_torrent"))
                if int(row.get(col) or 0) > 0
            ]
            if item_cnt > sum(int(row.get(c) or 0) for c in ("k_magnet", "k_ed2k", "k_thunder", "k_torrent")):
                link_kinds.append("other")
            display_name = format_retry_task_name(
                extracted,
                [row.get("first_magnet")] if row.get("first_magnet") else [],
                created_at=row.get("created_at"),
                link_count=item_cnt or int(row.get("total_count") or 1),
                link_kinds=link_kinds,
            )
            result.append({
                "group_id": row.get("group_id"),
                "display_name": display_name,
                "category": category,
                "s": s_cnt,
                "f": f_cnt,
                "d": d_cnt,
                "p": p_cnt,
                "total": max(int(row.get("total_count") or 0), item_cnt) or 1,
                "attempt": int(rc) + 1,
                "next_at": row.get("next_at"),
                "cur_wait": row.get("cur_wait"),
                "created_at": row.get("created_at"),
            })
    except Exception as e:
        if init.logger:
            init.logger.debug(f"读取任务队列记录失败: {e}")
    return result


def build_tasks_view(filter_key: str = "all") -> Tuple[str, InlineKeyboardMarkup]:
    """
    Unified Task Queue (merged with retry list):
    - text shows only summary counts (no duplicated task records);
    - every task is an interactive button showing success/total and current attempt number.
    """
    from app.utils.bot_settings import format_retry_cap_label, is_auto_retry_enabled

    fk = _norm_queue_filter(filter_key)
    mem_tasks = list_queue_tasks(limit=20)
    mem_running = [t for t in mem_tasks if t.get("status") == "running"]
    running_gids = {t.get("batch_group_id") for t in mem_running if t.get("batch_group_id")}
    mem_unlinked_finished = [
        t for t in mem_tasks if t.get("status") != "running" and not t.get("batch_group_id")
    ]
    groups = [g for g in _load_queue_groups() if g["group_id"] not in running_gids]

    # entries: (category, priority, label, callback_data)
    entries: List[Tuple[str, int, str, str]] = []
    for t in mem_running:
        kind_icon = "⬆️" if t.get("task_type") == "upload" else "⬇️"
        title = str(t.get("title", "任务"))[:18]
        label = f"⏳{kind_icon} {title} ({t.get('completed', 0)}/{t.get('total', 1)}·第1次)"
        entries.append(("run", 0, label, f"panel:td:{t['task_id']}:1:all"))
    for g in groups:
        cat = g["category"]
        mode = "failed" if cat in ("retry", "fail") else "all"
        label = f"{_QUEUE_ICONS[cat]} {g['display_name'][:20]} ({g['s']}/{g['total']}·第{g['attempt']}次)"
        priority = {"run": 0, "retry": 1}.get(cat, 2)
        entries.append((cat, priority, label, f"panel:bd:{g['group_id']}:1:{mode}:{fk}"))
    for t in mem_unlinked_finished:
        cat = "done" if t.get("status") == "completed" else "fail"
        kind_icon = "⬆️" if t.get("task_type") == "upload" else "⬇️"
        title = str(t.get("title", "任务"))[:18]
        label = f"{_QUEUE_ICONS[cat]}{kind_icon} {title} ({t.get('completed', 0)}/{t.get('total', 1)}·第1次)"
        entries.append((cat, 2, label, f"panel:td:{t['task_id']}:1:all"))

    entries.sort(key=lambda e: e[1])  # stable: keeps newest-first order inside the same priority
    counts = {key: sum(1 for e in entries if e[0] == key) for key in ("run", "retry", "done", "fail")}
    counts["all"] = len(entries)
    visible = [e for e in entries if fk == "all" or e[0] == fk]

    cloud_tasks, cloud_total, _ = _fetch_cloud_tasks_page(page=1)
    running_cloud = [t for t in (cloud_tasks or []) if t.get("status") in (0, 1)]

    lines = [
        f"<b>📥 任务队列</b>（筛选：<b>{QUEUE_FILTERS[fk]}</b>）",
        "",
        f"⚡ 进行中 <b>{counts['run']}</b>｜🔁 待重试 <b>{counts['retry']}</b>｜✅ 已完成 <b>{counts['done']}</b>｜☠️ 已失败 <b>{counts['fail']}</b>",
    ]
    if isinstance(cloud_tasks, list):
        lines.append(f"☁️ 115 云端任务：总计 <b>{cloud_total}</b> 条（首页进行中 <b>{len(running_cloud)}</b> 条）")
    else:
        lines.append("☁️ 115 云端任务：暂无法获取")
    if is_auto_retry_enabled():
        lines.append(f"⏱ 自动重试：<b>已开启</b>（每次重新添加磁力后等待时间翻倍，最长 <b>{format_retry_cap_label()}</b>）")
    else:
        lines.append("⏸ 自动重试：<b>已关闭</b>（可在面板第 2 页开启，或点任务详情手动重试）")
    lines.append("")

    if visible:
        shown = min(len(visible), QUEUE_BUTTON_LIMIT)
        extra = f"（显示最近 {shown}/{len(visible)} 条）" if len(visible) > shown else ""
        lines.append(f"<i>👇 点击下方任务查看逐条磁力明细、尝试次数与下次重试时间{extra}</i>")
    elif counts["all"] == 0:
        lines.append("<b>💡 下载 / 上传使用说明</b>")
        lines.append("• <b>离线下载</b>：直接发送/转发 <code>magnet:</code> / <code>.torrent</code> / <code>ed2k://</code> / <code>thunder://</code> 链接，或发送 <code>.txt</code> / <code>.csv</code> / <code>.html</code> / <code>.htm</code> / <code>Excel(.xlsx/.xls)</code> / <code>.torrent</code> 文件")
        lines.append("• <b>视频上传</b>：直接转发视频给机器人，选择保存目录即可转存至 115")
    else:
        lines.append(f"🈳 当前筛选（{QUEUE_FILTERS[fk]}）下没有任务")
    lines.append(f"\n<i>查询时间：{datetime.now(TZ_CN).strftime('%H:%M:%S')}</i>")

    def _filter_btn(key: str) -> InlineKeyboardButton:
        mark = "▶ " if key == fk else ""
        return InlineKeyboardButton(f"{mark}{QUEUE_FILTERS[key]} {counts[key]}", callback_data=f"panel:tq:{key}")

    keyboard: List[List[InlineKeyboardButton]] = [
        [_filter_btn("all"), _filter_btn("run"), _filter_btn("retry")],
        [_filter_btn("done"), _filter_btn("fail")],
    ]
    for _, _, label, cb in visible[:QUEUE_BUTTON_LIMIT]:
        keyboard.append([InlineKeyboardButton(label, callback_data=cb)])

    bottom = [
        InlineKeyboardButton("🔄 刷新", callback_data=f"panel:tq:{fk}"),
        InlineKeyboardButton("☁️ 115云端任务", callback_data="panel:cloud_edit:1"),
    ]
    keyboard.append(bottom)
    if fk in ("done", "fail") and counts[fk] > 0:
        keyboard.append([
            InlineKeyboardButton(
                "🗑 清空已完成记录" if fk == "done" else "🗑 清空已失败记录",
                callback_data=f"panel:tq_clear:{fk}",
            )
        ])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def clear_queue_groups(category: str) -> int:
    """Delete persisted task-group records of a finished category ('done' / 'fail'); never deletes cloud files."""
    if category not in ("done", "fail"):
        return 0
    from app.core.batch_task_manager import delete_batch_task_group
    removed = 0
    for g in _load_queue_groups(limit=1000):
        if g["category"] == category:
            delete_batch_task_group(g["group_id"])
            removed += 1
    return removed


def build_batch_group_detail_view(
    group_id: str,
    page: int = 1,
    mode: str = "all",
    back: str = "all",
) -> Tuple[str, InlineKeyboardMarkup]:
    """Build paginated per-magnet detail view for a persisted task group with attempt & next-retry info."""
    from app.core.batch_task_manager import (
        get_batch_task_group_info,
        get_batch_task_items,
        get_group_actual_path,
        get_group_display_name,
        summarize_group_items,
    )
    from app.utils.bot_settings import format_retry_cap_label, is_auto_retry_enabled

    fk = _norm_queue_filter(back)
    back_cb = f"panel:tq:{fk}"
    back_label = "⬅️ 返回任务队列"

    group_info = get_batch_task_group_info(group_id)
    if not group_info:
        return (
            "⚠️ 未找到该任务记录（可能已被删除）。",
            InlineKeyboardMarkup([[InlineKeyboardButton(back_label, callback_data=back_cb)]]),
        )

    items = get_batch_task_items(group_id)
    summary = summarize_group_items(items)
    display_name = get_group_display_name(group_info, items)

    mode = "failed" if mode == "failed" else "all"
    filtered = [it for it in items if it.get("status") in ("failed", "dead", "pending")] if mode == "failed" else items
    unfinished_cnt = summary["failed"] + summary["dead"] + summary["pending"]

    total_pages = max(1, (len(filtered) + TASK_DETAIL_PAGE_SIZE - 1) // TASK_DETAIL_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * TASK_DETAIL_PAGE_SIZE
    page_items = filtered[start:start + TASK_DETAIL_PAGE_SIZE]

    badge = {
        "run": "⏳ 进行中",
        "retry": "🔁 待自动重试",
        "done": "✅ 全部完成",
        "fail": "☠️ 已失败",
    }[summary["category"]]
    full_path = get_group_actual_path(group_info)
    cap_label = format_retry_cap_label()
    now_ts = int(time.time())

    lines = [
        f"<b>🔍 任务明细：{html.escape(display_name[:60])}</b>",
        f"• 状态：{badge}｜添加时间：<code>{_fmt_ts(group_info.get('created_at'))}</code>",
        f"• 统计：成功 <b>{summary['success']}</b>｜待重试 <b>{summary['failed']}</b>｜已失败 <b>{summary['dead']}</b>"
        + (f"｜进行中 <b>{summary['pending']}</b>" if summary["pending"] else "")
        + f"｜共 <b>{len(items) or group_info.get('total_count', 0)}</b>",
    ]
    if full_path:
        lines.append(f"• 目录：<code>{html.escape(full_path)}</code>")
    lines.append(f"• 🔄 第 {summary['attempt']} 次尝试任务")
    if summary["failed"]:
        if is_auto_retry_enabled():
            next_at = summary["next_retry_at"]
            if next_at and next_at > now_ts:
                lines.append(f"• ⏰ 下次自动重试：（{_fmt_wait(next_at - now_ts)}后）<code>{_fmt_ts(next_at)}</code>")
            else:
                lines.append("• ⏰ 下次自动重试：即将执行（等待调度器触发）")
            if summary["current_wait"]:
                lines.append(f"• ⏱ 本轮等待：{_fmt_wait(summary['current_wait'])}（每次翻倍，最长 {cap_label}）")
        else:
            lines.append("• ⏸ 自动重试：<b>已关闭</b>（可在面板第 2 页开启，或点击下方「🔄 立即重试」手动执行）")
    elif summary["dead"]:
        lines.append(f"• ⛔ 等待时间已达上限（{cap_label}）仍未完成，已标记失败")
    elif summary["pending"]:
        lines.append("• ⏳ 对话内正在跟踪下载进度")
    lines.append(f"• 视图：{'仅未完成项' if mode == 'failed' else '全部明细'}（第 {page}/{total_pages} 页）")
    lines.append("")

    if page_items:
        for idx, it in enumerate(page_items, start + 1):
            ist = it.get("status")
            attempt_no = int(it.get("retry_count") or 0) + 1
            item_name = format_retry_task_name(
                it.get("resource_name"),
                it.get("magnet") or "",
                created_at=it.get("created_at") or group_info.get("created_at"),
                link_count=1,
            )
            safe_name = html.escape(item_name[:56])
            if ist == "success":
                lines.append(f"{idx}. ✅ {safe_name}")
            elif ist == "failed":
                wait_hint = f"，本轮等待 {_fmt_wait(it.get('current_wait'))}" if it.get("current_wait") and it.get("last_retry_at") else ""
                lines.append(f"{idx}. 🔁 {safe_name}（第 {attempt_no} 次尝试{wait_hint}）")
            elif ist == "dead":
                lines.append(f"{idx}. ☠️ {safe_name}（第 {attempt_no} 次尝试后失败）")
            else:
                lines.append(f"{idx}. ⏳ {safe_name}（下载中）")
    else:
        lines.append("🈳 当前筛选条件下没有记录。")

    lines.append(f"\n<i>更新时间：{datetime.now(TZ_CN).strftime('%H:%M:%S')}</i>")

    keyboard: List[List[InlineKeyboardButton]] = []
    if total_pages > 1:
        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"panel:bd:{group_id}:{page - 1}:{mode}:{fk}"))
        nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data=f"panel:bd:{group_id}:{page}:{mode}:{fk}"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"panel:bd:{group_id}:{page + 1}:{mode}:{fk}"))
        keyboard.append(nav_row)

    toggle_mode = "all" if mode == "failed" else "failed"
    toggle_label = "📋 显示全部" if mode == "failed" else f"❌ 只看未完成({unfinished_cnt})"
    keyboard.append([
        InlineKeyboardButton("🔄 刷新明细", callback_data=f"panel:bd:{group_id}:{page}:{mode}:{fk}"),
        InlineKeyboardButton(toggle_label, callback_data=f"panel:bd:{group_id}:1:{toggle_mode}:{fk}"),
    ])
    action_row = []
    if summary["failed"] or summary["dead"]:
        action_row.append(InlineKeyboardButton("🔄 立即重试", callback_data=f"panel:bg_retry:{group_id}:{fk}"))
    action_row.append(InlineKeyboardButton("🗑 删除记录", callback_data=f"panel:bg_del:{group_id}:{fk}"))
    keyboard.append(action_row)
    keyboard.append([InlineKeyboardButton(back_label, callback_data=back_cb)])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def build_tasks_text() -> str:
    """Backward-compatible helper returning only the task queue HTML text."""
    text, _ = build_tasks_view()
    return text


def build_task_detail_view(task_id: str, page: int = 1, mode: str = "all") -> Tuple[str, InlineKeyboardMarkup]:
    """Build paginated per-magnet detail view for a specific queued or recent task."""
    task = get_task(task_id)
    if not task:
        return (
            "⚠️ 未找到该任务记录（可能已过期或机器人重启）。",
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回任务队列", callback_data="panel:tasks_inline")]]),
        )

    items = task.get("items") or []
    mode = "failed" if mode == "failed" else "all"
    filtered = [it for it in items if it.get("status") == "failed"] if mode == "failed" else items

    total_items = len(items)
    done_cnt = sum(1 for it in items if it.get("status") == "success")
    fail_cnt = sum(1 for it in items if it.get("status") == "failed")
    run_cnt = sum(1 for it in items if it.get("status") in ("running", "submitted", "pending"))

    total_pages = max(1, (len(filtered) + TASK_DETAIL_PAGE_SIZE - 1) // TASK_DETAIL_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * TASK_DETAIL_PAGE_SIZE
    page_items = filtered[start:start + TASK_DETAIL_PAGE_SIZE]

    status_badge = "⏳ 进行中" if task.get("status") == "running" else ("✅ 已完成" if task.get("status") == "completed" else "❌ 失败")
    end_ts = task.get("finished_at") or time.time()
    elapsed = _fmt_duration(end_ts - task.get("started_at", end_ts))

    lines = [
        f"<b>🔍 任务明细：{html.escape(str(task.get('title', '未命名')))}</b>",
        f"• 状态：{status_badge}｜阶段：{html.escape(str(task.get('stage', '')))}",
        f"• 统计：成功 <b>{done_cnt}</b>｜失败 <b>{fail_cnt}</b>｜进行中/待处理 <b>{run_cnt}</b>｜共 <b>{total_items or task.get('total', 0)}</b>",
        f"• 耗时：{elapsed}" + (f"｜目录：<code>{html.escape(str(task.get('save_path')))}</code>" if task.get("save_path") else ""),
        "• 🔄 第 1 次尝试任务（对话内实时跟踪；未完成项结束后自动转入后台翻倍重试）",
        f"• 视图：{'仅失败项' if mode == 'failed' else '全部明细'}（第 {page}/{total_pages} 页）",
        "",
    ]

    if page_items:
        for it in page_items:
            idx = it.get("index", 0)
            st = it.get("status", "pending")
            pct = it.get("percent", 0)
            name = html.escape(str(it.get("name") or it.get("link") or f"任务#{idx}")[:48])
            err = html.escape(str(it.get("error") or ""))
            if st == "success":
                lines.append(f"{idx}. ✅ {name}")
            elif st == "failed":
                err_part = f"（{err}）" if err else "（失败）"
                lines.append(f"{idx}. ❌ {name}{err_part}")
            elif st == "running":
                lines.append(f"{idx}. ⏳ {name}（<code>{pct}%</code>）")
            else:
                lines.append(f"{idx}. 🕒 {name}（等待中）")
    else:
        lines.append("🈳 当前筛选条件下没有记录。")

    lines.append(f"\n<i>更新时间：{datetime.now(TZ_CN).strftime('%H:%M:%S')}</i>")

    keyboard: List[List[InlineKeyboardButton]] = []
    if total_pages > 1:
        nav_row = []
        if page > 1:
            nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"panel:td:{task_id}:{page - 1}:{mode}"))
        nav_row.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data=f"panel:td:{task_id}:{page}:{mode}"))
        if page < total_pages:
            nav_row.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"panel:td:{task_id}:{page + 1}:{mode}"))
        keyboard.append(nav_row)

    toggle_mode = "all" if mode == "failed" else "failed"
    toggle_label = "📋 显示全部" if mode == "failed" else f"❌ 只看失败({fail_cnt})"
    keyboard.append([
        InlineKeyboardButton("🔄 刷新明细", callback_data=f"panel:td:{task_id}:{page}:{mode}"),
        InlineKeyboardButton(toggle_label, callback_data=f"panel:td:{task_id}:1:{toggle_mode}"),
    ])
    if task.get("batch_group_id"):
        keyboard.append([
            InlineKeyboardButton("🗂 查看重试记录", callback_data=f"panel:bd:{task['batch_group_id']}:1:all:all"),
        ])
    keyboard.append([
        InlineKeyboardButton("⬅️ 返回任务队列", callback_data="panel:tq:all"),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def build_cloud_tasks_view(chat_id: int, page: int = 1) -> Tuple[str, InlineKeyboardMarkup]:
    """Build 115 Official Cloud Offline Task Manager view with per-item delete & bulk clear."""
    page = max(1, int(page or 1))
    tasks, total_count, page_count = _fetch_cloud_tasks_page(page=page)
    state = get_chat_panel_state(chat_id)
    cache: Dict[str, Dict] = {}

    lines = [f"<b>☁️ 115 官方磁力任务列表管理（第 {page}/{page_count} 页）</b>", ""]

    if tasks is None:
        lines.append("⚠️ 无法获取 115 云端离线任务（可能未授权或网络异常，请检查 /auth）。")
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 重试", callback_data=f"panel:cloud_edit:{page}")]])
        return "\n".join(lines), markup

    running = [t for t in tasks if t.get("status") in (0, 1)]
    finished = [t for t in tasks if t.get("status") == 2]
    failed = [t for t in tasks if t.get("status") == -1]
    lines.append(
        f"• 云端总任务：<b>{total_count}</b> 条｜本页 {len(tasks)} 条（进行中 <b>{len(running)}</b>｜已完成 <b>{len(finished)}</b>｜失败 <b>{len(failed)}</b>）"
    )
    lines.append("")

    display_tasks = tasks[:CLOUD_PAGE_DISPLAY_LIMIT]
    if display_tasks:
        for idx, t in enumerate(display_tasks, 1):
            icon, status_text = _cloud_status_icon(t.get("status"), t.get("percentDone"))
            name = str(t.get("name") or "未命名任务")
            lines.append(f"{idx}. {icon} {html.escape(name[:46])}（<code>{status_text}</code>）")
            cache[str(idx)] = {
                "info_hash": t.get("info_hash", ""),
                "name": name,
                "status": t.get("status"),
                "percentDone": t.get("percentDone", 0),
                "url": t.get("url", ""),
                "page": page,
            }
        if len(tasks) > CLOUD_PAGE_DISPLAY_LIMIT:
            lines.append(f"<i>... 本页另有 {len(tasks) - CLOUD_PAGE_DISPLAY_LIMIT} 条，可使用下方批量清理或翻页</i>")
    else:
        lines.append("🈳 当前页没有任何云端离线任务。")

    state["cloud_cache"] = cache
    lines.append("\n<i>👇 点击序号按钮可管理单条任务，或使用下方一键清理：</i>")
    lines.append(f"<i>更新时间：{datetime.now(TZ_CN).strftime('%H:%M:%S')}</i>")

    keyboard: List[List[InlineKeyboardButton]] = []
    # 序号操作按钮（每行 5 个，紧凑直观）
    if display_tasks:
        row: List[InlineKeyboardButton] = []
        for idx in range(1, len(display_tasks) + 1):
            row.append(InlineKeyboardButton(f"管理 #{idx}", callback_data=f"panel:cloud_item:{page}:{idx}"))
            if len(row) == 5:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    # 分页与刷新
    nav_row: List[InlineKeyboardButton] = []
    if page > 1:
        nav_row.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"panel:cloud_edit:{page - 1}"))
    nav_row.append(InlineKeyboardButton("🔄 刷新", callback_data=f"panel:cloud_edit:{page}"))
    if page < page_count:
        nav_row.append(InlineKeyboardButton("➡️ 下一页", callback_data=f"panel:cloud_edit:{page + 1}"))
    keyboard.append(nav_row)

    # 官方批量清理按钮
    keyboard.append([
        InlineKeyboardButton("🧹 清空已完成", callback_data=f"panel:cloud_clear:4:{page}"),
        InlineKeyboardButton("🗑 清空失败任务", callback_data=f"panel:cloud_clear:2:{page}"),
    ])
    keyboard.append([
        InlineKeyboardButton("⚠️ 清空全部记录", callback_data=f"panel:cloud_clear_ask:1:{page}"),
        InlineKeyboardButton("📥 返回任务队列", callback_data="panel:tasks_inline"),
    ])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard)


def build_cloud_item_view(chat_id: int, page: int, idx_str: str) -> Tuple[str, InlineKeyboardMarkup]:
    """Build single 115 cloud task detail + delete options."""
    state = get_chat_panel_state(chat_id)
    item = (state.get("cloud_cache") or {}).get(str(idx_str))
    if not item or not item.get("info_hash"):
        return (
            "⚠️ 该云端任务缓存已刷新，请先返回列表刷新后重试。",
            InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回云端列表", callback_data=f"panel:cloud_edit:{page}")]]),
        )

    icon, status_text = _cloud_status_icon(item.get("status"), item.get("percentDone"))
    name = html.escape(str(item.get("name") or "未命名任务"))
    info_hash = html.escape(str(item.get("info_hash") or ""))
    url_preview = html.escape(str(item.get("url") or "")[:120])

    text = (
        f"<b>☁️ 115 云端任务详情 #{idx_str}</b>\n\n"
        f"• 名称：<b>{name}</b>\n"
        f"• 状态：{icon} <code>{status_text}</code>\n"
        f"• Hash：<code>{info_hash}</code>\n"
        + (f"• 链接：<code>{url_preview}</code>\n" if url_preview else "")
        + "\n请选择要执行的操作："
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 仅移除任务记录（保留已下文件）", callback_data=f"panel:cloud_del:{page}:{idx_str}:0")],
        [InlineKeyboardButton("⚠️ 删除任务并同时删除源文件", callback_data=f"panel:cloud_del:{page}:{idx_str}:1")],
        [InlineKeyboardButton("⬅️ 返回云端列表", callback_data=f"panel:cloud_edit:{page}")],
    ])
    return text, markup


def prepare_log_snapshot() -> Tuple[Optional[str], str, bool]:
    """Create a safe snapshot of 115bot.log in init.TEMP for sending via Telegram."""
    log_path = os.path.join(init.CONFIG, "115bot.log")
    if not os.path.exists(log_path):
        return None, "⚠️ 未找到日志文件（115bot.log）。", False

    file_size = os.path.getsize(log_path)
    if file_size == 0:
        return None, "ℹ️ 当前日志文件为空。", False

    os.makedirs(init.TEMP, exist_ok=True)
    ts_str = datetime.now(TZ_CN).strftime("%Y%m%d_%H%M%S")
    snapshot_path = os.path.join(init.TEMP, f"115bot_{ts_str}.log")
    truncated = False

    if file_size > MAX_TG_LOG_BYTES:
        truncated = True
        with open(log_path, "rb") as src, open(snapshot_path, "wb") as dst:
            src.seek(-TRUNCATED_LOG_TAIL_BYTES, os.SEEK_END)
            dst.write(src.read())
        caption = (
            f"📄 运行日志（原文件 {_fmt_size(file_size)} 超过限制，已截取最新 {_fmt_size(TRUNCATED_LOG_TAIL_BYTES)}）\n"
            f"导出时间：{datetime.now(TZ_CN).strftime('%Y-%m-%d %H:%M:%S')}"
        )
    else:
        shutil.copy2(log_path, snapshot_path)
        caption = (
            f"📄 机器人运行日志（{_fmt_size(file_size)}）\n"
            f"导出时间：{datetime.now(TZ_CN).strftime('%Y-%m-%d %H:%M:%S')}"
        )

    return snapshot_path, caption, truncated


async def _send_log_file(bot, chat_id: int) -> bool:
    """Send log file directly (never deleted by minimal mode so log exports can stack)."""
    snapshot_path, caption, _ = await asyncio.to_thread(prepare_log_snapshot)
    if not snapshot_path:
        await bot.send_message(chat_id=chat_id, text=caption)
        return False
    try:
        with open(snapshot_path, "rb") as f:
            await bot.send_document(
                chat_id=chat_id,
                document=f,
                filename=os.path.basename(snapshot_path),
                caption=caption,
            )
        return True
    finally:
        if os.path.exists(snapshot_path):
            try:
                os.remove(snapshot_path)
            except Exception:
                pass


def _build_retry_message() -> Tuple[str, InlineKeyboardMarkup]:
    """Backward-compatible alias: the retry list is now merged into the Task Queue (「待重试」filter)."""
    return build_tasks_view("retry")


def build_retry_cap_view() -> Tuple[str, InlineKeyboardMarkup]:
    """Panel view to toggle auto-retry and choose the retry max-wait cap (24h / 48h / 96h / 192h / 384h=16天)."""
    from app.utils.bot_settings import (
        RETRY_CAP_OPTIONS_HOURS,
        get_retry_max_wait_hours,
        format_retry_cap_label,
        is_auto_retry_enabled,
        build_retry_wait_chain_str,
    )
    current = get_retry_max_wait_hours()
    enabled = is_auto_retry_enabled()
    chain, attempt_count = build_retry_wait_chain_str(current)
    status_desc = "已开启" if enabled else "已关闭（默认关闭，开启后后台定时执行）"
    text = (
        "<b>⏱ 自动重试设置与最长等待上限</b>\n\n"
        f"• 自动重试开关：<b>{status_desc}</b>\n"
        f"• 当前最长上限：<b>{format_retry_cap_label(current)}</b>\n\n"
        "规则：开启自动重试后，每次重新添加磁力等待时间是上一次的 <b>2 倍</b>；"
        "当等待时间翻倍到上限且这一轮仍未完成时，标记为失败并引用原消息通知。\n\n"
        f"当前设置下的等待序列（共 {attempt_count} 次尝试）：\n<code>{chain}</code>\n\n"
        "可切换开关或选择新的上限："
    )
    buttons = [
        InlineKeyboardButton(
            ("✅ " if h == current else "") + format_retry_cap_label(h),
            callback_data=f"panel:retry_cap_set:{h}",
        )
        for h in RETRY_CAP_OPTIONS_HOURS
    ]
    keyboard = [
        [
            InlineKeyboardButton(
                "⏸ 关闭自动重试" if enabled else "🔁 开启自动重试",
                callback_data="panel:toggle_auto_retry_inline",
            )
        ]
    ]
    keyboard.extend([buttons[i:i + 2] for i in range(0, len(buttons), 2)])
    return text, InlineKeyboardMarkup(keyboard)


async def send_panel_result(
    bot,
    chat_id: int,
    text: str,
    *,
    parse_mode: Optional[str] = "html",
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    is_log: bool = False,
):
    """Send a result message while honoring minimal_mode (non-log messages replace the previous one)."""
    state = get_chat_panel_state(chat_id)
    if state.get("minimal_mode") and not is_log:
        prev_id = state.get("last_result_message_id")
        if prev_id:
            await _safe_delete_message(bot, chat_id, prev_id)
            state["last_result_message_id"] = None

    sent = await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=parse_mode,
        disable_web_page_preview=True,
        reply_markup=reply_markup,
    )
    if not is_log and sent and getattr(sent, "message_id", None):
        state["last_result_message_id"] = sent.message_id
    return sent


def _cancel_pending_panel_refresh(state: Dict) -> None:
    """Cancel any scheduled debounced bottom-panel refresh task for this chat."""
    task = state.pop("pending_panel_refresh_task", None)
    if task is not None and not task.done():
        try:
            task.cancel()
        except Exception:
            pass


def _get_panel_lock(state: Dict) -> asyncio.Lock:
    """Return an asyncio.Lock bound to the current running event loop for this chat."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    lock = state.get("panel_lock")
    lock_loop = state.get("panel_lock_loop")
    if lock is None or lock_loop is not loop:
        lock = asyncio.Lock()
        state["panel_lock"] = lock
        state["panel_lock_loop"] = loop
    return lock


async def ensure_bottom_panel(
    bot,
    chat_id: int,
    *,
    page: Optional[int] = None,
    extra_old_panel_id: Optional[int] = None,
):
    """Delete previous panel message(s) and post exactly ONE fresh panel at the very bottom."""
    state = get_chat_panel_state(chat_id)
    _cancel_pending_panel_refresh(state)
    lock = _get_panel_lock(state)
    async with lock:
        if page is not None:
            state["panel_page"] = 2 if page == 2 else 1

        old_ids = []
        if state.get("panel_message_id"):
            old_ids.append(state["panel_message_id"])
        if extra_old_panel_id and extra_old_panel_id not in old_ids:
            old_ids.append(extra_old_panel_id)

        for old_id in old_ids:
            deleted = await _safe_delete_message(bot, chat_id, old_id)
            if not deleted:
                await clear_message_reaction_safe(bot, chat_id, old_id)

        text, markup = build_home_panel(page=state["panel_page"], minimal_mode=state["minimal_mode"])
        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="html",
            disable_web_page_preview=True,
            reply_markup=markup,
        )
        state["panel_message_id"] = sent.message_id if sent and getattr(sent, "message_id", None) else None
        return sent


async def refresh_bottom_panel_if_exists(bot, chat_id: int) -> None:
    """If this chat already has a control panel, move it back to the very bottom."""
    if not bot or not chat_id:
        return
    state = get_chat_panel_state(chat_id)
    if state.get("panel_message_id"):
        await ensure_bottom_panel(bot, chat_id)


def schedule_bottom_panel_refresh(bot, chat_id: int, delay: Optional[float] = None) -> Optional[asyncio.Task]:
    """
    Debounced bottom-panel refresh: when multiple files arrive within `delay` seconds
    (defaults to `panel_refresh_debounce_sec`, 3.0s), coalesce them so the control panel
    is deleted and re-posted at the bottom ONLY ONCE.
    """
    if not bot or not chat_id:
        return None
    state = get_chat_panel_state(chat_id)
    if not state.get("panel_message_id"):
        return None

    wait_sec = get_panel_refresh_debounce_sec() if delay is None else max(0.0, float(delay))
    _cancel_pending_panel_refresh(state)

    async def _delayed():
        try:
            await asyncio.sleep(wait_sec)
            await refresh_bottom_panel_if_exists(bot, chat_id)
        except asyncio.CancelledError:
            return
        except Exception as e:
            if init.logger:
                init.logger.debug(f"延迟刷新底部面板失败 (chat={chat_id}): {e}")
        finally:
            if state.get("pending_panel_refresh_task") is asyncio.current_task():
                state.pop("pending_panel_refresh_task", None)

    try:
        task = asyncio.create_task(_delayed())
        state["pending_panel_refresh_task"] = task
        return task
    except RuntimeError:
        return None


async def _safe_edit_query_html(
    query,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    *,
    bot=None,
    chat_id: Optional[int] = None,
):
    """Edit text message in-place; if clicked message is media/document (no text), send a new result message."""
    msg = getattr(query, "message", None)
    # 文件/图片消息（例如带错误日志TXT的完成汇总）没有可编辑的 text，直接新发一条明细消息以保留原文件消息
    if msg is not None and not getattr(msg, "text", None) and bot is not None and chat_id is not None:
        await send_panel_result(bot, chat_id, text, reply_markup=reply_markup)
        await refresh_bottom_panel_if_exists(bot, chat_id)
        return True

    try:
        await query.edit_message_text(
            text=text,
            parse_mode="html",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
        return False
    except BadRequest as e:
        err = str(e)
        if "Message is not modified" in err:
            return False
        if "no text in the message to edit" in err.lower() and bot is not None and chat_id is not None:
            await send_panel_result(bot, chat_id, text, reply_markup=reply_markup)
            await refresh_bottom_panel_if_exists(bot, chat_id)
            return True
        raise


async def _check_authorized_message(update: Update) -> bool:
    if not update.effective_user:
        return False
    if not init.check_user(update.effective_user.id):
        if update.message:
            await update.message.reply_text("⚠️ 对不起，您无权使用115机器人！")
        return False
    return True


async def start_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start and /menu: open/refresh the single persistent panel at the bottom."""
    if not await _check_authorized_message(update):
        return
    chat_id = update.effective_chat.id
    await ensure_bottom_panel(context.bot, chat_id, page=1)
    if update.message:
        await set_message_reaction_safe(context.bot, chat_id, update.message.message_id, REACTION_DONE)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help: send full usage documentation and keep panel at the bottom."""
    if not await _check_authorized_message(update):
        return
    chat_id = update.effective_chat.id
    await send_panel_result(context.bot, chat_id, build_help_text())
    await refresh_bottom_panel_if_exists(context.bot, chat_id)
    if update.message:
        await set_message_reaction_safe(context.bot, chat_id, update.message.message_id, REACTION_DONE)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/status: send account/quota/reset/storage/directories and keep panel at the bottom."""
    if not await _check_authorized_message(update):
        return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    text = await asyncio.to_thread(build_status_text, user_id)
    await send_panel_result(context.bot, chat_id, text)
    await refresh_bottom_panel_if_exists(context.bot, chat_id)
    if update.message:
        await set_message_reaction_safe(context.bot, chat_id, update.message.message_id, REACTION_DONE)


async def tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/tasks: send interactive task queue with clickable magnet details."""
    if not await _check_authorized_message(update):
        return
    chat_id = update.effective_chat.id
    text, markup = await asyncio.to_thread(build_tasks_view)
    await send_panel_result(context.bot, chat_id, text, reply_markup=markup)
    await refresh_bottom_panel_if_exists(context.bot, chat_id)
    if update.message:
        await set_message_reaction_safe(context.bot, chat_id, update.message.message_id, REACTION_DONE)


async def cloud_tasks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cloud_tasks: open 115 official cloud offline task list manager."""
    if not await _check_authorized_message(update):
        return
    chat_id = update.effective_chat.id
    text, markup = await asyncio.to_thread(build_cloud_tasks_view, chat_id, 1)
    await send_panel_result(context.bot, chat_id, text, reply_markup=markup)
    await refresh_bottom_panel_if_exists(context.bot, chat_id)
    if update.message:
        await set_message_reaction_safe(context.bot, chat_id, update.message.message_id, REACTION_DONE)


async def log_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/log: send current 115bot.log file (never deleted by minimal mode) and keep panel at bottom."""
    if not await _check_authorized_message(update):
        return
    chat_id = update.effective_chat.id
    ok = False
    try:
        ok = await _send_log_file(context.bot, chat_id)
    except Exception as e:
        init.logger.error(f"发送日志文件失败: {e}")
        await send_panel_result(context.bot, chat_id, f"❌ 发送日志文件失败：{html.escape(str(e))}")
    await refresh_bottom_panel_if_exists(context.bot, chat_id)
    if update.message:
        await set_message_reaction_safe(
            context.bot,
            chat_id,
            update.message.message_id,
            REACTION_DONE if ok else REACTION_ERROR,
        )


async def minimal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/minimal: toggle minimal conversation mode."""
    if not await _check_authorized_message(update):
        return
    chat_id = update.effective_chat.id
    state = get_chat_panel_state(chat_id)
    state["minimal_mode"] = not state["minimal_mode"]
    tip = (
        "✨ 已开启<b>对话极简模式</b>：除获取日志外，新反馈会自动替换上一条消息。"
        if state["minimal_mode"]
        else "💬 已关闭<b>对话极简模式</b>：所有操作反馈消息均会保留。"
    )
    await send_panel_result(context.bot, chat_id, tip)
    await refresh_bottom_panel_if_exists(context.bot, chat_id)
    if update.message:
        await set_message_reaction_safe(context.bot, chat_id, update.message.message_id, REACTION_DONE)


async def reload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/reload: reload YAML configuration."""
    if not await _check_authorized_message(update):
        return
    chat_id = update.effective_chat.id
    try:
        init.load_yaml_config()
        init.logger.info("Reload configuration success:")
        init.logger.info(json.dumps(init.bot_config))
        await send_panel_result(context.bot, chat_id, "🔁 重载配置完成！")
        await refresh_bottom_panel_if_exists(context.bot, chat_id)
        if update.message:
            await set_message_reaction_safe(context.bot, chat_id, update.message.message_id, REACTION_DONE)
    except Exception as e:
        init.logger.error(f"重载配置失败: {e}")
        await send_panel_result(context.bot, chat_id, f"❌ 重载配置失败：{html.escape(str(e))}")
        if update.message:
            await set_message_reaction_safe(context.bot, chat_id, update.message.message_id, REACTION_ERROR)


async def handle_panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle panel:* callbacks; keep a single main panel at the bottom and edit sub-views cleanly."""
    query = update.callback_query
    if not query:
        return

    user_id = update.effective_user.id if update.effective_user else 0
    if not init.check_user(user_id):
        await query.answer("⚠️ 您无权操作此机器人", show_alert=True)
        return

    action = query.data or ""
    chat_id = update.effective_chat.id if update.effective_chat else (query.message.chat_id if query.message else None)
    clicked_msg_id = query.message.message_id if query.message else None

    await query.answer()
    if not chat_id:
        return

    state = get_chat_panel_state(chat_id)
    is_main_panel_action = action in (
        "panel:page:1", "panel:page:2", "panel:home",
        "panel:toggle_minimal", "panel:toggle_auto_retry", "panel:help", "panel:status",
        "panel:tasks", "panel:log", "panel:retry", "panel:reload",
        "panel:reset_path", "panel:auth", "panel:retry_cap",
    ) or action.startswith("panel:cloud:")

    if clicked_msg_id and is_main_panel_action and not state.get("panel_message_id"):
        state["panel_message_id"] = clicked_msg_id

    # 先给被点击的消息加上 👀 处理中表情
    if clicked_msg_id:
        await set_message_reaction_safe(context.bot, chat_id, clicked_msg_id, REACTION_PROCESSING)

    reposted_panel = False
    try:
        # 1. 主面板翻页（原地切换）
        if action in ("panel:page:1", "panel:page:2", "panel:home"):
            target_page = 2 if action == "panel:page:2" else 1
            state["panel_page"] = target_page
            text, markup = build_home_panel(page=target_page, minimal_mode=state["minimal_mode"])
            try:
                await _safe_edit_query_html(query, text, markup)
                if clicked_msg_id:
                    state["panel_message_id"] = clicked_msg_id
            except BadRequest:
                await ensure_bottom_panel(context.bot, chat_id, page=target_page, extra_old_panel_id=clicked_msg_id)
                reposted_panel = True
            return

        # 2. 对话极简开关 / 自动重试开关
        if action == "panel:toggle_minimal":
            state["minimal_mode"] = not state["minimal_mode"]
            tip = (
                "✨ 已开启<b>对话极简模式</b>：除获取日志外，其他结果消息仅保留最新一条。"
                if state["minimal_mode"]
                else "💬 已关闭<b>对话极简模式</b>：所有反馈消息均会保留。"
            )
            await send_panel_result(context.bot, chat_id, tip)
            await ensure_bottom_panel(context.bot, chat_id, extra_old_panel_id=clicked_msg_id)
            reposted_panel = True
            return

        if action == "panel:toggle_auto_retry":
            from app.utils.bot_settings import (
                is_auto_retry_enabled,
                set_auto_retry_enabled,
                build_auto_retry_guide_text,
            )
            new_enabled = await asyncio.to_thread(set_auto_retry_enabled, not is_auto_retry_enabled())
            init.logger.info(f"自动重试机制开关已切换为: {'开启' if new_enabled else '关闭'}")
            if new_enabled:
                tip = await asyncio.to_thread(build_auto_retry_guide_text)
            else:
                tip = (
                    "⏸ <b>已关闭自动重试机制</b>\n"
                    "后台定时任务将暂停自动重新提交未完成的磁力；你仍可在「📥 任务队列」点击任务详情中的「🔄 立即重试」手动执行。"
                )
            await send_panel_result(context.bot, chat_id, tip)
            await ensure_bottom_panel(context.bot, chat_id, extra_old_panel_id=clicked_msg_id)
            reposted_panel = True
            return

        if action == "panel:toggle_auto_retry_inline":
            from app.utils.bot_settings import (
                is_auto_retry_enabled,
                set_auto_retry_enabled,
                build_auto_retry_guide_text,
            )
            new_enabled = await asyncio.to_thread(set_auto_retry_enabled, not is_auto_retry_enabled())
            init.logger.info(f"自动重试机制开关（设置页）已切换为: {'开启' if new_enabled else '关闭'}")
            cap_text, cap_markup = await asyncio.to_thread(build_retry_cap_view)
            if new_enabled:
                guide_text = await asyncio.to_thread(build_auto_retry_guide_text)
                combined_text = f"{guide_text}\n\n{'─' * 16}\n\n{cap_text}"
            else:
                combined_text = (
                    "⏸ <b>已关闭自动重试机制</b>（后台将暂停自动重试，仍支持任务详情手动重试）。\n\n"
                    + cap_text
                )
            await _safe_edit_query_html(
                query,
                combined_text,
                cap_markup,
                bot=context.bot,
                chat_id=chat_id,
            )
            await refresh_bottom_panel_if_exists(context.bot, chat_id)
            return

        # 3. 帮助 / 账号状态
        if action == "panel:help":
            await send_panel_result(context.bot, chat_id, build_help_text())
            await ensure_bottom_panel(context.bot, chat_id, extra_old_panel_id=clicked_msg_id)
            reposted_panel = True
            return

        if action == "panel:status":
            text = await asyncio.to_thread(build_status_text, user_id)
            await send_panel_result(context.bot, chat_id, text)
            await ensure_bottom_panel(context.bot, chat_id, extra_old_panel_id=clicked_msg_id)
            reposted_panel = True
            return

        # 4. 任务队列（从主面板打开 vs 在任务消息内原地刷新/返回）
        if action == "panel:tasks":
            text, markup = await asyncio.to_thread(build_tasks_view)
            await send_panel_result(context.bot, chat_id, text, reply_markup=markup)
            await ensure_bottom_panel(context.bot, chat_id, extra_old_panel_id=clicked_msg_id)
            reposted_panel = True
            return

        if action == "panel:tasks_inline":
            text, markup = await asyncio.to_thread(build_tasks_view)
            await _safe_edit_query_html(query, text, markup, bot=context.bot, chat_id=chat_id)
            return

        if action.startswith("panel:tq_clear:"):
            fk = _norm_queue_filter(action.split(":")[2] if len(action.split(":")) > 2 else "")
            removed = await asyncio.to_thread(clear_queue_groups, fk)
            text, markup = await asyncio.to_thread(build_tasks_view, fk)
            label = "已完成" if fk == "done" else "已失败"
            await _safe_edit_query_html(
                query,
                f"🗑 已清空 {removed} 条{label}任务记录（不影响网盘文件）。\n\n" + text,
                markup,
                bot=context.bot,
                chat_id=chat_id,
            )
            return

        if action.startswith("panel:tq:"):
            fk = action.split(":")[2] if len(action.split(":")) > 2 else "all"
            text, markup = await asyncio.to_thread(build_tasks_view, fk)
            await _safe_edit_query_html(query, text, markup, bot=context.bot, chat_id=chat_id)
            return

        # 5. 点击任务查看逐条磁力明细（panel:td:<task_id>:<page>:<mode> / 持久化任务组 panel:bd:<group_id>:<page>:<mode>:<back>）
        if action.startswith("panel:td:"):
            parts = action.split(":")
            task_id = parts[2] if len(parts) > 2 else ""
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
            mode = parts[4] if len(parts) > 4 else "all"
            text, markup = await asyncio.to_thread(build_task_detail_view, task_id, page, mode)
            await _safe_edit_query_html(query, text, markup, bot=context.bot, chat_id=chat_id)
            return

        if action.startswith("panel:bd:"):
            parts = action.split(":")
            group_id = parts[2] if len(parts) > 2 else ""
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
            mode = parts[4] if len(parts) > 4 else "all"
            back = parts[5] if len(parts) > 5 else "tasks"
            text, markup = await asyncio.to_thread(build_batch_group_detail_view, group_id, page, mode, back)
            await _safe_edit_query_html(query, text, markup, bot=context.bot, chat_id=chat_id)
            return

        if action.startswith("panel:bg_retry:"):
            parts = action.split(":")
            group_id = parts[2] if len(parts) > 2 else ""
            back = parts[3] if len(parts) > 3 else "retry"
            await _safe_edit_query_html(
                query,
                f"⏳ 正在立即重试批量任务组 <code>{html.escape(group_id)}</code> 的失败项，请稍候（约 10~35 秒）...",
                InlineKeyboardMarkup([[InlineKeyboardButton("🔄 刷新明细", callback_data=f"panel:bd:{group_id}:1:failed:{back}")]]),
                bot=context.bot,
                chat_id=chat_id,
            )

            async def _run_group_retry():
                from app.core.batch_task_retry import retry_batch_group_now
                try:
                    res = await asyncio.to_thread(retry_batch_group_now, group_id)
                    prefix = ("✅ " if res.get("ok") else "❌ ") + html.escape(str(res.get("message") or "")) + "\n\n"
                    text, markup = await asyncio.to_thread(build_batch_group_detail_view, group_id, 1, "all", back)
                    await _safe_edit_query_html(query, prefix + text, markup, bot=context.bot, chat_id=chat_id)
                except Exception as e:
                    init.logger.error(f"立即重试任务组 {group_id} 失败: {e}", exc_info=True)
                    await _safe_edit_query_html(
                        query,
                        f"❌ 立即重试任务组失败：{html.escape(str(e))}",
                        InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ 返回明细", callback_data=f"panel:bd:{group_id}:1:all:{back}")]]),
                        bot=context.bot,
                        chat_id=chat_id,
                    )

            asyncio.create_task(_run_group_retry())
            return

        if action.startswith("panel:bg_del:"):
            parts = action.split(":")
            group_id = parts[2] if len(parts) > 2 else ""
            back = parts[3] if len(parts) > 3 else "retry"
            from app.core.batch_task_manager import delete_batch_task_group
            await asyncio.to_thread(delete_batch_task_group, group_id)
            text, markup = await asyncio.to_thread(build_tasks_view, _norm_queue_filter(back))
            await _safe_edit_query_html(
                query,
                f"🗑 已删除批量任务组记录 <code>{html.escape(group_id)}</code>。\n\n" + text,
                markup,
                bot=context.bot,
                chat_id=chat_id,
            )
            return

        # 6. 115 官方磁力任务列表管理（从主面板新发 vs 原地翻页/操作）
        if action.startswith("panel:cloud:"):
            page_str = action.split(":")[2] if len(action.split(":")) > 2 else "1"
            page = int(page_str) if page_str.isdigit() else 1
            text, markup = await asyncio.to_thread(build_cloud_tasks_view, chat_id, page)
            await send_panel_result(context.bot, chat_id, text, reply_markup=markup)
            await ensure_bottom_panel(context.bot, chat_id, extra_old_panel_id=clicked_msg_id)
            reposted_panel = True
            return

        if action.startswith("panel:cloud_edit:"):
            page_str = action.split(":")[2] if len(action.split(":")) > 2 else "1"
            page = int(page_str) if page_str.isdigit() else 1
            text, markup = await asyncio.to_thread(build_cloud_tasks_view, chat_id, page)
            await _safe_edit_query_html(query, text, markup)
            return

        if action.startswith("panel:cloud_item:"):
            parts = action.split(":")
            page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
            idx_str = parts[3] if len(parts) > 3 else ""
            text, markup = build_cloud_item_view(chat_id, page, idx_str)
            await _safe_edit_query_html(query, text, markup)
            return

        if action.startswith("panel:cloud_del:"):
            parts = action.split(":")
            page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
            idx_str = parts[3] if len(parts) > 3 else ""
            del_src = (parts[4] == "1") if len(parts) > 4 else False
            item = (state.get("cloud_cache") or {}).get(idx_str)
            if not item or not item.get("info_hash") or init.openapi_115 is None:
                await _safe_edit_query_html(
                    query,
                    "⚠️ 任务信息已失效或 115 未初始化，请刷新云端列表。",
                    InlineKeyboardMarkup([[InlineKeyboardButton("🔄 刷新列表", callback_data=f"panel:cloud_edit:{page}")]]),
                )
                return
            ok = await asyncio.to_thread(
                init.openapi_115.del_faild_offline_task,
                item["info_hash"],
                del_src,
            )
            text, markup = await asyncio.to_thread(build_cloud_tasks_view, chat_id, page)
            prefix = (
                f"✅ 已删除云端任务（{'含源文件' if del_src else '仅移除记录'}）：<code>{html.escape(item['name'][:40])}</code>\n\n"
                if ok
                else "❌ 删除该云端任务失败，请稍后重试。\n\n"
            )
            await _safe_edit_query_html(query, prefix + text, markup)
            return

        if action.startswith("panel:cloud_clear_ask:"):
            parts = action.split(":")
            flag = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
            confirm_text = (
                "⚠️ <b>确认清空 115 全部云端离线任务记录？</b>\n\n"
                "此操作会清空 115 云端所有离线任务记录（不删除已下载的网盘源文件）。"
            )
            confirm_markup = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ 确认清空全部记录", callback_data=f"panel:cloud_clear:{flag}:{page}"),
                    InlineKeyboardButton("取消", callback_data=f"panel:cloud_edit:{page}"),
                ]
            ])
            await _safe_edit_query_html(query, confirm_text, confirm_markup)
            return

        if action.startswith("panel:cloud_clear:"):
            parts = action.split(":")
            flag = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 4
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
            label_map = {1: "全部任务记录", 2: "失败任务", 4: "已完成任务"}
            label = label_map.get(flag, f"flag={flag}")
            ok = False
            if init.openapi_115 is not None:
                ok = bool(await asyncio.to_thread(init.openapi_115.clear_cloud_task, flag))
            text, markup = await asyncio.to_thread(build_cloud_tasks_view, chat_id, 1 if ok else page)
            prefix = f"✅ 已清空 115 云端{label}！\n\n" if ok else f"❌ 清空 115 云端{label}失败。\n\n"
            await _safe_edit_query_html(query, prefix + text, markup)
            return

        # 7. 日志导出（不受极简模式删除限制）
        if action == "panel:log":
            await _send_log_file(context.bot, chat_id)
            await ensure_bottom_panel(context.bot, chat_id, extra_old_panel_id=clicked_msg_id)
            reposted_panel = True
            return

        # 8. 重试列表（已合并到任务队列「待重试」筛选）/ 重试上限设置 / 重载 / 重置目录 / 授权
        if action == "panel:retry":
            text, markup = await asyncio.to_thread(build_tasks_view, "retry")
            await send_panel_result(context.bot, chat_id, text, reply_markup=markup)
            await ensure_bottom_panel(context.bot, chat_id, extra_old_panel_id=clicked_msg_id)
            reposted_panel = True
            return

        if action in ("panel:retry_inline", "panel:clear_retry"):
            text, markup = await asyncio.to_thread(build_tasks_view, "retry")
            await _safe_edit_query_html(query, text, markup, bot=context.bot, chat_id=chat_id)
            return

        if action == "panel:retry_cap":
            text, markup = await asyncio.to_thread(build_retry_cap_view)
            await send_panel_result(context.bot, chat_id, text, reply_markup=markup)
            await ensure_bottom_panel(context.bot, chat_id, extra_old_panel_id=clicked_msg_id)
            reposted_panel = True
            return

        if action.startswith("panel:retry_cap_set:"):
            from app.utils.bot_settings import set_retry_max_wait_hours, format_retry_cap_label
            hours_str = action.split(":")[2] if len(action.split(":")) > 2 else "24"
            new_hours = await asyncio.to_thread(set_retry_max_wait_hours, hours_str)
            init.logger.info(f"自动重试最长等待上限已设置为 {new_hours} 小时")
            text, markup = await asyncio.to_thread(build_retry_cap_view)
            await _safe_edit_query_html(
                query,
                f"✅ 已将自动重试最长等待上限设置为 <b>{format_retry_cap_label(new_hours)}</b>（对后续每轮重试立即生效）。\n\n" + text,
                markup,
                bot=context.bot,
                chat_id=chat_id,
            )
            await refresh_bottom_panel_if_exists(context.bot, chat_id)
            return

        if action == "panel:reload":
            init.load_yaml_config()
            init.logger.info("Reload configuration success (via panel)")
            await send_panel_result(context.bot, chat_id, "🔁 配置已重载完成！")
            await ensure_bottom_panel(context.bot, chat_id, extra_old_panel_id=clicked_msg_id)
            reposted_panel = True
            return

        if action == "panel:reset_path":
            from app.handlers.download_handler import clear_user_last_save_path
            cleared = clear_user_last_save_path(user_id)
            msg = "🔄 已重置您的默认保存目录，下次发送链接时将重新选择分类。" if cleared else "ℹ️ 当前尚未记住默认保存目录。"
            await send_panel_result(context.bot, chat_id, msg)
            await ensure_bottom_panel(context.bot, chat_id, extra_old_panel_id=clicked_msg_id)
            reposted_panel = True
            return

        if action == "panel:auth":
            from app.handlers.auth_handler import check_115_app_id
            if not check_115_app_id():
                await send_panel_result(context.bot, chat_id, "⚠️ 115 开放平台 AppID 未配置，请先检查 config.yaml。")
                await ensure_bottom_panel(context.bot, chat_id, extra_old_panel_id=clicked_msg_id)
                reposted_panel = True
                return
            if init.openapi_115 is None:
                await send_panel_result(context.bot, chat_id, "⚠️ 115 客户端尚未初始化，请检查配置后重启机器人。")
                await ensure_bottom_panel(context.bot, chat_id, extra_old_panel_id=clicked_msg_id)
                reposted_panel = True
                return
            await send_panel_result(context.bot, chat_id, "🔑 正在生成 115 授权二维码，请稍候并用 115 App 扫码确认...")
            await ensure_bottom_panel(context.bot, chat_id, extra_old_panel_id=clicked_msg_id)
            reposted_panel = True

            async def _run_auth():
                try:
                    await asyncio.to_thread(init.openapi_115.auth_pkce, user_id, init.bot_config["115_app_id"])
                    if init.openapi_115.access_token and init.openapi_115.refresh_token:
                        await send_panel_result(context.bot, chat_id, "✅ 115 扫码授权成功！")
                    else:
                        await send_panel_result(context.bot, chat_id, "⚠️ 授权未完成或二维码已过期，请重试。")
                except Exception as e:
                    init.logger.error(f"面板触发扫码授权失败: {e}")
                    await send_panel_result(context.bot, chat_id, f"❌ 授权失败：{html.escape(str(e))}")
                finally:
                    await refresh_bottom_panel_if_exists(context.bot, chat_id)

            asyncio.create_task(_run_auth())
    except Exception as e:
        init.logger.error(f"处理面板操作 {action} 失败: {e}", exc_info=True)
        await send_panel_result(context.bot, chat_id, f"❌ 操作失败：{html.escape(str(e))}")
        if is_main_panel_action:
            await ensure_bottom_panel(context.bot, chat_id, extra_old_panel_id=clicked_msg_id)
            reposted_panel = True
    finally:
        if not reposted_panel and clicked_msg_id:
            await clear_message_reaction_safe(context.bot, chat_id, clicked_msg_id)


async def handle_unrecognized_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Catch-all fallback in group=0 so every unrecognized message/file receives clear feedback."""
    msg = update.message
    if not msg or not update.effective_user or not update.effective_chat:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if not init.check_user(user_id):
        await msg.reply_text("⚠️ 对不起，您无权使用115机器人！")
        return

    doc = getattr(msg, "document", None)
    if doc:
        from app.utils.file_link_parser import is_supported_link_document
        if is_supported_link_document(doc):
            return

    text = (getattr(msg, "text", None) or "").strip()
    if text and not text.startswith("/") and context.user_data.get("rename_data"):
        return

    caption = (getattr(msg, "caption", None) or "").strip()
    if text.startswith("/"):
        cmd_token = html.escape(text.split()[0])
        feedback = (
            f"⚠️ 未识别的命令：<code>{cmd_token}</code>\n"
            "请发送 <code>/help</code> 查看全部可用命令，或直接点击下方控制面板。"
        )
    elif doc:
        doc_name = html.escape(str(getattr(doc, "file_name", None) or "未知文件"))
        feedback = (
            f"⚠️ 暂不支持处理该文件：<code>{doc_name}</code>\n"
            "目前支持发送 <b>.txt / .csv / .html / .htm / .xlsx / .xlsm / .xls / .torrent</b> 文件或直接转发视频。"
        )
    elif getattr(msg, "photo", None) or caption:
        feedback = (
            "⚠️ 已收到该媒体消息，但未在配文中检测到有效的下载链接。\n"
            "支持识别 <code>magnet:</code> / <code>ed2k://</code> / <code>thunder://</code> 链接。"
        )
    elif text:
        feedback = (
            "⚠️ 未能识别该消息内容（未检测到 <code>magnet:</code> / <code>ed2k://</code> / <code>thunder://</code> 下载链接，也不是有效命令）。\n"
            "请发送下载链接、<code>.txt</code> 文件，或发送 <code>/help</code> 查看使用说明。"
        )
    else:
        feedback = (
            "⚠️ 暂不支持处理该类型的消息。\n"
            "请发送下载链接、<code>.txt</code> 文件、视频，或点击下方控制面板。"
        )

    await send_panel_result(context.bot, chat_id, feedback)
    await refresh_bottom_panel_if_exists(context.bot, chat_id)
    await set_message_reaction_safe(context.bot, chat_id, msg.message_id, REACTION_ERROR)


def register_menu_handlers(application):
    """Register main control dialog, status, tasks, cloud tasks, log, minimal toggle, help, and reload."""
    application.add_handler(CommandHandler(["start", "menu"], start_panel_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("tasks", tasks_command))
    application.add_handler(CommandHandler("cloud_tasks", cloud_tasks_command))
    application.add_handler(CommandHandler("log", log_command))
    application.add_handler(CommandHandler("minimal", minimal_command))
    application.add_handler(CommandHandler("reload", reload_command))
    application.add_handler(CallbackQueryHandler(handle_panel_callback, pattern=r"^panel:"))
    init.logger.info("✅ Menu/Status/Tasks/CloudTasks/Log 面板处理器已注册")


def register_fallback_handlers(application):
    """Register catch-all handler LAST in group=0 so no message is silently dropped."""
    application.add_handler(MessageHandler(filters.ALL, handle_unrecognized_message))
    init.logger.info("✅ 全局兜底消息反馈处理器已注册")
