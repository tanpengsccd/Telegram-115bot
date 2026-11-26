# -*- coding: utf-8 -*-
"""Comprehensive unit & integration tests for FastAPI Web UI backend, REST API, Auth, QR, Downloads, Queue, Cloud, Settings & SSE."""

import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(ROOT_DIR, "app")
for p in (ROOT_DIR, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import init

os.makedirs(os.path.join(ROOT_DIR, "tmp"), exist_ok=True)
TEST_DB = os.path.join(ROOT_DIR, "tmp", "_test_web_api.db")
TEST_LOG_DIR = os.path.join(ROOT_DIR, "tmp", "_test_web_cfg")
os.makedirs(TEST_LOG_DIR, exist_ok=True)


class _DummyLogger:
    def debug(self, msg, *a, **kw): pass
    def info(self, msg, *a, **kw): pass
    def warn(self, msg, *a, **kw): pass
    def warning(self, msg, *a, **kw): pass
    def error(self, msg, *a, **kw): pass


def _reset_env():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)
    init.DB_FILE = TEST_DB
    init.CONFIG = TEST_LOG_DIR
    init.TEMP = os.path.join(ROOT_DIR, "tmp")
    init.logger = _DummyLogger()
    init.bot_config = {
        "allowed_user": [42],
        "115_app_id": "100199999",
        "create_strm": False,
        "category_folder": [
            {
                "name": "movies",
                "display_name": "电影",
                "path_map": [
                    {"name": "外语电影", "path": "/115/Movies/Foreign"},
                    {"name": "华语电影", "path": "/115/Movies/Chinese"},
                ],
            }
        ],
        "web": {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 8115,
            "username": "admin",
            "password": "secret_password_123",
            "api_key": "t115_fixed_test_api_key_0001",
            "notify_tg_on_web_download": True,
        },
    }
    from app.core import batch_task_manager as btm
    from app.handlers.download_handler import clear_user_last_save_path
    from app.utils import task_tracker

    btm.initialize_batch_task_tables(reset_sql=False)
    clear_user_last_save_path()
    task_tracker.clear_tasks()


def _make_fake_115():
    fake = MagicMock()
    fake.access_token = "tok_a"
    fake.refresh_token = "tok_r"
    fake.get_challenge.return_value = ("verifier_xyz", "challenge_xyz")
    fake.get_user_info.return_value = {
        "user_id": 8888,
        "user_name": "Web测试号",
        "vip_info": {"level_name": "长期VIP", "expire": 253402271999},
        "rt_space_info": {
            "all_total": {"size": 1000, "size_format": "1.00PB"},
            "all_use": {"size": 300, "size_format": "300TB"},
            "all_remain": {"size": 700, "size_format": "700TB"},
        },
    }
    fake.get_quota_info.return_value = {
        "count": 5000,
        "used": 100,
        "surplus": 4900,
        "max_size": 500,
        "package": [
            {
                "name": "月度VIP配额",
                "surplus": 4900,
                "expire_info": [{"surplus": 4900, "expire_time": int(time.time()) + 5 * 86400}],
            }
        ],
    }
    fake.get_offline_tasks_by_page.return_value = {
        "count": 2,
        "page_count": 1,
        "tasks": [
            {"info_hash": "ih_ok", "name": "Done.mp4", "status": 2, "percentDone": 100, "url": "magnet:?xt=urn:btih:aaa"},
            {"info_hash": "ih_fail", "name": "Bad.mp4", "status": -1, "percentDone": 0, "url": "magnet:?xt=urn:btih:bbb"},
        ],
    }
    fake.del_faild_offline_task.return_value = True
    fake.clear_cloud_task.return_value = True
    return fake


def test_auth_session_api_key_password_and_rotation():
    from fastapi.testclient import TestClient
    from app.web.app import create_web_app

    _reset_env()
    init.openapi_115 = _make_fake_115()
    client = TestClient(create_web_app())

    # 1. Public health + OpenAPI docs + built React PWA static files
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200
    r_index = client.get("/")
    assert r_index.status_code == 200
    assert "Telegram-115Bot" in r_index.text
    r_manifest = client.get("/manifest.webmanifest")
    assert r_manifest.status_code == 200
    assert "115Bot" in r_manifest.text
    assert client.get("/sw.js").status_code == 200

    # 2. Protected route rejected without auth
    assert client.get("/api/v1/status").status_code == 401

    # 3. API Key via X-API-Key and Authorization: Bearer
    r_hdr = client.get("/api/v1/auth/me", headers={"X-API-Key": "t115_fixed_test_api_key_0001"})
    assert r_hdr.status_code == 200
    assert r_hdr.json()["auth_type"] == "api_key"

    r_bearer = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer t115_fixed_test_api_key_0001"})
    assert r_bearer.status_code == 200
    assert r_bearer.json()["auth_type"] == "api_key"

    # 4. Login with wrong password fails, right password sets HttpOnly cookie
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    r_login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "secret_password_123"})
    assert r_login.status_code == 200
    assert "tg115_session" in client.cookies

    r_me = client.get("/api/v1/auth/me")
    assert r_me.status_code == 200
    assert r_me.json()["auth_type"] == "session"
    assert r_me.json()["username"] == "admin"

    # 5. Rotate API key invalidates old key and activates new key
    r_rot = client.post("/api/v1/settings/api-key/rotate")
    assert r_rot.status_code == 200
    new_key = r_rot.json()["api_key"]
    assert new_key != "t115_fixed_test_api_key_0001"
    # Logout cookie to test header-only auth cleanly
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me", headers={"X-API-Key": "t115_fixed_test_api_key_0001"}).status_code == 401
    assert client.get("/api/v1/auth/me", headers={"X-API-Key": new_key}).status_code == 200

    # 6. Change password -> old password fails, new password succeeds
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "secret_password_123"}).status_code == 200
    r_pwd = client.post(
        "/api/v1/settings/password",
        json={"current_password": "secret_password_123", "new_password": "new_strong_password"},
    )
    assert r_pwd.status_code == 200
    client.post("/api/v1/auth/logout")
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "secret_password_123"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "new_strong_password"}).status_code == 200


def test_status_files_downloads_and_notify_tg_gate():
    from fastapi.testclient import TestClient
    from app.web.app import create_web_app
    from app.handlers.download_handler import _download_tasks_batch_impl

    _reset_env()
    fake_115 = _make_fake_115()
    init.openapi_115 = fake_115
    client = TestClient(create_web_app())
    hdr = {"X-API-Key": "t115_fixed_test_api_key_0001"}

    # 1. Status endpoint
    st = client.get("/api/v1/status", headers=hdr)
    assert st.status_code == 200
    body = st.json()
    assert body["authorized"] is True
    assert body["account"]["user_name"] == "Web测试号"
    assert body["quota"]["surplus"] == 4900
    assert body["default_save_path"] == "/115/Movies/Foreign"
    assert body["auto_retry_enabled"] is False

    # 2. Parse uploaded CSV file (preview only, does NOT submit)
    m1 = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
    m2 = "magnet:?xt=urn:btih:fedcba9876543210fedcba9876543210fedcba98"
    csv_bytes = f"name,link\nA,{m1}\nB,{m2}\n".encode("utf-8")
    with patch("app.web.services.download_executor.submit") as mock_sub:
        r_parse = client.post(
            "/api/v1/files/parse",
            files={"file": ("batch.csv", csv_bytes, "text/csv")},
            headers=hdr,
        )
        assert r_parse.status_code == 200
        pdata = r_parse.json()
        assert pdata["doc_kind"] == "CSV"
        assert pdata["link_count"] == 2
        assert len(pdata["links"]) == 2
        mock_sub.assert_not_called()

    # 3. Direct REST submit with notify_tg=False
    with patch("app.web.services.download_executor.submit") as mock_sub:
        r_sub = client.post(
            "/api/v1/downloads/submit",
            json={"links": [m1, m2], "custom_folder_name": "WebBatch", "notify_tg": False},
            headers=hdr,
        )
        assert r_sub.status_code == 200
        sdata = r_sub.json()
        assert sdata["ok"] is True
        assert sdata["accepted_count"] == 2
        assert sdata["notify_tg"] is False
        assert sdata["tracker_id"].startswith("dl_")
        mock_sub.assert_called_once()
        # args: (download_tasks_batch, normalized_links, target_path, uid, folder, None, effective_notify_tg, tracker_id)
        assert mock_sub.call_args.args[2] == "/115/Movies/Foreign"
        assert mock_sub.call_args.args[6] is False

    # 4. Direct multipart file submit via API (/api/v1/files/submit)
    with patch("app.web.services.download_executor.submit") as mock_sub2:
        r_fsub = client.post(
            "/api/v1/files/submit",
            files={"file": ("auto.csv", csv_bytes, "text/csv")},
            data={"notify_tg": "true"},
            headers=hdr,
        )
        assert r_fsub.status_code == 200
        assert r_fsub.json()["accepted_count"] == 2
        assert r_fsub.json()["notify_tg"] is True
        mock_sub2.assert_called_once()

    # 5. Verify _download_tasks_batch_impl honors notify_tg=False (zero TG messages pushed)
    fake_115.offline_download_specify_path_batch.return_value = True
    fake_115.get_offline_tasks_for_links.return_value = [
        {"url": m1, "name": "M1.mp4", "status": 2, "percentDone": 100, "info_hash": "h1"},
    ]
    with patch("app.handlers.download_handler.time.sleep"), \
         patch("app.core.batch_task_manager.generate_magnet_txt_file", return_value=True), \
         patch("app.utils.message_queue.add_task_to_queue") as mock_q, \
         patch("app.utils.message_queue.send_document_via_bot") as mock_doc:
        _download_tasks_batch_impl(
            [{"link": m1}], "/115/Movies/Foreign", 42, tracker_id="dl_silent", notify_tg=False
        )
        mock_q.assert_not_called()
        mock_doc.assert_not_called()


def test_queue_cloud_settings_logs_qr_and_sse():
    from fastapi.testclient import TestClient
    from app.web.app import create_web_app
    from app.core import batch_task_manager as btm
    from app.utils import task_tracker

    _reset_env()
    fake_115 = _make_fake_115()
    init.openapi_115 = fake_115
    client = TestClient(create_web_app())
    hdr = {"X-API-Key": "t115_fixed_test_api_key_0001"}

    # Seed one persisted group + one live task
    m1 = "magnet:?xt=urn:btih:1111111111111111111111111111111111111111"
    m2 = "magnet:?xt=urn:btih:2222222222222222222222222222222222222222"
    gid = btm.create_batch_task_group("SampleBatch", "/115/Movies/Foreign", 2, chat_id=42)
    btm.save_batch_task_items(gid, [{"link": m1}, {"link": m2}])
    btm.update_task_item_status(m1, gid, "success", "Done1.mp4")
    btm.update_task_item_status(m2, gid, "failed", "")
    btm.update_task_group_counts(gid)

    task_tracker.register_task(
        "dl_live_1", "download", "LiveJob", total=1, save_path="/115/Movies/Foreign",
        items=[{"index": 1, "link": m1, "status": "running", "percent": 55}],
    )

    # 1. Unified tasks + live detail + group detail
    r_tasks = client.get("/api/v1/tasks", headers=hdr)
    assert r_tasks.status_code == 200
    tdata = r_tasks.json()
    assert tdata["counts"]["run"] == 1
    assert tdata["counts"]["retry"] == 1

    r_live = client.get("/api/v1/tasks/live/dl_live_1", headers=hdr)
    assert r_live.status_code == 200
    assert r_live.json()["items"][0]["percent"] == 55

    r_grp = client.get(f"/api/v1/tasks/groups/{gid}", headers=hdr)
    assert r_grp.status_code == 200
    assert r_grp.json()["summary"]["success"] == 1
    assert r_grp.json()["summary"]["failed"] == 1

    # 2. Manual retry + delete group
    with patch("app.web.services.retry_batch_group_now", return_value={"ok": True, "message": "已重试"}):
        r_ret = client.post(f"/api/v1/tasks/groups/{gid}/retry", headers=hdr)
        assert r_ret.status_code == 200 and r_ret.json()["ok"] is True

    assert client.delete(f"/api/v1/tasks/groups/{gid}", headers=hdr).status_code == 200
    assert client.get(f"/api/v1/tasks/groups/{gid}", headers=hdr).status_code == 404

    # 3. 115 Cloud task manager
    r_cloud = client.get("/api/v1/cloud/tasks?page=1", headers=hdr)
    assert r_cloud.status_code == 200
    assert len(r_cloud.json()["tasks"]) == 2
    assert client.delete("/api/v1/cloud/tasks/ih_ok?delete_source=true", headers=hdr).status_code == 200
    fake_115.del_faild_offline_task.assert_called_with("ih_ok", True)
    assert client.post("/api/v1/cloud/tasks/clear", json={"flag": 4}, headers=hdr).status_code == 200
    fake_115.clear_cloud_task.assert_called_with(4)

    # 4. Settings: enabling auto-retry returns usage guide HTML; retry cap persists
    r_set = client.patch(
        "/api/v1/settings",
        json={"auto_retry_enabled": True, "retry_max_wait_hours": 48, "notify_tg_on_web_download": False},
        headers=hdr,
    )
    assert r_set.status_code == 200
    s_body = r_set.json()
    assert s_body["auto_retry_enabled"] is True
    assert s_body["retry_max_wait_hours"] == 48
    assert s_body["notify_tg_on_web_download"] is False
    assert "自动重试使用说明" in (s_body.get("auto_retry_guide_html") or "")

    # 5. Logs endpoint & download & SSE streams (once=true)
    log_file = os.path.join(TEST_LOG_DIR, "115bot.log")
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("2026-11-26 17:00:00 INFO line one\n2026-11-26 17:00:01 INFO line two\n")

    r_log = client.get("/api/v1/logs?lines=50", headers=hdr)
    assert r_log.status_code == 200
    assert len(r_log.json()["lines"]) == 2
    assert client.get("/api/v1/logs/download", headers=hdr).status_code == 200

    r_sse_tasks = client.get("/api/v1/events/stream?once=true", headers=hdr)
    assert r_sse_tasks.status_code == 200
    assert "event: tasks" in r_sse_tasks.text

    r_sse_logs = client.get("/api/v1/logs/stream?once=true", headers=hdr)
    assert r_sse_logs.status_code == 200
    assert "event: log_init" in r_sse_logs.text
    assert "line two" in r_sse_logs.text

    # 6. 115 QR PKCE auth start + status check
    fake_qr_resp = MagicMock(status_code=200)
    fake_qr_resp.json.return_value = {
        "data": {"uid": "uid_1", "time": 1700000000, "sign": "sig_1", "qrcode": "https://115.com/scan/demo"}
    }
    fake_poll_resp = MagicMock(status_code=200)
    fake_poll_resp.json.return_value = {"state": 0}  # immediately expire background poll in test
    with patch("app.web.qr_auth.requests.post", return_value=fake_qr_resp), \
         patch("app.web.qr_auth.requests.get", return_value=fake_poll_resp):
        r_qr = client.post("/api/v1/115/qr/start", headers=hdr)
        assert r_qr.status_code == 200
        qr_data = r_qr.json()
        assert qr_data["qr_data_url"].startswith("data:image/png;base64,")
        r_qr_st = client.get(f"/api/v1/115/qr/{qr_data['session_id']}", headers=hdr)
        assert r_qr_st.status_code == 200


if __name__ == "__main__":
    try:
        test_auth_session_api_key_password_and_rotation()
        test_status_files_downloads_and_notify_tg_gate()
        test_queue_cloud_settings_logs_qr_and_sse()
        print("All FastAPI Web UI & REST API tests passed!")
    finally:
        if os.path.exists(TEST_DB):
            os.remove(TEST_DB)
