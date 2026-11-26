#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone pure Web UI + REST API entry point (no Telegram bot token required)."""

import os
import sys

# Ensure app/ and project root are on sys.path
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_APP_DIR)
for _p in (_APP_DIR, _ROOT_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import uvicorn

import init
from app.core.scheduler import start_scheduler_in_thread
from app.web.app import create_web_app
from app.web.auth import ensure_web_credentials
from app.web.config import get_web_config


def main():
    init.load_yaml_config()
    init.create_logger()
    init.create_tmp()
    init.init_db()
    init.initialize_115open()  # Non-fatal if token expired; Web UI allows QR authorization
    start_scheduler_in_thread()

    ensure_web_credentials()
    cfg = get_web_config()
    host = cfg["host"]
    port = cfg["port"]

    if init.logger:
        init.logger.info(f"🌐 独立 Web/API 模式启动中: http://{host}:{port}（Swagger 文档: /docs）")

    app = create_web_app()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
