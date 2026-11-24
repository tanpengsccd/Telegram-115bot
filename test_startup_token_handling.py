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


if __name__ == "__main__":
    test_refresh_access_token_handles_empty_data_error_response()
    test_get_user_info_returns_none_when_token_refresh_fails()
    test_welcome_message_does_not_crash_on_failed_user_info()
    test_welcome_message_handles_missing_quota_info()
    test_initialize_115open_keeps_client_for_reauth_when_token_expired()
    print("All startup token handling tests passed!")
