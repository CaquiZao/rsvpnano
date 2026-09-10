from urllib.parse import parse_qs, urlparse

import pytest

from handy_bridge.config import DriveConfig
from handy_bridge.drive_auth import build_consent_url, device_toml, exchange_code


class FakeResponse:
    def __init__(self, status=200, payload=None, json_error=False):
        self.status_code = status
        self._payload = payload or {}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("Invalid JSON")
        return self._payload


def test_the_consent_url_asks_only_for_drive_file_and_offline_access():
    url = build_consent_url("cid", "http://127.0.0.1:9004/")
    query = parse_qs(urlparse(url).query)
    assert query["scope"] == ["https://www.googleapis.com/auth/drive.file"]
    # Sem access_type=offline não vem refresh token, e sem refresh token o
    # device pararia de funcionar em uma hora.
    assert query["access_type"] == ["offline"]
    assert query["client_id"] == ["cid"]


def test_exchanging_the_code_returns_the_refresh_token():
    def requester(method, url, **kw):
        assert kw["data"]["grant_type"] == "authorization_code"
        assert kw["data"]["code"] == "the-code"
        return FakeResponse(200, {"refresh_token": "rtok", "access_token": "at"})

    token = exchange_code("cid", "csec", "the-code", "http://127.0.0.1:9004/", requester)
    assert token == "rtok"


def test_a_response_without_a_refresh_token_is_an_error():
    # Acontece quando a conta já autorizou o app antes: o Google devolve só
    # access_token, e seguir em frente escreveria um config quebrado.
    def requester(method, url, **kw):
        return FakeResponse(200, {"access_token": "at"})

    with pytest.raises(RuntimeError, match="refresh"):
        exchange_code("cid", "csec", "c", "http://127.0.0.1:9004/", requester)


def test_the_device_config_is_toml_the_firmware_can_read():
    cfg = DriveConfig(enabled=True, client_id="cid", client_secret="csec",
                      refresh_token="rtok", folder_id="fid")
    text = device_toml(cfg)
    assert 'client_id = "cid"' in text
    assert 'refresh_token = "rtok"' in text
    assert 'folder_id = "fid"' in text


def test_non_200_response_with_invalid_json_is_handled():
    # Simulates a corporate proxy or captive-portal error page
    def requester(method, url, **kw):
        return FakeResponse(500, json_error=True)

    with pytest.raises(RuntimeError, match="HTTP 500"):
        exchange_code("cid", "csec", "c", "http://127.0.0.1:9004/", requester)


def test_non_200_response_with_valid_json_is_handled():
    # Simulates a Google error response
    def requester(method, url, **kw):
        return FakeResponse(400, {"error": "invalid_grant"})

    with pytest.raises(RuntimeError, match="HTTP 400"):
        exchange_code("cid", "csec", "c", "http://127.0.0.1:9004/", requester)


def test_transport_exception_is_wrapped():
    # Simulates a network error (ConnectError, TimeoutException, etc.)
    def requester(method, url, **kw):
        raise RuntimeError("Connection timeout")

    with pytest.raises(RuntimeError, match="RuntimeError"):
        exchange_code("cid", "csec", "c", "http://127.0.0.1:9004/", requester)
