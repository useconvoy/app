"""Google connector: JWT-bearer token exchange (cached), tool wire shapes,
flag annotations. Live smoke at the bottom, skipped unless
CONVOY_LIVE_GOOGLE_SA_KEY + CONVOY_LIVE_GOOGLE_FOLDER_ID are set."""

import json
import os
import time

import httpx
import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from convoy_environments.connectors import ConnectorError, get_connector
from convoy_environments.connectors import google as google_mod

_PEM = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
SA_KEY = json.dumps({"type": "service_account", "client_email": "demo@convoy-test.iam.gserviceaccount.com",
                     "private_key": _PEM})


@pytest.fixture(autouse=True)
def clear_token_cache():
    google_mod._token_cache.clear()
    yield
    google_mod._token_cache.clear()


def _handler(seen, api_response=None):
    def handle(request):
        if request.url.host == "oauth2.googleapis.com":
            body = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
            seen["grant_type"] = httpx.QueryParams(request.content.decode())["grant_type"]
            assertion = httpx.QueryParams(request.content.decode())["assertion"]
            claims = pyjwt.decode(assertion, options={"verify_signature": False})
            seen["scope"] = claims["scope"]
            seen["iss"] = claims["iss"]
            seen.setdefault("exchanges", 0)
            seen["exchanges"] += 1
            return httpx.Response(200, json={"access_token": "ya29.test-token", "expires_in": 3600})
        seen["auth"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        seen["method"] = request.method
        if request.method == "POST":
            seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=api_response or {"ok": True})
    return handle


async def test_token_exchange_jwt_grant_and_bearer_injection():
    seen = {}
    google = get_connector("google", transport=httpx.MockTransport(_handler(seen, {"files": []})))
    result = await google.invoke("google.drive_list_files", {"folderId": "folder123"}, SA_KEY)
    assert seen["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    assert seen["iss"] == "demo@convoy-test.iam.gserviceaccount.com"
    assert "spreadsheets" in seen["scope"] and "drive.readonly" in seen["scope"]
    assert seen["auth"] == "Bearer ya29.test-token"
    assert "folder123" in seen["url"]
    assert result == {"files": []}


async def test_token_cached_across_calls():
    seen = {}
    transport = httpx.MockTransport(_handler(seen))
    google = get_connector("google", transport=transport)
    await google.invoke("google.sheets_read_range", {"spreadsheetId": "s1", "range": "A1:B2"}, SA_KEY)
    await google.invoke("google.sheets_read_range", {"spreadsheetId": "s1", "range": "A3:B4"}, SA_KEY)
    assert seen["exchanges"] == 1  # second call reuses the cached token


async def test_append_row_wire_shape():
    seen = {}
    google = get_connector("google", transport=httpx.MockTransport(_handler(seen, {"updates": {"updatedRows": 1}})))
    result = await google.invoke("google.sheets_append_row",
                                 {"spreadsheetId": "sheet9", "values": ["2026-08-05", "ok", 42]},
                                 SA_KEY)
    assert seen["method"] == "POST"
    assert "spreadsheets/sheet9/values/A1:append" in seen["url"]
    assert "valueInputOption=USER_ENTERED" in seen["url"]
    assert seen["body"] == {"values": [["2026-08-05", "ok", 42]]}
    assert result["updates"]["updatedRows"] == 1


async def test_bad_key_and_rejected_exchange():
    google = get_connector("google", transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(ConnectorError, match="not a service-account JSON key"):
        await google.invoke("google.drive_list_files", {"folderId": "f"}, "not-json")
    with pytest.raises(ConnectorError, match="credential rejected"):
        await google.invoke("google.drive_list_files", {"folderId": "f"}, SA_KEY)


async def test_manifest_flags():
    manifest = await get_connector("google").manifest()
    flags = {t.name: (t.execution, t.sideEffecting) for t in manifest.tools}
    assert flags == {"google.drive_list_files": ("inline", False),
                     "google.sheets_read_range": ("inline", False),
                     "google.sheets_append_row": ("promoted", True)}


LIVE_KEY = os.environ.get("CONVOY_LIVE_GOOGLE_SA_KEY", "")
LIVE_FOLDER = os.environ.get("CONVOY_LIVE_GOOGLE_FOLDER_ID", "")


@pytest.mark.skipif(not (LIVE_KEY and LIVE_FOLDER),
                    reason="set CONVOY_LIVE_GOOGLE_SA_KEY (path) + CONVOY_LIVE_GOOGLE_FOLDER_ID")
async def test_live_smoke_list_append_read():
    key = open(LIVE_KEY).read() if os.path.exists(LIVE_KEY) else LIVE_KEY
    google = get_connector("google")
    files = await google.invoke("google.drive_list_files", {"folderId": LIVE_FOLDER}, key)
    assert files.get("files"), "share the folder with the service account and add one sheet"
    sheet = files["files"][0]["id"]
    stamp = "convoy-smoke-%d" % int(time.time())
    await google.invoke("google.sheets_append_row",
                        {"spreadsheetId": sheet, "values": [stamp, "live-smoke"]}, key)
    read = await google.invoke("google.sheets_read_range",
                               {"spreadsheetId": sheet, "range": "A1:Z1000"}, key)
    assert any(stamp in row for row in read.get("values", []))
