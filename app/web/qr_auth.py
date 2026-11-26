# -*- coding: utf-8 -*-
"""Web-oriented 115 PKCE QR-code authorization session manager."""

import base64
import hashlib
import io
import os
import secrets
import threading
import time
import uuid
from typing import Dict, Optional

import qrcode
import requests

import init

_lock = threading.Lock()
_sessions: Dict[str, Dict] = {}
_SESSION_TTL_SECONDS = 300


def _generate_pkce_pair():
    if init.openapi_115 is not None and hasattr(init.openapi_115, "get_challenge"):
        return init.openapi_115.get_challenge()
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _prune_old_sessions() -> None:
    cutoff = time.time() - _SESSION_TTL_SECONDS
    stale = [sid for sid, s in _sessions.items() if s.get("created_at", 0) < cutoff]
    for sid in stale:
        _sessions.pop(sid, None)


def _render_qr_data_url(qr_data: str) -> str:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=8,
        border=2,
    )
    qr.add_data(qr_data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _poll_qr_session(session_id: str) -> None:
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": init.USER_AGENT,
    }
    deadline = time.time() + 180
    while time.time() < deadline:
        with _lock:
            sess = _sessions.get(session_id)
            if not sess or sess["status"] in ("authorized", "expired", "error", "cancelled"):
                return
            params = {"uid": sess["uid"], "time": sess["time"], "sign": sess["sign"]}
            verifier = sess["verifier"]

        try:
            resp = requests.get("https://qrcodeapi.115.com/get/status/", params=params, timeout=15)
            if resp.status_code != 200:
                time.sleep(2)
                continue
            res = resp.json()
            if res.get("state") == 0:
                with _lock:
                    if session_id in _sessions:
                        _sessions[session_id]["status"] = "expired"
                        _sessions[session_id]["message"] = "二维码已过期，请点击刷新重新生成"
                return

            status_data = res.get("data") if isinstance(res, dict) else None
            st = status_data.get("status") if isinstance(status_data, dict) else None
            if st is None:
                time.sleep(2)
                continue
            if st == 1:
                with _lock:
                    if session_id in _sessions:
                        _sessions[session_id]["status"] = "scanned"
                        _sessions[session_id]["message"] = "已扫码，请在 115 App 内点击确认授权"
                time.sleep(1.5)
                continue
            if st == 2:
                token_resp = requests.post(
                    "https://passportapi.115.com/open/deviceCodeToToken",
                    headers=headers,
                    data={"uid": params["uid"], "code_verifier": verifier},
                    timeout=15,
                )
                token_res = token_resp.json()
                token_data = token_res.get("data") if isinstance(token_res, dict) else None
                if token_resp.status_code == 200 and isinstance(token_data, dict) and token_data.get("access_token"):
                    access_token = token_data["access_token"]
                    refresh_token = token_data["refresh_token"]
                    if init.openapi_115 is None:
                        from app.core.open_115 import OpenAPI_115
                        client = OpenAPI_115.__new__(OpenAPI_115)
                        client.access_token = access_token
                        client.refresh_token = refresh_token
                        client.base_url = "https://proapi.115.com"
                        client._rate_limit_lock = threading.Lock()
                        client._last_request_time = 0
                        client._rate_limit_interval = 1.0
                        client._offline_page1_cache_lock = threading.Lock()
                        client._offline_page1_cache_ts = 0.0
                        client._offline_page1_cache_data = None
                        client._offline_page1_cache_ttl = 3.0
                        init.openapi_115 = client
                    else:
                        init.openapi_115.access_token = access_token
                        init.openapi_115.refresh_token = refresh_token
                    init.openapi_115.save_token_to_file(access_token, refresh_token, init.TOKEN_FILE)
                    if init.logger:
                        init.logger.info("✅ Web 端 115 PKCE 扫码授权成功，Token 已保存")
                    with _lock:
                        if session_id in _sessions:
                            _sessions[session_id]["status"] = "authorized"
                            _sessions[session_id]["message"] = "115 授权成功！"
                    return
                else:
                    with _lock:
                        if session_id in _sessions:
                            _sessions[session_id]["status"] = "error"
                            _sessions[session_id]["message"] = f"换取 Token 失败: {token_res}"
                    return
        except Exception as e:
            if init.logger:
                init.logger.warn(f"Web 115 扫码状态轮询异常: {e}")
            time.sleep(2)

    with _lock:
        if session_id in _sessions and _sessions[session_id]["status"] in ("waiting", "scanned"):
            _sessions[session_id]["status"] = "expired"
            _sessions[session_id]["message"] = "二维码等待超时，请重新生成"


def start_qr_session() -> Dict:
    """Start a new 115 PKCE QR authorization session and return QR data URL + session_id."""
    app_id = str((init.bot_config or {}).get("115_app_id") or "").strip()
    if not app_id or app_id.lower() == "your_115_app_id":
        raise ValueError("尚未在 config.yaml 中配置有效的 115_app_id")

    verifier, challenge = _generate_pkce_pair()
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": init.USER_AGENT,
    }
    resp = requests.post(
        "https://passportapi.115.com/open/authDeviceCode",
        headers=headers,
        data={
            "client_id": app_id,
            "code_challenge": challenge,
            "code_challenge_method": "sha256",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"请求 115 授权二维码失败: HTTP {resp.status_code}")
    res = resp.json()
    data = res.get("data") if isinstance(res, dict) else None
    if not isinstance(data, dict) or not data.get("uid") or not data.get("qrcode"):
        raise RuntimeError(f"115 返回二维码数据异常: {res}")

    qr_data_url = _render_qr_data_url(data["qrcode"])
    session_id = uuid.uuid4().hex[:12]
    record = {
        "session_id": session_id,
        "uid": data["uid"],
        "time": data["time"],
        "sign": data["sign"],
        "verifier": verifier,
        "qr_data_url": qr_data_url,
        "status": "waiting",
        "message": "请打开 115 App 扫描上方二维码",
        "created_at": time.time(),
    }
    with _lock:
        _prune_old_sessions()
        _sessions[session_id] = record

    t = threading.Thread(target=_poll_qr_session, args=(session_id,), daemon=True)
    t.start()

    return {
        "session_id": session_id,
        "qr_data_url": qr_data_url,
        "status": record["status"],
        "message": record["message"],
    }


def get_qr_session_status(session_id: str) -> Optional[Dict]:
    with _lock:
        _prune_old_sessions()
        sess = _sessions.get(session_id)
        if not sess:
            return None
        return {
            "session_id": sess["session_id"],
            "status": sess["status"],
            "message": sess["message"],
            "qr_data_url": sess["qr_data_url"],
        }
