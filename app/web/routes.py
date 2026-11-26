# -*- coding: utf-8 -*-
"""REST API v1 routes & Server-Sent Events (SSE) streams."""

import asyncio
import json
import os
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.utils.task_tracker import get_tracker_version
from app.web.auth import (
    authenticate_user,
    clear_session_cookie,
    get_web_username,
    require_auth,
    require_auth_sse,
    rotate_api_key,
    set_session_cookie,
    update_web_password,
)
from app.web.qr_auth import get_qr_session_status, start_qr_session
from app.web.services import (
    clear_cloud_tasks_payload,
    clear_groups_payload,
    delete_cloud_task_payload,
    delete_group_payload,
    get_cloud_tasks_payload,
    get_group_detail_payload,
    get_live_task_payload,
    get_log_file_path,
    get_settings_payload,
    get_status_payload,
    list_unified_tasks_payload,
    parse_raw_text_payload,
    parse_uploaded_file_payload,
    read_log_tail,
    reload_config_payload,
    reset_default_path_payload,
    retry_group_payload,
    submit_download_payload,
    update_settings_payload,
)

router = APIRouter(prefix="/api/v1")


# ── Pydantic Request Schemas ─────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., description="Web 登录用户名")
    password: str = Field(..., description="Web 登录密码")


class ParseTextRequest(BaseModel):
    raw_text: str = Field(..., description="包含磁力/ed2k/迅雷/种子链接的原始文本")


class SubmitDownloadRequest(BaseModel):
    links: Optional[List[str]] = Field(default=None, description="待提交的链接列表")
    raw_text: Optional[str] = Field(default=None, description="或直接传入包含链接与标题行的原始文本")
    save_path: Optional[str] = Field(default=None, description="115 保存目录（省略时使用默认目录）")
    custom_folder_name: Optional[str] = Field(default=None, description="批量任务子目录名（可选）")
    notify_tg: Optional[bool] = Field(default=None, description="任务完成后是否同步发通知到 Telegram（省略时跟随全局设置）")
    remember_path: bool = Field(default=True, description="是否将本次目录记住为默认目录")


class ClearCloudRequest(BaseModel):
    flag: int = Field(..., description="1=全部任务记录, 2=失败任务, 4=已完成任务")


class UpdateSettingsRequest(BaseModel):
    auto_retry_enabled: Optional[bool] = None
    retry_max_wait_hours: Optional[int] = None
    notify_tg_on_web_download: Optional[bool] = None
    default_save_path: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6)
    new_username: Optional[str] = None


# ── Public Health & Auth ─────────────────────────────────────────────────────

@router.get("/health", tags=["System"])
async def health():
    return {"ok": True, "ts": int(time.time())}


@router.post("/auth/login", tags=["Auth"])
async def login(body: LoginRequest, response: Response):
    if not authenticate_user(body.username, body.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    username = get_web_username()
    set_session_cookie(response, username)
    return {"ok": True, "username": username}


@router.post("/auth/logout", tags=["Auth"])
async def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/auth/me", tags=["Auth"])
async def auth_me(user: Dict = Depends(require_auth)):
    return {"authenticated": True, "username": user["username"], "auth_type": user["auth_type"]}


# ── System Status & 115 QR PKCE Auth ─────────────────────────────────────────

@router.get("/status", tags=["System"])
async def api_status(_: Dict = Depends(require_auth)):
    return await asyncio.to_thread(get_status_payload)


@router.post("/115/qr/start", tags=["115 QR Auth"])
async def api_qr_start(_: Dict = Depends(require_auth)):
    try:
        return await asyncio.to_thread(start_qr_session)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/115/qr/{session_id}", tags=["115 QR Auth"])
async def api_qr_status(session_id: str, _: Dict = Depends(require_auth)):
    data = get_qr_session_status(session_id)
    if not data:
        raise HTTPException(status_code=404, detail="扫码会话不存在或已过期")
    return data


# ── Downloads & Multi-Format File Parsing ────────────────────────────────────

@router.post("/files/parse", tags=["Downloads & Files"])
async def api_parse_file(
    file: UploadFile = File(...),
    _: Dict = Depends(require_auth),
):
    content = await file.read()
    try:
        return await asyncio.to_thread(
            parse_uploaded_file_payload,
            file.filename or "upload.txt",
            content,
            file.content_type or "",
        )
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="无法解析文件编码（支持 UTF-8 / GB18030 / GBK / UTF-16）")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"解析文件失败: {e}")


@router.post("/files/submit", tags=["Downloads & Files"])
async def api_submit_file(
    file: UploadFile = File(...),
    save_path: Optional[str] = Form(default=None),
    custom_folder_name: Optional[str] = Form(default=None),
    notify_tg: Optional[bool] = Form(default=None),
    _: Dict = Depends(require_auth),
):
    """直接上传多格式链接文件并解析提交到 115 离线下载（面向自动化脚本与外部 API）。"""
    content = await file.read()
    try:
        parsed = await asyncio.to_thread(
            parse_uploaded_file_payload,
            file.filename or "upload.txt",
            content,
            file.content_type or "",
        )
        urls = [item["link"] for item in parsed["links"]]
        folder = (custom_folder_name or "").strip() or (
            parsed["suggested_folder_name"] if len(urls) > 1 else ""
        )
        result = await asyncio.to_thread(
            submit_download_payload,
            links=urls,
            save_path=save_path,
            custom_folder_name=folder,
            notify_tg=notify_tg,
        )
        result["doc_kind"] = parsed["doc_kind"]
        result["file_name"] = parsed["file_name"]
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/downloads/parse-text", tags=["Downloads & Files"])
async def api_parse_text(body: ParseTextRequest, _: Dict = Depends(require_auth)):
    return await asyncio.to_thread(parse_raw_text_payload, body.raw_text)


@router.post("/downloads/submit", tags=["Downloads & Files"])
async def api_submit_download(body: SubmitDownloadRequest, _: Dict = Depends(require_auth)):
    try:
        return await asyncio.to_thread(
            submit_download_payload,
            links=body.links,
            raw_text=body.raw_text,
            save_path=body.save_path,
            custom_folder_name=body.custom_folder_name,
            notify_tg=body.notify_tg,
            remember_path=body.remember_path,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


# ── Unified Task Queue & Batch Group Management ──────────────────────────────

@router.get("/tasks", tags=["Task Queue"])
async def api_list_tasks(
    filter: str = Query(default="all", description="all | run | retry | done | fail"),
    limit: int = Query(default=100, ge=1, le=500),
    _: Dict = Depends(require_auth),
):
    return await asyncio.to_thread(list_unified_tasks_payload, filter, limit)


@router.get("/tasks/live/{task_id}", tags=["Task Queue"])
async def api_get_live_task(task_id: str, _: Dict = Depends(require_auth)):
    data = get_live_task_payload(task_id)
    if not data:
        raise HTTPException(status_code=404, detail="实时任务不存在或已过期")
    return data


@router.get("/tasks/groups/{group_id}", tags=["Task Queue"])
async def api_get_group_detail(
    group_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    mode: str = Query(default="all", description="all | failed"),
    _: Dict = Depends(require_auth),
):
    data = await asyncio.to_thread(get_group_detail_payload, group_id, page, page_size, mode)
    if not data:
        raise HTTPException(status_code=404, detail="未找到该任务组记录")
    return data


@router.post("/tasks/groups/{group_id}/retry", tags=["Task Queue"])
async def api_retry_group(group_id: str, _: Dict = Depends(require_auth)):
    return await asyncio.to_thread(retry_group_payload, group_id)


@router.delete("/tasks/groups/{group_id}", tags=["Task Queue"])
async def api_delete_group(group_id: str, _: Dict = Depends(require_auth)):
    return await asyncio.to_thread(delete_group_payload, group_id)


@router.delete("/tasks/groups", tags=["Task Queue"])
async def api_clear_groups(
    category: str = Query(..., description="done | fail"),
    _: Dict = Depends(require_auth),
):
    try:
        return await asyncio.to_thread(clear_groups_payload, category)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── 115 Official Cloud Offline Tasks ─────────────────────────────────────────

@router.get("/cloud/tasks", tags=["115 Cloud Tasks"])
async def api_cloud_tasks(
    page: int = Query(default=1, ge=1),
    _: Dict = Depends(require_auth),
):
    return await asyncio.to_thread(get_cloud_tasks_payload, page)


@router.delete("/cloud/tasks/{info_hash}", tags=["115 Cloud Tasks"])
async def api_delete_cloud_task(
    info_hash: str,
    delete_source: bool = Query(default=False),
    _: Dict = Depends(require_auth),
):
    try:
        return await asyncio.to_thread(delete_cloud_task_payload, info_hash, delete_source)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/cloud/tasks/clear", tags=["115 Cloud Tasks"])
async def api_clear_cloud_tasks(body: ClearCloudRequest, _: Dict = Depends(require_auth)):
    try:
        return await asyncio.to_thread(clear_cloud_tasks_payload, body.flag)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


# ── Settings & Logs ──────────────────────────────────────────────────────────

@router.get("/settings", tags=["Settings & Logs"])
async def api_get_settings(_: Dict = Depends(require_auth)):
    return await asyncio.to_thread(get_settings_payload)


@router.patch("/settings", tags=["Settings & Logs"])
async def api_update_settings(body: UpdateSettingsRequest, _: Dict = Depends(require_auth)):
    return await asyncio.to_thread(
        update_settings_payload,
        auto_retry_enabled=body.auto_retry_enabled,
        retry_max_wait_hours=body.retry_max_wait_hours,
        notify_tg_on_web_download=body.notify_tg_on_web_download,
        default_save_path=body.default_save_path,
    )


@router.post("/settings/reset-default-path", tags=["Settings & Logs"])
async def api_reset_default_path(_: Dict = Depends(require_auth)):
    return await asyncio.to_thread(reset_default_path_payload)


@router.post("/settings/reload", tags=["Settings & Logs"])
async def api_reload_config(_: Dict = Depends(require_auth)):
    return await asyncio.to_thread(reload_config_payload)


@router.post("/settings/password", tags=["Settings & Logs"])
async def api_change_password(
    body: ChangePasswordRequest,
    response: Response,
    user: Dict = Depends(require_auth),
):
    if not authenticate_user(user["username"], body.current_password):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    try:
        update_web_password(body.new_password, body.new_username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    new_user = get_web_username()
    set_session_cookie(response, new_user)
    return {"ok": True, "username": new_user}


@router.post("/settings/api-key/rotate", tags=["Settings & Logs"])
async def api_rotate_key(_: Dict = Depends(require_auth)):
    new_key = await asyncio.to_thread(rotate_api_key)
    return {"ok": True, "api_key": new_key}


@router.get("/logs", tags=["Settings & Logs"])
async def api_get_logs(
    lines: int = Query(default=200, ge=10, le=2000),
    _: Dict = Depends(require_auth),
):
    return await asyncio.to_thread(read_log_tail, lines)


@router.get("/logs/download", tags=["Settings & Logs"])
async def api_download_logs(_: Dict = Depends(require_auth)):
    log_path = get_log_file_path()
    if not os.path.exists(log_path):
        raise HTTPException(status_code=404, detail="日志文件不存在")
    return FileResponse(
        path=log_path,
        filename="115bot.log",
        media_type="text/plain; charset=utf-8",
    )


# ── Real-Time SSE Streams ────────────────────────────────────────────────────

@router.get("/events/stream", tags=["Real-Time SSE"])
async def api_events_stream(
    request: Request,
    once: bool = Query(default=False, description="仅推送首帧后关闭（用于测试）"),
    _: Dict = Depends(require_auth_sse),
):
    async def _gen():
        last_ver = -1
        last_push = 0.0
        while True:
            if await request.is_disconnected():
                break
            cur_ver = get_tracker_version()
            now = time.monotonic()
            if cur_ver != last_ver or (now - last_push) >= 5.0:
                snapshot = await asyncio.to_thread(list_unified_tasks_payload, "all", 100)
                last_ver = cur_ver
                last_push = now
                yield f"event: tasks\ndata: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
                if once:
                    break
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.8)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/logs/stream", tags=["Real-Time SSE"])
async def api_logs_stream(
    request: Request,
    lines: int = Query(default=100, ge=10, le=500),
    once: bool = Query(default=False, description="仅推送初始日志帧后关闭（用于测试）"),
    _: Dict = Depends(require_auth_sse),
):
    async def _gen():
        initial = await asyncio.to_thread(read_log_tail, lines)
        yield f"event: log_init\ndata: {json.dumps(initial, ensure_ascii=False)}\n\n"
        if once:
            return

        log_path = get_log_file_path()
        offset = os.path.getsize(log_path) if os.path.exists(log_path) else 0
        last_ping = time.monotonic()

        while True:
            if await request.is_disconnected():
                break
            if os.path.exists(log_path):
                size = os.path.getsize(log_path)
                if size < offset:
                    offset = 0  # log rotated/truncated
                if size > offset:
                    def _read_chunk(pos: int):
                        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                            f.seek(pos)
                            chunk = f.read()
                            return chunk, f.tell()

                    chunk, offset = await asyncio.to_thread(_read_chunk, offset)
                    new_lines = [ln for ln in chunk.splitlines() if ln]
                    if new_lines:
                        yield f"event: log_append\ndata: {json.dumps({'lines': new_lines}, ensure_ascii=False)}\n\n"
            now = time.monotonic()
            if now - last_ping >= 15.0:
                last_ping = now
                yield ": keepalive\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
