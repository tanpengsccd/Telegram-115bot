# -*- coding: utf-8 -*-
"""Persistent bot settings (stored in SQLite) such as the retry max-wait cap configurable from the panel."""

import time
from typing import Optional, Tuple

import init
from app.utils.sqlitelib import SqlLiteLib

# 第一次（对话内）等待时长：与对话内 10 分钟无进度早停一致；之后每次重新添加磁力等待时间翻倍
BASE_RETRY_WAIT_SECONDS = 600
# 面板可选的「重试最长等待上限」（小时）：24h → 48h → 96h → 192h(8天) → 384h(16天)
RETRY_CAP_OPTIONS_HOURS = (24, 48, 96, 192, 384)
DEFAULT_RETRY_MAX_WAIT_HOURS = 24
RETRY_CAP_SETTING_KEY = "retry_max_wait_hours"
AUTO_RETRY_SETTING_KEY = "auto_retry_enabled"
DEFAULT_AUTO_RETRY_ENABLED = False


def ensure_settings_table(sqlite) -> None:
    sqlite.execute_sql(
        """
        CREATE TABLE IF NOT EXISTS bot_setting (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at INTEGER
        )
        """
    )


def _settings_table_exists(sqlite) -> bool:
    return bool(sqlite.query_one(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'bot_setting'"
    ))


def get_setting(key: str, default=None):
    try:
        with SqlLiteLib() as sqlite:
            if not _settings_table_exists(sqlite):
                return default
            rows = sqlite.query_all("SELECT value FROM bot_setting WHERE key = ?", (key,))
            return rows[0]["value"] if rows else default
    except Exception:
        return default


def set_setting(key: str, value) -> None:
    with SqlLiteLib() as sqlite:
        ensure_settings_table(sqlite)
        sqlite.execute_sql(
            "INSERT OR REPLACE INTO bot_setting (key, value, updated_at) VALUES (?, ?, ?)",
            (key, str(value), int(time.time())),
        )


def delete_setting(key: str) -> bool:
    try:
        with SqlLiteLib() as sqlite:
            if not _settings_table_exists(sqlite):
                return False
            exists = sqlite.query_one("SELECT COUNT(*) FROM bot_setting WHERE key = ?", (key,)) or 0
            if exists:
                sqlite.execute_sql("DELETE FROM bot_setting WHERE key = ?", (key,))
                return True
    except Exception:
        pass
    return False


def delete_settings_by_prefix(prefix: str) -> bool:
    try:
        with SqlLiteLib() as sqlite:
            if not _settings_table_exists(sqlite):
                return False
            exists = sqlite.query_one("SELECT COUNT(*) FROM bot_setting WHERE key LIKE ?", (f"{prefix}%",)) or 0
            if exists:
                sqlite.execute_sql("DELETE FROM bot_setting WHERE key LIKE ?", (f"{prefix}%",))
                return True
    except Exception:
        pass
    return False


def normalize_retry_cap_hours(value) -> int:
    """Snap any value to the nearest allowed option (24h..384h)."""
    try:
        hours = int(float(value))
    except (TypeError, ValueError):
        return DEFAULT_RETRY_MAX_WAIT_HOURS
    for option in RETRY_CAP_OPTIONS_HOURS:
        if hours <= option:
            return option
    return RETRY_CAP_OPTIONS_HOURS[-1]


def get_retry_max_wait_hours() -> int:
    value = get_setting(RETRY_CAP_SETTING_KEY)
    if value is None:
        cfg = getattr(init, "bot_config", None) or {}
        value = cfg.get(RETRY_CAP_SETTING_KEY, DEFAULT_RETRY_MAX_WAIT_HOURS) if isinstance(cfg, dict) else DEFAULT_RETRY_MAX_WAIT_HOURS
    return normalize_retry_cap_hours(value)


def get_retry_max_wait_seconds() -> int:
    return get_retry_max_wait_hours() * 3600


def set_retry_max_wait_hours(hours) -> int:
    normalized = normalize_retry_cap_hours(hours)
    set_setting(RETRY_CAP_SETTING_KEY, normalized)
    return normalized


def format_retry_cap_label(hours: Optional[int] = None) -> str:
    hours = normalize_retry_cap_hours(hours if hours is not None else get_retry_max_wait_hours())
    if hours <= 24:
        return f"{hours}小时"
    return f"{hours}小时（{hours // 24}天）"


def format_wait_duration(seconds) -> str:
    """Human readable duration, e.g. '1天2小时', '1小时20分', '20分钟'."""
    try:
        seconds = max(0, int(seconds or 0))
    except (TypeError, ValueError):
        seconds = 0
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}天{hours}小时" if hours else f"{days}天"
    if hours:
        return f"{hours}小时{minutes}分" if minutes else f"{hours}小时"
    if minutes:
        return f"{minutes}分钟"
    return "不到1分钟"


def _parse_bool_flag(raw, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if text in ("1", "true", "yes", "on", "enable", "enabled"):
        return True
    if text in ("0", "false", "no", "off", "disable", "disabled"):
        return False
    return default


def is_auto_retry_enabled() -> bool:
    """Return whether background automatic retry is enabled (defaults to False)."""
    value = get_setting(AUTO_RETRY_SETTING_KEY)
    if value is None:
        cfg = getattr(init, "bot_config", None) or {}
        if isinstance(cfg, dict) and AUTO_RETRY_SETTING_KEY in cfg:
            return _parse_bool_flag(cfg.get(AUTO_RETRY_SETTING_KEY), DEFAULT_AUTO_RETRY_ENABLED)
        return DEFAULT_AUTO_RETRY_ENABLED
    return _parse_bool_flag(value, DEFAULT_AUTO_RETRY_ENABLED)


def set_auto_retry_enabled(enabled: bool) -> bool:
    flag = bool(enabled)
    set_setting(AUTO_RETRY_SETTING_KEY, "1" if flag else "0")
    return flag


def build_retry_wait_chain_str(cap_hours: Optional[int] = None) -> Tuple[str, int]:
    """Return (formatted doubling wait chain string, total attempt count) for a cap."""
    hours = normalize_retry_cap_hours(cap_hours if cap_hours is not None else get_retry_max_wait_hours())
    cap_seconds = hours * 3600
    waits = []
    wait = BASE_RETRY_WAIT_SECONDS
    while True:
        waits.append(wait)
        if wait >= cap_seconds:
            break
        wait = min(wait * 2, cap_seconds)
    return " → ".join(format_wait_duration(w) for w in waits), len(waits)


def build_auto_retry_guide_text(cap_hours: Optional[int] = None) -> str:
    """Build detailed HTML usage guide sent whenever the user enables auto-retry."""
    hours = normalize_retry_cap_hours(cap_hours if cap_hours is not None else get_retry_max_wait_hours())
    chain, attempt_count = build_retry_wait_chain_str(hours)
    return (
        "🔁 <b>已开启自动重试机制</b>\n\n"
        "<b>📖 自动重试使用说明：</b>\n"
        "1. <b>触发条件</b>：离线任务在对话内超时，或连续 <b>10 分钟</b>无进度变化时，会自动转入后台重试队列。\n"
        "2. <b>2 倍指数退避</b>：每轮自动重新向 115 提交未完成的磁力，且本轮等待时间是上一轮的 <b>2 倍</b>，尽量减少 115 接口调用频率。\n"
        f"3. <b>当前最长等待上限</b>：<b>{format_retry_cap_label(hours)}</b>（共 {attempt_count} 次尝试）\n"
        f"   等待序列：<code>{chain}</code>\n"
        "4. <b>结果通知</b>：后台补全成功或达到上限最终失败时，都会自动发送通知并<b>引用你最初添加任务的消息</b>（无新增进展时不刷屏）。\n"
        "5. <b>随时管理</b>：在「📥 任务队列 → 待重试」可查看每条任务是第几次尝试、下次重试时间，也可随时手动点「🔄 立即重试」或在本面板调整上限 / 关闭开关。"
    )
