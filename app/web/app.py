# -*- coding: utf-8 -*-
"""FastAPI application assembly, static PWA serving, and embedded Uvicorn thread launcher."""

import os
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

import init
from app.handlers.menu_handler import BOT_VERSION
from app.web.auth import ensure_web_credentials
from app.web.config import get_web_config, is_web_enabled
from app.web.routes import router as api_router

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_web_thread: Optional[threading.Thread] = None
_web_server = None


def create_web_app() -> FastAPI:
    app = FastAPI(
        title="Telegram-115Bot Web & API",
        description="Telegram-115Bot 统一管理后台与外部 REST API（支持 Web 登录与 API Key 鉴权）",
        version=BOT_VERSION,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    cfg = get_web_config()
    if cfg.get("cors_origins"):
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg["cors_origins"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(api_router)

    assets_dir = os.path.join(STATIC_DIR, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="web-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint not found")

        # Serve root-level static assets (manifest.webmanifest, sw.js, icons, favicon)
        if full_path:
            safe_rel = os.path.normpath(full_path).lstrip(os.sep)
            candidate = os.path.join(STATIC_DIR, safe_rel)
            if (
                os.path.abspath(candidate).startswith(os.path.abspath(STATIC_DIR))
                and os.path.isfile(candidate)
            ):
                return FileResponse(candidate)

        index_file = os.path.join(STATIC_DIR, "index.html")
        if os.path.isfile(index_file):
            return FileResponse(index_file)

        return HTMLResponse(
            "<html><body style='font-family:sans-serif;padding:2rem'>"
            "<h2>Telegram-115Bot Web API 已运行</h2>"
            "<p>前端静态文件尚未构建到 <code>app/web/static/</code>。可先访问 <a href='/docs'>/docs</a> 调试 REST API。</p>"
            "</body></html>",
            status_code=200,
        )

    return app


def start_web_server_in_thread() -> Optional[threading.Thread]:
    """Start embedded Uvicorn server inside a daemon thread (used when running via app/115bot.py)."""
    global _web_thread, _web_server

    if not is_web_enabled():
        if init.logger:
            init.logger.info("Web/API 服务未启用（web.enabled=false）")
        return None

    if _web_thread is not None and _web_thread.is_alive():
        return _web_thread

    ensure_web_credentials()
    cfg = get_web_config()
    host = cfg["host"]
    port = cfg["port"]

    import uvicorn

    web_app = create_web_app()
    uv_config = uvicorn.Config(
        web_app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(uv_config)
    # Prevent Uvicorn from overriding main thread's SIGINT/SIGTERM handlers
    server.install_signal_handlers = lambda: None
    _web_server = server

    def _run():
        try:
            if init.logger:
                init.logger.info(f"🌐 Web 管理端与 REST API 已启动: http://{host}:{port}（文档: /docs）")
            server.run()
        except Exception as e:
            if init.logger:
                init.logger.error(f"Web/API 服务运行异常: {e}", exc_info=True)

    _web_thread = threading.Thread(target=_run, name="web-api-server", daemon=True)
    _web_thread.start()
    return _web_thread
