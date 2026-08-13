from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

CONVOY_ENVIRONMENTS = Path(__file__).resolve().parents[2] / "environments"
sys.path.insert(0, str(CONVOY_ENVIRONMENTS))

from convoy_environments.connectors.github import GitHubConnector  # noqa: E402
from convoy_environments.connectors.google import GoogleConnector  # noqa: E402
from convoy_environments.connectors.slack import SlackConnector  # noqa: E402


async def main() -> None:
    origin = os.environ["CONVOY_SANDBOX_ORIGIN"].rstrip("/")
    sandbox_id = os.environ.get("CONVOY_SANDBOX_ID", "python-connectors")
    base = f"{origin}/s/{sandbox_id}"

    slack = SlackConnector(config={"apiBaseUrl": f"{base}/slack/api"})
    channels = await slack.invoke("slack.list_channels", {}, "xoxb-convoy-sandbox")
    assert channels["ok"] is True
    assert any(channel["id"] == "C_OPERATIONS" for channel in channels["channels"])
    posted = await slack.invoke(
        "slack.post_message",
        {"channel": "C_OPERATIONS", "text": "Called through the real Convoy Slack connector"},
        "xoxb-convoy-sandbox",
    )
    assert posted["ok"] is True

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    service_account = json.dumps({
        "client_email": "convoy-agent@sandbox.invalid",
        "private_key": pem,
    })
    google = GoogleConnector(config={
        "tokenUrl": f"{base}/google/oauth2/token",
        "driveBaseUrl": f"{base}/google/drive/v3",
        "sheetsBaseUrl": f"{base}/google/sheets/v4",
        "docsBaseUrl": f"{base}/google/docs/v1",
    })
    files = await google.invoke(
        "google.drive_list_files",
        {"folderId": "folder-shared"},
        service_account,
    )
    # The default listing filters to spreadsheets; the runbook doc stays out.
    assert [item["id"] for item in files["files"]] == ["sheet-operations"]
    documents = await google.invoke(
        "google.drive_list_files",
        {"folderId": "folder-shared", "mimeType": "application/vnd.google-apps.document"},
        service_account,
    )
    assert [item["id"] for item in documents["files"]] == ["doc-runbook"]
    appended = await google.invoke(
        "google.sheets_append_row",
        {
            "spreadsheetId": "sheet-operations",
            "range": "Operations!A1",
            "values": ["Sample task", "Connector E2E", "Convoy"],
        },
        service_account,
    )
    assert appended["updates"]["updatedRows"] == 1

    runbook = await google.invoke(
        "google.docs_read", {"documentId": "doc-runbook"}, service_account,
    )
    assert runbook["title"] == "Operations Runbook"
    assert "nightly export" in runbook["text"]
    await google.invoke(
        "google.docs_update",
        {"documentId": "doc-runbook",
         "content": "Operations Runbook\n\nExports: the hourly-sync job runs every hour.\n"},
        service_account,
    )
    updated = await google.invoke(
        "google.docs_read", {"documentId": "doc-runbook"}, service_account,
    )
    assert "hourly-sync" in updated["text"]
    assert "nightly export" not in updated["text"]

    github = GitHubConnector(config={"apiBaseUrl": f"{base}/github"})
    file_result = await github.invoke(
        "github.get_file",
        {"repo": "sandbox/example-service", "path": "README.md", "ref": "main"},
        "github-convoy-sandbox",
    )
    assert file_result["encoding"] == "base64"
    issue = await github.invoke(
        "github.create_issue",
        {"repo": "sandbox/example-service", "title": "Created through the real Convoy connector"},
        "github-convoy-sandbox",
    )
    # The fixture seeds issues 41 and 42; a new issue continues the sequence.
    assert issue["number"] == 43
    print("Real Convoy connector integration passed")


if __name__ == "__main__":
    asyncio.run(main())
