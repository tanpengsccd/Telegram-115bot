# -*- coding: utf-8 -*-
"""Persistent bot settings (stored in SQLite) such as the retry max-wait cap configurable from the panel."""

import time
from typing import Optional

import init
from app.utils.sqlitelib import SqlLiteLib

# 第一次（对话内）等待时长：与对话内 10 分钟无进度早停一致；之后每次重新添加磁力等待时间翻倍
BASE_RETRY_WAIT_SECONDS = 600
# 面板可选的「重试最长等待上限」（小时）：24h → 48h → 96h → 192h(8天) → 384h(16天)
RETRY_CAP_OPTIONS_HOURS = (24, 48, 96, 192, 384)
DEFAULT_RETRY_MAX_WAIT_HOURS = 24
RETRY_CAP_SETTING_KEY = "retry_max_wait_hours"


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
