from datetime import timezone

import pytest

from handy_bridge.config import DriveConfig
from handy_bridge.drive import Drive, DriveError

CFG = DriveConfig(
    enabled=True,
    client_id="cid",
    client_secret="csec",
    refresh_token="rtok",
    folder_id="fid",
)


class FakeResponse:
    def __init__(self, status: int = 200, payload: dict | None = None, content: bytes = b""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = content
        self.text = ""

    def json(self) -> dict:
        return self._payload


def test_exchanges_the_refresh_token_for_an_access_token():
    seen = {}

    def requester(method, url, **kw):
        seen["method"], seen["url"], seen["data"] = method, url, kw.get("data")
        return FakeResponse(200, {"access_token": "at-1", "expires_in": 3599})

    assert Drive(CFG, requester=requester).access_token() == "at-1"
    assert seen["method"] == "POST"
    assert seen["url"] == "https://oauth2.googleapis.com/token"
    assert seen["data"]["grant_type"] == "refresh_token"
    assert seen["data"]["refresh_token"] == "rtok"


def test_reuses_the_access_token_instead_of_refreshing_every_call():
    calls = []

    def requester(method, url, **kw):
        calls.append(url)
        return FakeResponse(200, {"access_token": "at-1", "expires_in": 3599})

    drive = Drive(CFG, requester=requester)
    drive.access_token()
    drive.access_token()
    assert len(calls) == 1


def test_a_rejected_refresh_token_says_so():
    def requester(method, url, **kw):
        return FakeResponse(400, {"error": "invalid_grant"})

    with pytest.raises(DriveError, match="invalid_grant"):
        Drive(CFG, requester=requester).access_token()


def test_lists_only_the_configured_folder():
    seen = {}

    def requester(method, url, **kw):
        if url.endswith("/token"):
            return FakeResponse(200, {"access_token": "at", "expires_in": 3599})
        seen["url"], seen["params"], seen["headers"] = url, kw.get("params"), kw.get("headers")
        return FakeResponse(
            200,
            {
                "files": [
                    {
                        "id": "w1",
                        "name": "20260910-120000.wav",
                        "createdTime": "2026-09-10T12:00:00.000Z",
                    }
                ]
            },
        )

    files = Drive(CFG, requester=requester).list_inbox()
    assert len(files) == 1
    assert files[0].id == "w1"
    assert files[0].name == "20260910-120000.wav"
    assert files[0].created_at.tzinfo is not None
    assert files[0].created_at.astimezone(timezone.utc).hour == 12
    assert "'fid' in parents" in seen["params"]["q"]
    assert "trashed = false" in seen["params"]["q"]
    assert seen["headers"]["Authorization"] == "Bearer at"


def test_downloads_the_file_bytes():
    def requester(method, url, **kw):
        if url.endswith("/token"):
            return FakeResponse(200, {"access_token": "at", "expires_in": 3599})
        assert kw.get("params") == {"alt": "media"}
        return FakeResponse(200, content=b"RIFFxxxx")

    assert Drive(CFG, requester=requester).download("w1") == b"RIFFxxxx"


def test_deletes_the_file():
    seen = {}

    def requester(method, url, **kw):
        if url.endswith("/token"):
            return FakeResponse(200, {"access_token": "at", "expires_in": 3599})
        seen["method"], seen["url"] = method, url
        return FakeResponse(204)

    Drive(CFG, requester=requester).delete("w1")
    assert seen["method"] == "DELETE"
    assert seen["url"].endswith("/files/w1")


def test_deletes_the_file_treating_404_as_success():
    def requester(method, url, **kw):
        if url.endswith("/token"):
            return FakeResponse(200, {"access_token": "at", "expires_in": 3599})
        return FakeResponse(404)

    # Should not raise; 404 means the file is already gone, which is the goal.
    Drive(CFG, requester=requester).delete("w1")


def test_an_http_error_on_listing_raises():
    def requester(method, url, **kw):
        if url.endswith("/token"):
            return FakeResponse(200, {"access_token": "at", "expires_in": 3599})
        return FakeResponse(403, {"error": {"message": "insufficientPermissions"}})

    with pytest.raises(DriveError, match="insufficientPermissions"):
        Drive(CFG, requester=requester).list_inbox()


def test_network_error_on_token_exchange_does_not_leak_credentials():
    def requester(method, url, **kw):
        raise OSError("connection reset by peer")

    with pytest.raises(DriveError, match="OSError") as caught:
        Drive(CFG, requester=requester).access_token()
    # Ensure no credential appears in the error message
    assert "rtok" not in str(caught.value)
    assert "csec" not in str(caught.value)
    assert "cid" not in str(caught.value)
