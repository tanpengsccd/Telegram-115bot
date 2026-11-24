# -*- coding: utf-8 -*-
"""Smoke tests for 115 token refresh failure and startup degradation."""

import sys
import os
from unittest.mock import MagicMock, patch

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(ROOT_DIR, "app")
for p in (ROOT_DIR, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import init
from app.core.open_115 import OpenAPI_115


class _DummyLogger:
    def debug(self, msg): pass
    def info(self, msg): pass
    def warn(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _make_client():
    init.logger = _DummyLogger()
    init.TOKEN_FILE = os.path.join("tmp", "_nonexistent_test_tokens.json")
    with patch.object(OpenAPI_115, "get_token", return_value=None):
        client = OpenAPI_115()
    client.access_token = "expired_access"
    client.refresh_token = "expired_refresh"
    return client


def test_refresh_access_token_handles_empty_data_error_response():
    client = _make_client()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "state": False,
        "code": 40140119,
        "message": "refresh_token 无效",
        "data": [],
    }
    with patch("app.core.open_115.requests.post", return_value=mock_resp):
        assert client.refresh_access_token() is False


def test_get_user_info_returns_none_when_token_refresh_fails():
    client = _make_client()
    expired_resp = {
        "state": False,
        "message": "access_token 无效",
        "code": 40140125,
        "data": [],
    }
    with patch.object(client, "_make_api_request", return_value=expired_resp) as mock_req, \
         patch.object(client, "refresh_access_token", return_value=False) as mock_refresh:
        result = client.get_user_info()
        assert result is None
        assert mock_refresh.call_count == 1
        assert mock_req.call_count == 1


def test_welcome_message_does_not_crash_on_failed_user_info():
    client = _make_client()
    with patch.object(client, "get_user_info", return_value=None), \
         patch.object(client, "get_quota_info") as mock_quota:
        lines = client.welcome_message()
        assert lines == ("", "", "", "")
        mock_quota.assert_not_called()


def test_welcome_message_handles_missing_quota_info():
    client = _make_client()
    valid_user = {
        "user_name": "tester",
        "rt_space_info": {
            "all_total": {"size_format": "10TB"},
            "all_use": {"size_format": "1TB"},
            "all_remain": {"size_format": "9TB"},
        },
        "vip_info": {"level_name": "VIP", "expire": 1800000000},
    }
    with patch.object(client, "get_user_info", return_value=valid_user), \
         patch.object(client, "get_quota_info", return_value=None):
        line1, line2, line3, line4 = client.welcome_message()
        assert "tester" in line1
        assert "未知" in line4


def test_initialize_115open_keeps_client_for_reauth_when_token_expired():
    init.logger = _DummyLogger()
    fake_client = _make_client()
    with patch.object(init, "OpenAPI_115", return_value=fake_client), \
         patch.object(fake_client, "get_user_info", return_value=None):
        ok = init.initialize_115open()
        assert ok is False
        assert init.openapi_115 is fake_client


def test_batch_delete_files_deduplicates_and_chunks():
    client = _make_client()
    # 250 unique ids + 10 duplicates -> 3 batches (100, 100, 50)
    fids = [f"fid_{i}" for i in range(250)] + [f"fid_{i}" for i in range(10)]
    called_batches = []

    def _fake_delete(file_ids):
        called_batches.append(file_ids.split(","))
        return True

    with patch.object(client, "delet_file", side_effect=_fake_delete), \
         patch("app.core.open_115.time.sleep"):
        client._batch_delete_files(fids, batch_size=100)

    assert [len(b) for b in called_batches] == [100, 100, 50]
    assert called_batches[0][0] == "fid_0"
    assert called_batches[-1][-1] == "fid_249"


def test_auth_pkce_waits_when_qr_status_missing():
    client = _make_client()
    init.IMAGE_PATH = "tmp"
    init.TOKEN_FILE = "tmp/_test_tokens_unused.json"

    post_responses = [
        MagicMock(status_code=200, json=lambda: {
            "data": {"uid": "u1", "time": 123, "qrcode": "https://example.com/qr", "sign": "s1"}
        }),
    ]
    get_responses = [
        # First poll: status is None (waiting for scan) -> must not raise KeyError
        MagicMock(status_code=200, json=lambda: {"state": 1, "data": {}}),
        # Second poll: expired -> exits loop cleanly
        MagicMock(status_code=200, json=lambda: {"state": 0, "data": {}}),
    ]

    with patch("app.core.open_115.requests.post", side_effect=post_responses), \
         patch("app.core.open_115.requests.get", side_effect=get_responses), \
         patch("app.core.open_115.add_task_to_queue"), \
         patch("app.core.open_115.time.sleep"):
        client.auth_pkce(12345, "100196043")

    qr_path = os.path.join("tmp", "qrcode.png")
    if os.path.exists(qr_path):
        os.remove(qr_path)


def test_build_bot_request_uses_proxy_env():
    import importlib.util
    spec = importlib.util.spec_from_file_location("bot_entry", os.path.join(APP_DIR, "115bot.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:7890"}, clear=False):
        req = mod.build_bot_request()
        assert req is not None

    with patch.dict(os.environ, {"HTTP_PROXY": "", "HTTPS_PROXY": ""}, clear=False):
        req_direct = mod.build_bot_request()
        assert req_direct is not None


if __name__ == "__main__":
    test_refresh_access_token_handles_empty_data_error_response()
    test_get_user_info_returns_none_when_token_refresh_fails()
    test_welcome_message_does_not_crash_on_failed_user_info()
    test_welcome_message_handles_missing_quota_info()
    test_initialize_115open_keeps_client_for_reauth_when_token_expired()
    test_batch_delete_files_deduplicates_and_chunks()
    test_auth_pkce_waits_when_qr_status_missing()
    test_build_bot_request_uses_proxy_env()
    print("All startup token handling tests passed!")
