#!/usr/bin/env python3
"""One-command seeding for the sheets demo.

Three subcommands, one per seam of the demo ("run our script in a sandbox,
then append its result to 100 Google Sheets in a Drive folder"):

  create-sheets  Talk straight to Google: exchange a service-account JSON key
                 for an access token (OAuth2 JWT-bearer grant, same pattern as
                 convoy_environments.connectors.google) and create the missing
                 sheet-001..sheet-NNN spreadsheets in a Drive folder.
  provision      Talk to the console API: workspace -> google connection ->
                 "sheets-demo" environment allowlisting exactly the three
                 google tools, with a sandboxTemplate set.
  start-run      Talk to the agent runtime's control plane: POST /runs with a
                 valid CreateRunRequest and print the run id + SSE events URL.

Dependencies: stdlib + httpx + PyJWT (RS256 via the cryptography extra, which
the workspace venv already carries).

ASSUMPTIONS (everything here that cannot be dictated from outside the
control plane / console, and the smallest honest choice made for each):

1. Control-plane auth (agent-runtime/src/convoy_runtime/control_plane/auth.py)
   is the v0 static scheme: `Authorization: Bearer <CONVOY_DEV_TOKEN>` plus
   `x-actor-id` / `x-tenant-id` headers. We default the token to "dev-token"
   (the RuntimeConfig default) and use the workspace id as the tenant id.
2. `prompt_ref` is NOT a client-side field. CreateRunRequest has no prompt
   input; the control plane itself writes runs/{id}/prompts/root.json with a
   canned root prompt at creation. The external knobs are `goal` and
   `success_criteria`, so that is all start-run sends.
3. `model` is omitted by default so the deployment's configured default
   (CONVOY_DEFAULT_MODEL, "scripted-echo-1" in the local stack) applies;
   --model overrides it and must then be approved by the model gateway when
   real execution is configured.
4. `policy`: plan approval defaults ON for non-sandbox bindings, which would
   stall a one-command demo, so start-run sends an explicit
   {"require_plan_approval": false} (flip with --require-plan-approval).
   Every other RunPolicy field keeps its schema default (linear_fanout,
   pause on budget exhausted, max_parallel 5) — the right shape for fanning
   out over ~100 sheets.
5. `tools` requests sandbox_exec plus the three google tools. The environment
   allowlists ONLY the google tools; sandbox_exec is auto-granted into the
   binding's tool registry from the environment's sandboxTemplate (behavior
   from branch demo-sandbox-grant). Until that lands in the deployment, the
   control plane will 422 the sandbox_exec request.
6. `budget_usd` is a Decimal on the wire; we send it as a JSON string.
7. environment ids resolve through the control plane's environments registry
   seam (CONVOY_STUB_ENV_URL) — the environment must be visible there.
8. Console auth: workspace creation needs X-Convoy-Internal (the provisioning
   token); every later call authenticates as the created user via
   X-Convoy-User, which works whenever no WorkOS verifier is configured or
   CONVOY_CONSOLE_ALLOW_HEADER_AUTH=1.
9. Connection registration normally lets the console build the manifest via
   its google connector; --manifest-json (or the `manifest=` argument of
   provision()) supplies an explicit declared manifest instead, for hermetic
   runs where the console must not reach out to build one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import jwt

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"  # files.create needs full drive
SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"

GOOGLE_TOOLS = [
    "google.drive_list_files",
    "google.sheets_read_range",
    "google.sheets_append_row",
]
SANDBOX_TOOL = "sandbox_exec"


def _die(message: str) -> "SystemExit":
    return SystemExit("demo_sheets_seed: %s" % message)


def _check(resp: httpx.Response, what: str) -> httpx.Response:
    if resp.status_code >= 400:
        raise _die("%s failed (%d): %s" % (what, resp.status_code, resp.text[:500]))
    return resp


def _load_sa_key(path: str) -> Dict[str, Any]:
    try:
        key = json.loads(Path(path).read_text())
        key["client_email"], key["private_key"]
    except (OSError, ValueError, KeyError) as err:
        raise _die("--sa-key %s is not a service-account JSON key (%s)" % (path, err))
    return key


# -- google: JWT-bearer token exchange (connectors/google.py pattern) --------

def google_access_token(client: httpx.Client, sa_key: Dict[str, Any],
                        scope: str = DRIVE_SCOPE) -> str:
    """Exchange a service-account JSON key for a short-lived access token via
    the OAuth2 JWT-bearer grant (RS256; no consent screens)."""
    now = int(time.time())
    assertion = jwt.encode(
        {"iss": sa_key["client_email"], "scope": scope, "aud": GOOGLE_TOKEN_URL,
         "iat": now, "exp": now + 3600},
        sa_key["private_key"], algorithm="RS256",
    )
    resp = client.post(GOOGLE_TOKEN_URL, data={
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    })
    if resp.status_code != 200:
        raise _die("google credential rejected (token exchange %d: %s)"
                   % (resp.status_code, resp.text[:200]))
    return resp.json()["access_token"]


def _list_folder_spreadsheets(client: httpx.Client, headers: Dict[str, str],
                              folder_id: str) -> Dict[str, str]:
    """name -> file id for every non-trashed spreadsheet in the folder."""
    found: Dict[str, str] = {}
    page_token: Optional[str] = None
    while True:
        params: Dict[str, Any] = {
            "q": "'%s' in parents and trashed = false and mimeType = '%s'"
                 % (folder_id, SPREADSHEET_MIME),
            "pageSize": 1000,
            "fields": "files(id,name),nextPageToken",
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        body = _check(client.get(DRIVE_API + "/files", headers=headers, params=params),
                      "drive list").json()
        for f in body.get("files", []):
            found[f["name"]] = f["id"]
        page_token = body.get("nextPageToken")
        if not page_token:
            return found


def cmd_create_sheets(args: argparse.Namespace) -> None:
    sa_key = _load_sa_key(args.sa_key)
    with httpx.Client(timeout=30.0) as client:
        token = google_access_token(client, sa_key, DRIVE_SCOPE)
        headers = {"Authorization": "Bearer %s" % token}
        existing = _list_folder_spreadsheets(client, headers, args.folder_id)
        created: List[str] = []
        for i in range(1, args.count + 1):
            name = "sheet-%03d" % i
            if name in existing:
                continue  # idempotent: only create the missing ones
            _check(client.post(DRIVE_API + "/files", headers=headers,
                               params={"supportsAllDrives": "true"},
                               json={"name": name, "mimeType": SPREADSHEET_MIME,
                                     "parents": [args.folder_id]}),
                   "drive create %s" % name)
            created.append(name)
    print(json.dumps({
        "folderId": args.folder_id,
        "existing": len(existing),
        "created": created,
        "total": len(existing) + len(created),
    }, indent=2))


# -- console: workspace -> connection -> environment -------------------------

async def provision(client: httpx.AsyncClient, *, provisioning_token: str,
                    workspace_name: str, creator_email: str, sa_key_value: str,
                    sandbox_template: str,
                    manifest: Optional[Dict[str, Any]] = None,
                    display_name: str = "Google Drive+Sheets",
                    environment_name: str = "sheets-demo") -> Dict[str, Any]:
    """Drive the console API end to end. `client` is any httpx.AsyncClient
    pointed at the console (a real base_url, or an ASGITransport in smokes).

    NOTE: the environment allowlists EXACTLY the three google tools. Do not
    add sandbox_exec here — the runtime binding auto-grants it from
    sandboxTemplate (branch demo-sandbox-grant)."""
    ws = _check(await client.post(
        "/workspaces",
        headers={"X-Convoy-Internal": provisioning_token},
        json={"name": workspace_name, "creatorEmail": creator_email},
    ), "create workspace").json()
    workspace_id, user_id = ws["workspaceId"], ws["userId"]
    as_user = {"X-Convoy-User": user_id}

    connection_req: Dict[str, Any] = {
        "kind": "mcp_managed",
        "provider": "google",
        "displayName": display_name,
        "secretValue": sa_key_value,  # write-only: the SA key file contents
    }
    if manifest is not None:
        connection_req["manifest"] = manifest
    conn = _check(await client.post(
        "/workspaces/%s/connections" % workspace_id,
        headers=as_user, json=connection_req,
    ), "create connection").json()

    env = _check(await client.post(
        "/workspaces/%s/environments" % workspace_id,
        headers=as_user,
        json={
            "name": environment_name,
            "description": "Sheets demo: sandbox script + append to Drive spreadsheets.",
            "backingType": "live",
            "sandboxTemplate": sandbox_template,
            "connections": [{
                "connectionId": conn["connectionId"],
                "toolAllowlist": list(GOOGLE_TOOLS),
            }],
        },
    ), "create environment").json()

    return {
        "workspaceId": workspace_id,
        "userId": user_id,
        "connectionId": conn["connectionId"],
        "manifestHash": conn["manifestHash"],
        "connectionTools": conn["tools"],
        "environmentId": env["environmentId"],
        "environmentVersion": env["version"],
        "policyHash": env["policyHash"],
    }


def cmd_provision(args: argparse.Namespace) -> None:
    sa_key_value = Path(args.sa_key).read_text()
    _load_sa_key(args.sa_key)  # fail fast on a malformed key file
    manifest = None
    if args.manifest_json:
        manifest = json.loads(Path(args.manifest_json).read_text())

    async def _run() -> Dict[str, Any]:
        async with httpx.AsyncClient(base_url=args.console.rstrip("/"),
                                     timeout=30.0) as client:
            return await provision(
                client,
                provisioning_token=args.provisioning_token,
                workspace_name=args.workspace_name,
                creator_email=args.creator_email,
                sa_key_value=sa_key_value,
                sandbox_template=args.sandbox_template,
                manifest=manifest,
            )

    print(json.dumps(asyncio.run(_run()), indent=2))


# -- control plane: POST /runs ------------------------------------------------

def build_run_request(args: argparse.Namespace) -> Dict[str, Any]:
    """A valid CreateRunRequest for the sheets demo (see the module-level
    assumptions block for every field the schema decides for us)."""
    body: Dict[str, Any] = {
        "goal": args.goal,
        "success_criteria": args.criteria or [
            "Every sheet-* spreadsheet in the target Drive folder has the "
            "sandbox script's result appended as a new row.",
        ],
        "environment_id": args.environment,
        "budget_usd": str(args.budget),
        "tools": [SANDBOX_TOOL] + list(GOOGLE_TOOLS),
        "max_children": args.max_children,
        "policy": {"require_plan_approval": args.require_plan_approval},
    }
    if args.model:
        body["model"] = args.model
    if args.run_id:
        body["run_id"] = args.run_id  # client-supplied id makes retries idempotent
    return body


def cmd_start_run(args: argparse.Namespace) -> None:
    base = args.control_plane.rstrip("/")
    headers = {
        "Authorization": "Bearer %s" % args.token,
        "x-actor-id": args.actor,
        "x-tenant-id": args.workspace,
    }
    resp = _check(httpx.post(base + "/runs", headers=headers,
                             json=build_run_request(args), timeout=30.0),
                  "create run")
    data = resp.json()
    print(json.dumps({
        "runId": data["run_id"],
        "status": data["status"],
        "eventsUrl": "%s/runs/%s/events" % (base, data["run_id"]),
    }, indent=2))


# -- CLI ----------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="demo_sheets_seed",
        description="One-command seeding for the Convoy sheets demo.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-sheets",
                       help="create sheet-001..sheet-NNN in a Drive folder (idempotent)")
    p.add_argument("--sa-key", required=True,
                   help="path to the Google service-account JSON key")
    p.add_argument("--folder-id", required=True,
                   help="Drive folder id (shared with the service account)")
    p.add_argument("--count", type=int, default=100,
                   help="target number of sheets (default 100)")
    p.set_defaults(func=cmd_create_sheets)

    p = sub.add_parser("provision",
                       help="console: workspace + google connection + sheets-demo environment")
    p.add_argument("--console", required=True, help="console API base URL")
    p.add_argument("--provisioning-token", required=True,
                   help="X-Convoy-Internal shared secret for workspace creation")
    p.add_argument("--sa-key", required=True,
                   help="path to the Google service-account JSON key (stored as the connection secret)")
    p.add_argument("--sandbox-template", default="demo-sbx",
                   help="sandbox template ref for the environment (default demo-sbx)")
    p.add_argument("--workspace-name", default="sheets-demo")
    p.add_argument("--creator-email", default="demo@useconvoy.dev")
    p.add_argument("--manifest-json", default="",
                   help="optional path to an explicit declared connection manifest "
                        "(otherwise the console builds one via its google connector)")
    p.set_defaults(func=cmd_provision)

    p = sub.add_parser("start-run", help="control plane: POST /runs and print the SSE URL")
    p.add_argument("--control-plane", required=True, help="control-plane base URL")
    p.add_argument("--workspace", required=True,
                   help="workspace id (used as x-tenant-id)")
    p.add_argument("--environment", required=True, help="environment id")
    p.add_argument("--goal", required=True, help="the run's goal text")
    p.add_argument("--criteria", action="append", default=[],
                   help="success criterion (repeatable; a demo default is used if omitted)")
    p.add_argument("--token", default="dev-token",
                   help="control-plane bearer token (CONVOY_DEV_TOKEN; default dev-token)")
    p.add_argument("--actor", default="seed-cli", help="x-actor-id (default seed-cli)")
    p.add_argument("--budget", default="25", help="budget in USD (default 25)")
    p.add_argument("--model", default="",
                   help="model override (default: deployment's configured default)")
    p.add_argument("--max-children", type=int, default=5,
                   help="max concurrent subagent children (default 5)")
    p.add_argument("--require-plan-approval", action="store_true",
                   help="require human plan approval (default off for the demo)")
    p.add_argument("--run-id", default="", help="client-supplied run id for idempotent retries")
    p.set_defaults(func=cmd_start_run)

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
