# -*- coding: utf-8 -*-
"""Web UI & REST API configuration helpers."""

import os
from typing import Dict

import init
from app.utils.bot_settings import get_setting, set_setting, _parse_bool_flag

DEFAULT_WEB_ENABLED = True
DEFAULT_WEB_HOST = "0.0.0.0"
DEFAULT_WEB_PORT = 8115
DEFAULT_WEB_USERNAME = "admin"
DEFAULT_NOTIFY_TG_ON_WEB_DOWNLOAD = True

NOTIFY_TG_SETTING_KEY = "web_notify_tg_on_download"


def get_web_config() -> Dict:
    """Return merged web config from config.yaml `web:` section with optional environment overrides."""
    cfg = getattr(init, "bot_config", None) or {}
    raw = cfg.get("web") if isinstance(cfg, dict) else {}
    if not isinstance(raw, dict):
        raw = {}

    enabled = _parse_bool_flag(
        os.getenv("WEB_ENABLED", raw.get("enabled", DEFAULT_WEB_ENABLED)),
        DEFAULT_WEB_ENABLED,
    )
    host = str(os.getenv("WEB_HOST", raw.get("host", DEFAULT_WEB_HOST)) or DEFAULT_WEB_HOST).strip()
    try:
        port = int(os.getenv("WEB_PORT", raw.get("port", DEFAULT_WEB_PORT)))
    except (TypeError, ValueError):
        port = DEFAULT_WEB_PORT

    username = str(os.getenv("WEB_USERNAME", raw.get("username", DEFAULT_WEB_USERNAME)) or DEFAULT_WEB_USERNAME).strip()
    password = str(os.getenv("WEB_PASSWORD", raw.get("password", "")) or "").strip()
    api_key = str(os.getenv("WEB_API_KEY", raw.get("api_key", "")) or "").strip()
    notify_tg_default = _parse_bool_flag(
        raw.get("notify_tg_on_web_download", DEFAULT_NOTIFY_TG_ON_WEB_DOWNLOAD),
        DEFAULT_NOTIFY_TG_ON_WEB_DOWNLOAD,
    )
    cors_origins = raw.get("cors_origins") or []
    if isinstance(cors_origins, str):
        cors_origins = [x.strip() for x in cors_origins.split(",") if x.strip()]

    return {
        "enabled": enabled,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "api_key": api_key,
        "notify_tg_on_web_download": notify_tg_default,
        "cors_origins": cors_origins,
    }


def is_web_enabled() -> bool:
    return bool(get_web_config().get("enabled", DEFAULT_WEB_ENABLED))


def get_notify_tg_on_web_download() -> bool:
    """Return whether downloads submitted via Web/API also push a completion summary to Telegram."""
    persisted = get_setting(NOTIFY_TG_SETTING_KEY)
    if persisted is not None:
        return _parse_bool_flag(persisted, DEFAULT_NOTIFY_TG_ON_WEB_DOWNLOAD)
    return bool(get_web_config().get("notify_tg_on_web_download", DEFAULT_NOTIFY_TG_ON_WEB_DOWNLOAD))


def set_notify_tg_on_web_download(enabled: bool) -> bool:
    flag = bool(enabled)
    set_setting(NOTIFY_TG_SETTING_KEY, "1" if flag else "0")
    return flag
