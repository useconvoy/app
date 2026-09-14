"""Real browser journey (Playwright + Chromium) against the built web app served by the real server with
three live simulated agents. Every screenshot is taken only after the page shows SETTLED data (rows,
values, terminal states), every workflow is submitted through the actual UI form and its effect is
verified through the API, and error paths assert the specific message the UI rendered (R57).
Skipped honestly when Chromium or web/dist is unavailable unless CONVOY_REQUIRE_BROWSER=1 (CI, R68).
Screenshots at 1400/900/400 px are written to server/tests/.artifacts/ (gitignored)."""

from __future__ import annotations

import glob
import os
import re
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from conftest import WEB, login
from fastapi.testclient import TestClient
from helpers import seed

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "web" / "dist"
ART = Path(__file__).parent / ".artifacts"
REQUIRED = os.environ.get("CONVOY_REQUIRE_BROWSER") == "1"  # R68: CI must never silently skip this
BOTS = ("ui-bot-1", "ui-bot-2", "ui-bot-3")


def _chromium_path() -> str | None:
    for c in [os.environ.get("CONVOY_CHROMIUM")] + glob.glob(
        "/opt/pw-browsers/chromium-*/chrome-linux/chrome"
    ):
        if c and Path(c).exists():
            return c
    return None


def L(label: str) -> re.Pattern[str]:
    """Exact field label; the form components append an aria-hidden ' *' to required labels."""
    return re.compile(rf"^{re.escape(label)}( \*)?$")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _unavailable(reason: str) -> None:
    if REQUIRED:
        pytest.fail(f"browser journey required (CONVOY_REQUIRE_BROWSER=1) but unavailable: {reason}")
    pytest.skip(reason)


@pytest.fixture(autouse=True)
def _dist_required():
    if not (DIST / "index.html").exists():
        _unavailable("web/dist not built (run `cd web && pnpm build`)")


@pytest.fixture()
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _unavailable("playwright not installed")
    with sync_playwright() as p:
        exe = _chromium_path()
        try:
            b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        except Exception as e:  # pragma: no cover
            _unavailable(f"chromium not launchable: {str(e)[:120]}")
        yield b
        b.close()


def _wait(fn, timeout_s=120, every=0.5):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(every)
    raise AssertionError("timeout waiting")


def _terminal(admin, op_id, timeout_s=150):
    return _wait(
        lambda: (lambda o: o if o["status"] in ("succeeded", "failed", "cancelled") else None)(
            admin.get(f"/api/v1/operations/{op_id}").json()
        ),
        timeout_s,
    )


@pytest.fixture()
def stack(settings, tmp_path):
    port = _free_port()
    settings.public_url = f"http://127.0.0.1:{port}"
    settings.web_dist = DIST
    settings.heartbeat_interval_s = 1
    from convoy_server.app import create_app

    app = create_app(settings, start_scheduler=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    admin = login(TestClient(app))
    s = seed(admin)
    from convoy_agent.agent import Agent, enroll

    agents, threads, ids = [], [], []
    for i, name in enumerate(BOTS, 1):
        tok = admin.post("/api/v1/enrollments", json={"label": name, "simulated": True}, headers=WEB).json()[
            "token"
        ]
        d = tmp_path / name
        res = enroll(d, server=settings.public_url, token=tok, name=name, simulate=True, seed=i)
        a = Agent(d, robot_sim=True)
        t = threading.Thread(target=a.run, daemon=True)
        t.start()
        agents.append(a)
        threads.append(t)
        ids.append(res["device_id"])
    for did in ids:
        _wait(lambda did=did: admin.get(f"/api/v1/devices/{did}").json()["status"] == "online", 30)
    yield {
        "base": settings.public_url,
        "admin": admin,
        "seed": s,
        "device_ids": ids,
        "device_id": ids[0],
        "agents": agents,
    }
    for a in agents:
        a.stop.set()
    for t in threads:
        t.join(timeout=20)
    server.should_exit = True
    th.join(timeout=5)


def _login(page, base):
    page.goto(f"{base}/login")
    page.get_by_label(L("Email")).fill("admin@example.com")
    page.get_by_label(L("Password")).fill("admin-password-1")
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{base}/", timeout=10000)


def _shot(page, name: str) -> None:
    ART.mkdir(exist_ok=True)
    page.mouse.move(0, 0)  # no lingering hover tooltips/cursors in captures
    page.screenshot(path=str(ART / f"{name}.png"), full_page=True)


def _confirm(page, label: str) -> None:
    """Click an action button, then its confirmation in the modal (same label)."""
    page.get_by_role("button", name=label, exact=True).first.click()
    page.wait_for_selector("[role=dialog]", timeout=5000)
    page.locator("[role=dialog]").get_by_role("button", name=label, exact=True).click()


@pytest.mark.timeout(420)
def test_browser_journey_forms_conflicts_and_screenshots(browser, stack):
    base, admin, s, ids = stack["base"], stack["admin"], stack["seed"], stack["device_ids"]
    dev = ids[0]
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    # ---- login: wrong password renders the alert, then success shows the simulator banner ----
    page.goto(f"{base}/login")
    page.get_by_label(L("Email")).fill("admin@example.com")
    page.get_by_label(L("Password")).fill("wrong-password")
    page.get_by_role("button", name="Sign in").click()
    alert = page.locator("[role=alert]").first
    alert.wait_for(timeout=5000)
    assert "invalid" in alert.inner_text().lower() or "401" in alert.inner_text()
    page.get_by_label(L("Password")).fill("admin-password-1")
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(f"{base}/", timeout=10000)
    page.wait_for_selector("text=/simulated/i", timeout=10000)
    page.wait_for_selector("text=/3 online/", timeout=15000)  # overview cards settled on the live fleet
    page.wait_for_selector("text=No operations yet.", timeout=10000)
    _shot(page, "overview-1400")
    # ---- fleet: three online devices; the enrollment command shell-quotes a label with spaces (R74) ----
    page.get_by_role("link", name="Fleet").first.click()
    for b in BOTS:
        page.wait_for_selector(f"text={b}", timeout=10000)
    page.locator(".badge", has_text="online").first.wait_for(timeout=10000)
    _shot(page, "fleet-1400")
    page.get_by_role("button", name="Enroll device").click()
    page.wait_for_selector("text=Enroll a device", timeout=5000)
    page.get_by_label(L("Label")).fill("Container review 1")
    _shot(page, "fleet-enroll-1400")  # captured BEFORE a token exists: no secret ever lands in an image
    page.get_by_role("button", name="Create token").click()
    page.wait_for_selector("text=convoy-agent enroll", timeout=10000)
    dialog_text = page.locator("[role=dialog]").inner_text()
    token = re.search(r"--token\s+(\S+)", dialog_text).group(1)
    masked = dialog_text.replace(token, "<redacted>")  # the exact secret is removed everywhere (both copies)
    assert token not in masked and "<redacted>" in masked
    assert "'Container review 1'" in masked, masked  # quoted as one argument, never three
    assert "--name Container review 1" not in masked
    assert admin.get("/api/v1/enrollments").json()  # the token exists only in this throwaway test server
    page.keyboard.press("Escape")
    # ---- device: deploy the baseline THROUGH THE FORM; the real agent runs it to success ----
    page.get_by_role("link", name=BOTS[0]).first.click()
    page.wait_for_selector("text=Deploy", timeout=10000)
    page.get_by_role("button", name="Deploy", exact=True).click()
    page.wait_for_selector(f"text=Deploy to {BOTS[0]}", timeout=5000)
    page.get_by_label(L("Release")).select_option(s["release_id"])
    page.get_by_label(L("Qualification plan")).select_option(s["plan_id"])
    page.wait_for_selector(
        "text=model_weights_mb", timeout=15000
    )  # the budget BODY has loaded, not the heading
    _shot(page, "device-deploy-form-1400")
    page.get_by_role("button", name="Create deploy operation").click()
    page.wait_for_selector("text=Deploy operation created", timeout=10000)
    op = _wait(
        lambda: next(
            (o for o in admin.get(f"/api/v1/devices/{dev}/operations").json() if o["type"] == "deploy"), None
        ),
        10,
    )
    o = _terminal(admin, op["id"])
    assert o["status"] == "succeeded", o
    assert (
        o["outcome"]["evidence"]["probation"]["elapsed_s"] >= 2
        and o["outcome"]["evidence"]["eval"]["eval_result_id"]
    )
    page.reload()
    page.wait_for_selector("text=Deploy", timeout=10000)
    page.wait_for_selector(f"text={s['release_id']}", timeout=15000)
    page.get_by_role("tab", name="Operations").first.click()
    page.wait_for_selector("text=/succeeded/i", timeout=15000)
    _shot(page, "device-after-deploy-1400")
    # ---- conflict: a standalone eval (through the form) holds the reservation; a deploy submit renders the
    #      server's 409 with the active operation linked (R57: assert the specific message) ----
    # dispatch is paused so the eval operation deterministically holds the reservation until we resume
    assert (
        admin.post(
            "/api/v1/admin/dispatch", json={"paused": True, "reason": "ui-test"}, headers=WEB
        ).status_code
        == 200
    )
    page.get_by_role("button", name="Run eval").click()
    page.wait_for_selector("text=Run eval", timeout=5000)
    page.locator("[role=dialog]").get_by_label(L("Plan")).select_option(s["plan_id"])
    page.locator("[role=dialog]").get_by_role("checkbox", name="Record as baseline").check()
    page.locator("[role=dialog]").get_by_role("button", name="Create eval operation").click()
    page.wait_for_selector("text=/eval operation created/i", timeout=10000)
    eval_op = _wait(
        lambda: next(
            (o for o in admin.get(f"/api/v1/devices/{dev}/operations").json() if o["type"] == "eval"), None
        ),
        10,
    )
    page.get_by_role("button", name="Deploy", exact=True).click()
    page.wait_for_selector(f"text=Deploy to {BOTS[0]}", timeout=5000)
    page.get_by_label(L("Release")).select_option(s["candidate_release_id"])
    page.get_by_label(L("Qualification plan")).select_option(s["candidate_plan_id"])
    page.get_by_role("button", name="Create deploy operation").click()
    err = page.locator("[role=dialog] [role=alert]").first
    err.wait_for(timeout=10000)
    text = err.inner_text()
    assert "unsettled" in text and eval_op["id"] in text, text
    assert (
        page.locator("[role=dialog]").get_by_label(L("Release")).input_value() == s["candidate_release_id"]
    )  # values preserved
    _shot(page, "device-conflict-1400")
    page.keyboard.press("Escape")
    assert (
        admin.post(
            "/api/v1/admin/dispatch", json={"paused": False, "reason": "ui-test"}, headers=WEB
        ).status_code
        == 200
    )
    eo = _terminal(admin, eval_op["id"])
    assert eo["status"] == "succeeded" and eo["grant_id"], (
        eo
    )  # R77: standalone eval took a grant and completed
    # ---- releases: the wizard creates a release through the UI; bounds are enforced inline (R58) ----
    page.get_by_role("link", name="Releases").first.click()
    page.wait_for_selector("text=sim-qwen2.5-1.5b", timeout=10000)
    _shot(page, "releases-1400")
    page.get_by_role("button", name="New release").click()
    page.wait_for_selector("text=Model source", timeout=5000)
    page.get_by_label(L("Source")).select_option("fixture")
    page.get_by_label(L("Fixture")).select_option(index=1)
    page.get_by_role("button", name="Resolve").click()
    try:
        page.locator(".badge", has_text="resolved").first.wait_for(timeout=15000)
    except Exception as e:  # pragma: no cover - diagnostics for a failed resolve
        raise AssertionError(
            f"resolve did not settle: {page.locator('[role=dialog]').inner_text()[:1500]}"
        ) from e
    _wait(lambda: page.get_by_role("button", name="Next").is_enabled(), 15)
    page.get_by_role("button", name="Next").click()  # runtime
    page.get_by_label(L("Build recipe")).select_option(s["recipe_id"])
    page.get_by_label(L("Runtime artifact (optional)")).select_option(s["artifact_id"])
    page.get_by_role("button", name="Next").click()  # template
    page.get_by_role("button", name="Next").click()  # config
    page.get_by_label(L("n_predict")).fill("9999")  # above ctx_size: inline error blocks Next
    page.wait_for_selector("text=/n_predict/i", timeout=5000)
    assert page.get_by_role("button", name="Next").is_disabled()
    page.get_by_label(L("n_predict")).fill("128")
    page.get_by_label(L("gpu_layers")).fill("0")
    assert page.get_by_label(L("fit")).get_attribute("readonly") is not None  # fixed settings are read-only
    _shot(page, "release-wizard-config-1400")
    page.get_by_role("button", name="Next").click()  # eval set
    page.get_by_label(L("Eval set (optional)")).select_option(s["eval_set_id"])
    page.get_by_role("button", name="Next").click()  # review
    page.get_by_label(L("Name")).fill("ui-release")
    page.get_by_label(L("Version")).fill("0.0.1")
    page.get_by_role("button", name="Create release").click()
    page.wait_for_selector("text=ui-release", timeout=15000)
    rel = _wait(
        lambda: next((r for r in admin.get("/api/v1/releases").json() if r["name"] == "ui-release"), None), 10
    )
    full = admin.get(f"/api/v1/releases/{rel['id']}").json()
    assert full["spec"]["config"]["gpu_layers"] == 0 and isinstance(full["spec"]["config"]["gpu_layers"], int)
    # ---- eval set through the case editor; plan with an out-of-bounds freshness refused inline (R60) ----
    page.get_by_role("tab", name="Eval sets").first.click()
    page.get_by_role("button", name="New eval set").click()
    page.wait_for_selector("text=Case editor", timeout=5000)
    page.get_by_label(L("Name")).fill("ui-cases")
    page.get_by_label(L("Version")).fill("1")
    page.get_by_label(L("id")).first.fill("cap-1")
    page.get_by_label(L("prompt")).first.fill("Capital of France? One word.")
    page.get_by_label(L("expected")).first.fill("Paris")
    page.get_by_role("button", name="Add case").click()
    page.get_by_label(L("id")).nth(1).fill("cap-2")
    page.get_by_label(L("prompt")).nth(1).fill("Capital of Japan? One word.")
    page.get_by_label(L("expected")).nth(1).fill("Tokyo")
    page.locator("[role=dialog]").get_by_role("button", name="Create", exact=True).click()
    page.wait_for_selector("text=ui-cases", timeout=10000)
    es = _wait(
        lambda: next((e for e in admin.get("/api/v1/eval-sets").json() if e["name"] == "ui-cases"), None), 10
    )
    assert [c["id"] for c in admin.get(f"/api/v1/eval-sets/{es['id']}").json()["cases"]] == ["cap-1", "cap-2"]
    page.get_by_role("link", name="Evals").first.click()
    page.wait_for_selector("text=/passed/i", timeout=15000)  # settled: the real eval results are listed
    _shot(page, "evals-1400")
    page.get_by_role("tab", name="Plans").first.click()
    page.wait_for_selector(f"text={s['plan_id']}", timeout=10000)
    page.get_by_role("button", name="New plan").click()
    page.wait_for_selector("text=Create plan", timeout=5000)
    page.get_by_label(L("Name")).fill("ui-plan")
    page.get_by_label(L("Release")).select_option(rel["id"])
    page.get_by_label(L("fresh_eval_max_age_s")).fill("0")
    page.wait_for_selector("text=/fresh_eval_max_age_s/i", timeout=5000)
    assert page.get_by_role("button", name="Create plan").is_disabled()  # 0 is below the API minimum of 60
    page.get_by_label(L("fresh_eval_max_age_s")).fill("60")
    page.get_by_role("button", name="Create plan").click()
    page.wait_for_selector("text=ui-plan", timeout=10000)
    plan = _wait(
        lambda: next((p for p in admin.get("/api/v1/plans").json() if p["name"] == "ui-plan"), None), 10
    )
    assert plan["sample_policy"]["fresh_eval_max_age_s"] == 60 and plan["workload"]["ordering"] == "fixed"
    # ---- evidence detail: per-case hashes, methods and timings are populated (R76) ----
    ev = admin.get(f"/api/v1/evals?device_id={dev}").json()[0]
    page.goto(f"{base}/evals/{ev['id']}")
    page.wait_for_selector("text=/independently.rescored/", timeout=10000)
    page.wait_for_selector("text=/server.recomputed/", timeout=10000)
    _shot(page, "eval-detail-1400")
    # ---- remaining sections settle on real rows or explicit honest states ----
    page.get_by_role("link", name="Runs").first.click()
    page.locator(".badge", has_text="succeeded").first.wait_for(timeout=15000)
    _shot(page, "runs-1400")
    # usage: close the agents' counting interval now (normally every 60 s) so the page shows real counters
    for a in stack["agents"]:
        a._record_usage(force=True)
    _wait(lambda: (admin.get("/api/v1/usage").json()["totals"].get("inference_requests") or 0) > 0, 60)
    page.get_by_role("link", name="Usage", exact=True).first.click()
    page.wait_for_selector("text=/inference requests/i", timeout=15000)
    page.locator(".num", has_text=re.compile(r"^[1-9]\d*$")).first.wait_for(
        timeout=15000
    )  # positive counters
    # the loss-range evidence lives behind a disclosure: open it as a user would, then assert the
    # full statement (not just the collapsed summary) before capturing
    loss = page.locator("main details", has=page.locator("summary", has_text="Loss ranges"))
    assert loss.get_attribute("open") is None
    loss.locator("summary").click()
    page.wait_for_function(
        "Array.from(document.querySelectorAll('main details[open] summary'))"
        ".some(el => el.textContent.includes('Loss ranges'))",
        timeout=5000,
    )
    loss.get_by_text(
        re.compile(r"^No loss reported\. This only means no device has reported a dropped spool range")
    ).wait_for(timeout=15000)
    _shot(page, "usage-1400")
    page.get_by_role("link", name="Settings").first.click()
    page.wait_for_selector("text=admin@example.com", timeout=15000)
    _shot(page, "settings-1400")
    # project hardware validation: /overview and /settings carry the same partial-scope wording (never
    # the old blanket denial), and the Environment panel labels it as project scope
    for path in ("/api/v1/overview", "/api/v1/settings"):
        hv = admin.get(path).json()["hardware_validation"]
        assert hv.startswith("Partial physical validation") and "deferred" not in hv, (path, hv)
    page.get_by_text("Retention & environment", exact=True).first.click()
    page.wait_for_selector("text=Project hardware validation", timeout=5000)
    env = page.locator("main").locator("section:has(#environment)")
    assert env.get_by_text("all-eleven live continuity remain pending", exact=False).first.is_visible()
    assert page.locator("main").get_by_text("validation is deferred", exact=False).count() == 0
    page.get_by_text("Jetson guide", exact=True).first.click()
    page.wait_for_selector("text=requires qualification on your board", timeout=5000)
    assert page.locator("main").get_by_text("validation is deferred", exact=False).count() == 0
    # ---- responsive: 900 and 400 px, no horizontal overflow; key status visible on mobile ----
    for width in (900, 400):
        page.set_viewport_size({"width": width, "height": 800})
        page.goto(f"{base}/fleet")
        page.wait_for_selector(f"text={BOTS[2]}", timeout=10000)
        page.locator(".badge", has_text="online").first.wait_for(timeout=10000)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1, f"horizontal overflow at {width}px: {overflow}px"
        _shot(page, f"fleet-{width}")
        page.goto(f"{base}/fleet/{dev}")
        page.wait_for_selector(f"text={s['release_id']}", timeout=10000)
        _shot(page, f"device-{width}")
    # ---- keyboard: polling must not steal focus from an open form field (R75) ----
    page.set_viewport_size({"width": 1400, "height": 900})
    page.goto(f"{base}/fleet/{dev}")
    page.wait_for_selector("text=Deploy", timeout=10000)
    page.get_by_role("button", name="Edit settings").click()
    page.wait_for_selector("[role=dialog]", timeout=5000)
    name_input = page.locator("[role=dialog]").get_by_label(L("Name"))
    name_input.click()
    name_input.fill("ui-bot-1 renamed")
    with page.expect_response(
        lambda r: f"/api/v1/devices/{dev}" in r.url and r.request.method == "GET", timeout=30000
    ):
        pass  # a REAL background poll of this device page lands while the field is focused
    page.wait_for_timeout(200)
    assert page.evaluate("document.activeElement === document.querySelector('[role=dialog] input:focus')")
    assert name_input.input_value() == "ui-bot-1 renamed"  # typed value survives the refresh
    page.keyboard.press("Escape")
    page.goto(f"{base}/")
    page.keyboard.press("Tab")
    assert page.evaluate(
        "document.activeElement && (document.activeElement.textContent || document.activeElement.tagName)"
    )
    # the only acceptable console errors are the expected 401s (session probe before login, wrong password)
    # and the deliberate 409 conflict
    unexpected = [e for e in errors if "favicon" not in e.lower() and "401" not in e and "409" not in e]
    assert not unexpected, unexpected
    ctx.close()


@pytest.mark.timeout(480)
def test_browser_schedule_edit_and_multi_target_rollout(browser, stack):
    """UI-driven: schedule create (server-rejected cron rendered, then valid), edit -> new revision with
    the timezone preserved (R56/R59); a three-target rollout with one explicit canary: Start, real canary
    qualification, Promote, SERIAL expansion over the two remaining devices, final completed state."""
    base, admin, s, ids = stack["base"], stack["admin"], stack["seed"], stack["device_ids"]
    # every device runs the baseline first (real agents, API-issued)
    ops = [
        admin.post(
            f"/api/v1/devices/{d}/deploy",
            json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
            headers=WEB,
        ).json()
        for d in ids
    ]
    for o in ops:
        assert _terminal(admin, o["id"])["status"] == "succeeded"
    for d in ids:
        _wait(
            lambda d=d: (
                admin.get(f"/api/v1/devices/{d}").json()["observed_active_release_id"] == s["release_id"]
            ),
            60,
        )
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    _login(page, base)
    # ---- schedule: invalid cron is refused by the server and shown; then created; then edited ----
    page.goto(f"{base}/schedules")
    page.get_by_role("button", name="New schedule").click()
    page.wait_for_selector("text=New schedule", timeout=5000)
    page.get_by_label(L("Name")).fill("nightly eval")
    page.get_by_label(L("Kind")).select_option("eval")
    page.get_by_label(L("Cron (5 fields)")).fill("*/2 * * * *")
    page.get_by_label(L("Timezone (IANA)")).select_option("America/Los_Angeles")
    page.get_by_label(L("Plan")).select_option(s["plan_id"])
    page.get_by_label(L("Target")).select_option("devices")
    page.get_by_role("checkbox", name=BOTS[0]).first.check()
    page.get_by_role("button", name="Create", exact=True).click()
    err = page.locator("[role=dialog] [role=alert]").first
    err.wait_for(timeout=10000)
    assert "5 minutes" in err.inner_text(), err.inner_text()
    page.get_by_label(L("Cron (5 fields)")).fill("30 2 * * *")
    page.get_by_role("button", name="Create", exact=True).click()
    page.wait_for_selector("text=nightly eval", timeout=10000)
    sch = _wait(
        lambda: next((x for x in admin.get("/api/v1/schedules").json() if x["name"] == "nightly eval"), None),
        10,
    )
    assert (
        sch["timezone"] == "America/Los_Angeles"
        and sch["payload"]["plan_id"] == s["plan_id"]
        and sch["revision"] == 1
    )
    # R59: an API-created schedule in a zone this browser lists under another alias (Asia/Kolkata vs
    # Asia/Calcutta) must keep its saved zone through a UI edit
    kolkata = admin.post(
        "/api/v1/schedules",
        json={
            "name": "kolkata health",
            "kind": "health",
            "cron": "0 3 * * *",
            "timezone": "Asia/Kolkata",
            "target": {"device_ids": [ids[0]]},
        },
        headers=WEB,
    ).json()
    page.reload()
    page.get_by_text("kolkata health", exact=True).first.click()
    page.get_by_role("button", name="Edit", exact=True).click()
    page.wait_for_selector("text=Edit kolkata health", timeout=5000)
    assert page.get_by_label(L("Timezone (IANA)")).input_value() == "Asia/Kolkata"
    page.get_by_label(L("Name")).fill("kolkata health (edited)")
    page.get_by_role("button", name="Save revision").click()
    page.wait_for_selector("text=kolkata health (edited)", timeout=10000)
    k2 = admin.get(f"/api/v1/schedules/{kolkata['id']}").json()
    assert k2["timezone"] == "Asia/Kolkata" and k2["revision"] == 2, k2
    page.keyboard.press("Escape")
    page.get_by_text("nightly eval", exact=True).first.click()
    page.wait_for_selector("text=/\\d{4}-\\d{2}-\\d{2}T02:30/", timeout=10000)  # real civil rows, not labels
    page.wait_for_selector("text=/\\d{4}-\\d{2}-\\d{2}T\\d{2}:30:00Z/", timeout=10000)  # their UTC instants
    prev = admin.get(f"/api/v1/schedules/{sch['id']}/preview").json()["next"]
    assert len(prev) == 5 and all(p["civil"].endswith("T02:30") and p["utc"] for p in prev)
    page.get_by_role("button", name="Edit", exact=True).click()
    page.wait_for_selector("text=Edit nightly eval", timeout=5000)
    page.get_by_label(L("Name")).fill("nightly eval (renamed)")
    page.get_by_role("button", name="Save revision").click()
    page.wait_for_selector("text=nightly eval (renamed)", timeout=10000)
    sch2 = admin.get(f"/api/v1/schedules/{sch['id']}").json()
    assert (
        sch2["revision"] == 2 and sch2["timezone"] == "America/Los_Angeles" and sch2["cron"] == "30 2 * * *"
    )
    _shot(page, "schedules-1400")
    # ---- rollout: three targets, explicit canary, Start -> awaiting_promotion -> Promote -> serial ----
    page.goto(f"{base}/rollouts")
    page.get_by_role("button", name="New rollout").click()
    page.wait_for_selector("text=New rollout", timeout=5000)
    page.get_by_label(L("Name")).fill("candidate canary")
    page.get_by_label(L("Plan")).select_option(s["candidate_plan_id"])
    page.get_by_label(L("Target")).select_option("devices")
    assert page.get_by_role("button", name="Create draft").is_disabled()  # no target/canary yet (R62)
    for b in BOTS:
        page.get_by_role("checkbox", name=b).first.check()
    assert page.get_by_role("button", name="Create draft").is_disabled()  # targets but no canary
    page.get_by_role("checkbox", name=BOTS[0]).nth(1).check()  # canary
    page.get_by_role("button", name="Create draft").click()
    page.wait_for_selector("text=candidate canary", timeout=10000)
    ro = _wait(
        lambda: next(
            (x for x in admin.get("/api/v1/rollouts").json() if x["name"] == "candidate canary"), None
        ),
        10,
    )
    assert ro["status"] == "draft" and ro["targets"] == ids and ro["canaries"] == [ids[0]]
    page.goto(f"{base}/rollouts/{ro['id']}")
    page.wait_for_selector("text=/draft/i", timeout=10000)
    _confirm(page, "Start")
    page.wait_for_selector("text=/canary/i", timeout=10000)
    r = _wait(
        lambda: (lambda x: x if x["status"] in ("awaiting_promotion", "completed", "failed") else None)(
            admin.get(f"/api/v1/rollouts/{ro['id']}").json()
        ),
        180,
        1.0,
    )
    assert r["status"] == "awaiting_promotion", r
    assert (
        r["device_states"][ids[1]]["status"] == "queued" and r["device_states"][ids[2]]["status"] == "queued"
    )
    page.reload()
    page.wait_for_selector("text=/awaiting.promotion/i", timeout=15000)
    page.wait_for_selector("text=/elapsed/i", timeout=15000)  # probation evidence rendered from the operation
    _shot(page, "rollout-canary-passed-1400")
    _confirm(page, "Promote")
    page.wait_for_selector("text=/expanding/i", timeout=15000)
    r = _wait(
        lambda: (lambda x: x if x["status"] in ("completed", "failed") else None)(
            admin.get(f"/api/v1/rollouts/{ro['id']}").json()
        ),
        240,
        1.0,
    )
    assert r["status"] == "completed", r
    assert all(r["device_states"][d]["status"] == "passed" for d in ids)
    # serial expansion: the third device's operation was created only after the second one settled
    o2 = admin.get(f"/api/v1/operations/{r['device_states'][ids[1]]['operation_id']}").json()
    o3 = admin.get(f"/api/v1/operations/{r['device_states'][ids[2]]['operation_id']}").json()
    assert o2["terminal_at"] <= o3["created_at"], (o2["terminal_at"], o3["created_at"])
    for d in ids:
        assert (
            admin.get(f"/api/v1/devices/{d}").json()["observed_active_release_id"]
            == s["candidate_release_id"]
        )
    page.reload()
    page.wait_for_selector("text=/completed/i", timeout=15000)
    _shot(page, "rollout-completed-1400")
    page.goto(f"{base}/rollouts")
    page.wait_for_selector("text=/completed/i", timeout=15000)
    _shot(page, "rollouts-1400")
    ctx.close()


# --------------------------------------------------------------------------- redesign gallery
REDESIGN_WIDTHS = (1440, 1280, 1024, 768, 390, 320)
PHONE_ONLY = (1440, 390)  # secondary surfaces: one desktop and one phone capture keep the run bounded
NARROWEST = (1440, 1280, 1024, 768, 390, 320)


def _settle(page, target: str, timeout: int = 20000) -> None:
    """A surface is settled when a named record (or a finished, explicit empty state) is present
    inside <main>, the self-hosted fonts have finished loading, and nothing in <main> is still
    busy or a skeleton. Headings never count: they render before any data arrives."""
    page.locator("main").locator(target).first.wait_for(timeout=timeout)
    assert page.evaluate("document.fonts.ready.then(() => document.fonts.status)") == "loaded"
    page.wait_for_function(
        "!document.querySelector('main [aria-busy=\"true\"], main .skeleton')", timeout=timeout
    )


def _no_overflow(page, name: str, width: int) -> None:
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, f"horizontal overflow on {name} at {width}px: {overflow}px"


@pytest.mark.timeout(900)
def test_browser_redesign_surfaces_and_responsive_gallery(browser, stack):
    """Every major surface of the redesigned console, captured only once a named live record (or an
    explicit finished empty state) has rendered inside <main>, at 1440/1280/1024/768/390/320: list
    pages, detail pages (release, eval, rollout, trace, schedule), every Releases/Evals/Settings tab,
    the device tabs, the labelled synthetic state gallery. Also: no horizontal body overflow, no
    console errors, no external requests (the five IBM Plex faces come from the app origin), mobile
    navigation focus rules, chart keyboard access on real data, the request-identity guard against a
    delayed response from an earlier query, and a bounded viewer (read-only) pass."""
    base, admin, s, ids = stack["base"], stack["admin"], stack["seed"], stack["device_ids"]
    dev = ids[0]
    # real data for the screens, driven through the API so the captures show settled operations,
    # evals, usage, telemetry, spans; a rollout in its created state; a schedule that is paused
    op = admin.post(
        f"/api/v1/devices/{dev}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    assert _terminal(admin, op["id"])["status"] == "succeeded"
    _wait(lambda: len(admin.get(f"/api/v1/devices/{dev}/telemetry?limit=50").json()) >= 5, 60)
    for a in stack["agents"]:
        a._record_usage(force=True)
    _wait(lambda: admin.get("/api/v1/usage").json()["totals"].get("inference_requests"), 60)
    ev = _wait(lambda: (admin.get(f"/api/v1/evals?device_id={dev}").json() or [None])[0], 30)
    spans = _wait(lambda: admin.get(f"/api/v1/devices/{dev}/spans?limit=500").json() or None, 60)
    trace_id = spans[0]["trace_id"]
    rollout = admin.post(
        "/api/v1/rollouts",
        json={
            "name": "redesign-rollout",
            "plan_id": s["plan_id"],
            "target": {"device_ids": ids},
            "canary_device_ids": [ids[0]],
        },
        headers=WEB,
    )
    assert rollout.status_code == 201, rollout.text
    rollout = rollout.json()
    schedule = admin.post(
        "/api/v1/schedules",
        json={
            "name": "redesign-paused-health",
            "kind": "health",
            "cron": "10 12 * * *",
            "timezone": "UTC",
            "target": {"device_ids": [dev]},
        },
        headers=WEB,
    )
    assert schedule.status_code == 201, schedule.text
    schedule = schedule.json()
    assert schedule["next_run_at"]  # the scheduler persisted a next-due cursor at creation
    assert admin.post(f"/api/v1/schedules/{schedule['id']}/pause", headers=WEB).status_code == 200
    paused = admin.get(f"/api/v1/schedules/{schedule['id']}").json()
    assert paused["paused_at"] and paused["next_run_at"] == schedule["next_run_at"]  # cursor kept

    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()
    console_errors: list[str] = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))
    requests: list[str] = []
    fonts: dict[str, int] = {}
    page.on("request", lambda r: requests.append(r.url))
    page.on("response", lambda r: fonts.__setitem__(r.url, r.status) if "/fonts/" in r.url else None)

    # unauthenticated login screen first
    page.goto(f"{base}/login")
    page.get_by_role("button", name="Sign in").wait_for(timeout=10000)
    assert page.evaluate("document.fonts.ready.then(() => document.fonts.status)") == "loaded"
    _shot(page, "redesign-login-1440")
    _login(page, base)
    _settle(page, "text=/3 online/")
    # the only console errors allowed are the session probe's 401s from the unauthenticated login
    # screen; everything after sign-in must be clean
    pre_login_errors = list(console_errors)
    assert all("401" in e for e in pre_login_errors), pre_login_errors

    rel = s["release_id"]
    bot = BOTS[2]
    surfaces: list[tuple[str, str, str, tuple[int, ...]]] = [
        # name, path, settled target inside <main>, widths
        ("overview", "/", "text=/3 online/", NARROWEST),
        ("fleet", "/fleet", f"a:text-is('{bot}')", NARROWEST),
        ("device", f"/fleet/{dev}", f"text={rel}", NARROWEST),
        (
            "device-telemetry",
            f"/fleet/{dev}?tab=telemetry",
            "text=/Latest \\d+ samples, from/",
            REDESIGN_WIDTHS[:5],
        ),
        ("device-operations", f"/fleet/{dev}?tab=operations", ".badge:has-text('succeeded')", PHONE_ONLY),
        (
            "device-logs",
            f"/fleet/{dev}?tab=logs",
            "[role=log], .state-box:has-text('No log entries.')",
            PHONE_ONLY,
        ),
        ("device-traces", f"/fleet/{dev}?tab=traces", "a[href*='/runs/traces/']", PHONE_ONLY),
        ("device-evals", f"/fleet/{dev}?tab=evals", ".badge:has-text('passed')", PHONE_ONLY),
        ("usage", "/usage", "text=/\\d+ of \\d+ days reported/", NARROWEST),
        ("runs", "/runs", ".badge:has-text('succeeded')", NARROWEST),
        ("runs-failures", "/runs?tab=failures", "tbody tr:not(:has(.skeleton))", PHONE_ONLY),
        ("trace", f"/runs/traces/{trace_id}", "text=/error span/", PHONE_ONLY),
        ("releases", "/releases", "a:text-is('sim-qwen2.5-1.5b')", NARROWEST),
        (
            "release-detail",
            f"/releases/{rel}",
            "section:has(#rel-plans) tbody tr:not(:has(.skeleton))",
            PHONE_ONLY,
        ),
        ("releases-recipes", "/releases?tab=recipes", "tbody tr:not(:has(.skeleton))", (1440,)),
        ("releases-artifacts", "/releases?tab=artifacts", "tbody tr:not(:has(.skeleton))", (1440,)),
        ("releases-evalsets", "/releases?tab=evalsets", "tbody tr:not(:has(.skeleton))", (1440,)),
        ("evals", "/evals", ".badge:has-text('passed')", NARROWEST),
        ("evals-plans", "/evals?tab=plans", f"text={s['plan_id']}", PHONE_ONLY),
        ("evals-compare", "/evals?tab=compare", "select:has(option[value]:not([value='']))", (1440,)),
        ("eval-detail", f"/evals/{ev['id']}", "text=/server recomputed/", PHONE_ONLY),
        ("rollouts", "/rollouts", "a:text-is('redesign-rollout')", NARROWEST),
        ("rollout-detail", f"/rollouts/{rollout['id']}", f"a:text-is('{BOTS[0]}')", PHONE_ONLY),
        ("schedules", "/schedules", "text='Not dispatching (paused)'", NARROWEST),
        (
            "schedule-detail",
            f"/schedules?schedule={schedule['id']}",
            "table:has(caption:text-is('Cron preview')) tbody tr",
            PHONE_ONLY,
        ),
        ("settings", "/settings", "text=admin@example.com", NARROWEST),
        ("settings-tokens", "/settings?tab=tokens", "tbody tr:not(:has(.skeleton))", PHONE_ONLY),
        ("settings-installation", "/settings?tab=installation", "#dispatch", PHONE_ONLY),
        ("settings-backups", "/settings?tab=backups", "tbody tr:not(:has(.skeleton))", (1440,)),
        ("settings-retention", "/settings?tab=retention", "text='Kept without a window'", PHONE_ONLY),
        ("settings-jetson", "/settings?tab=jetson", "text=requires qualification on your board", (1440,)),
        (
            "settings-compat",
            "/settings?tab=compat",
            "section:has(#compat-matrix) tbody tr:not(:has(.skeleton))",
            (1440,),
        ),
        ("gallery", "/gallery", ".chart-svg", PHONE_ONLY),
    ]
    captured: set[str] = set()
    for width in REDESIGN_WIDTHS:
        page.set_viewport_size({"width": width, "height": 900})
        for name, path, target, widths in surfaces:
            if width not in widths:
                continue
            page.goto(f"{base}{path}")
            _settle(page, target)
            _no_overflow(page, name, width)
            _shot(page, f"redesign-{name}-{width}")
            captured.add(f"{name}-{width}")
        if width == 390:
            # mobile navigation: closed menu hides its links; open moves focus inside; Escape closes
            # and returns focus to the toggle; the page behind was inert while open
            page.goto(f"{base}/")
            _settle(page, "text=/3 online/")
            assert page.evaluate("document.getElementById('sidebar').hidden") is True
            page.get_by_role("button", name="Open navigation menu").click()
            page.get_by_role("link", name="Usage", exact=True).wait_for(timeout=5000)
            assert page.evaluate("document.getElementById('sidebar').contains(document.activeElement)")
            assert page.evaluate("!!document.querySelector('[inert] main')")
            _shot(page, "redesign-nav-open-390")
            page.keyboard.press("Escape")
            page.wait_for_function("document.getElementById('sidebar').hidden === true", timeout=5000)
            assert page.evaluate("document.activeElement.getAttribute('aria-controls')") == "sidebar"
            assert page.evaluate("!document.querySelector('[inert]')")
            # the Close control returns focus too (the page was inert until the close committed)
            page.get_by_role("button", name="Open navigation menu").click()
            page.get_by_role("button", name="Close menu").wait_for(timeout=5000)
            page.get_by_role("button", name="Close menu").click()
            page.wait_for_function("document.getElementById('sidebar').hidden === true", timeout=5000)
            assert page.evaluate("document.activeElement.getAttribute('aria-controls')") == "sidebar"
            # and a backdrop click (the sidebar is 300px wide at this width; 370px is the scrim)
            page.get_by_role("button", name="Open navigation menu").click()
            page.get_by_role("link", name="Usage", exact=True).wait_for(timeout=5000)
            page.mouse.click(370, 600)
            page.wait_for_function("document.getElementById('sidebar').hidden === true", timeout=5000)
            assert page.evaluate("document.activeElement.getAttribute('aria-controls')") == "sidebar"
            # crossing to the desktop breakpoint with the menu open ends the modal state: no dialog
            # role, nothing inert, and Tab from the last sidebar control leaves the sidebar normally
            page.get_by_role("button", name="Open navigation menu").click()
            page.get_by_role("link", name="Usage", exact=True).wait_for(timeout=5000)
            page.set_viewport_size({"width": 1280, "height": 900})
            page.wait_for_function(
                "!document.getElementById('sidebar').getAttribute('role')"
                " && !document.getElementById('sidebar').hidden && !document.querySelector('[inert]')",
                timeout=5000,
            )
            page.get_by_role("button", name="Sign out").focus()
            page.keyboard.press("Tab")
            assert not page.evaluate("document.getElementById('sidebar').contains(document.activeElement)")
            page.keyboard.press("Escape")
            assert page.evaluate("document.getElementById('sidebar').hidden") is False
            page.set_viewport_size({"width": 390, "height": 900})
    for must in (
        "schedules-320",
        "rollouts-320",
        "settings-320",
        "device-telemetry-390",
        "trace-390",
        "eval-detail-390",
    ):
        assert must in captured, must

    # the schedule detail states the paused semantics in words (this is what the walkthrough shows)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{base}/schedules?schedule={schedule['id']}")
    _settle(page, "table:has(caption:text-is('Cron preview')) tbody tr")
    main = page.locator("main")
    main.get_by_text("Saved next-due cursor", exact=True).wait_for(timeout=5000)
    main.get_by_text(
        re.compile(r"Not dispatching: the schedule is paused\. Nothing fires until it is resumed")
    ).wait_for(timeout=5000)
    main.get_by_text(re.compile(r"none of the previewed times will dispatch until it is resumed")).wait_for(
        timeout=5000
    )
    assert main.get_by_text("Next occurrences", exact=True).count() == 0
    assert main.locator("th", has_text=re.compile(r"^Next$")).count() == 0

    # gallery states that need interaction: the busy confirm dialog cannot be dismissed
    page.goto(f"{base}/gallery")
    _settle(page, ".chart-svg")
    page.get_by_role("tab", name="Forms and dialogs").click()
    page.get_by_role("button", name="Confirm (busy)").click()
    page.get_by_role("dialog").wait_for(timeout=5000)
    assert page.get_by_role("dialog").get_by_role("button", name="Cancel").is_disabled()
    page.keyboard.press("Escape")
    page.wait_for_timeout(150)
    assert page.get_by_role("dialog").count() == 1  # Escape is ignored while the action is pending
    _shot(page, "redesign-gallery-confirm-busy-1440")

    # chart keyboard access on real data: the usage bar chart readout mirrors the cursor, and the
    # rendered axis labels are distinct (no 218.9k, 218.9k or 16:56, 16:56 repeats)
    page.goto(f"{base}/usage")
    _settle(page, "text=/\\d+ of \\d+ days reported/")
    page.locator(".chart-svg").first.focus()
    page.keyboard.press("End")
    page.locator("main").get_by_text(re.compile(r"\d{4}-\d{2}-\d{2}: \d+ requests")).wait_for(timeout=5000)
    page.goto(f"{base}/fleet/{dev}?tab=telemetry")
    _settle(page, "text=/Latest \\d+ samples, from/")
    for svg in page.locator("main .chart-svg").all():
        labels = svg.locator("g.tick text").all_text_contents()
        assert len(labels) == len(set(labels)), f"repeated axis label: {labels}"
        box = svg.bounding_box()
        for t in svg.locator("g.tick text").all():
            tb = t.bounding_box()
            if tb and box:
                assert tb["x"] >= box["x"] - 1 and tb["x"] + tb["width"] <= box["x"] + box["width"] + 1, (
                    f"clipped axis label {t.text_content()!r}"
                )

    # request identity guard: a response that arrives late for an EARLIER query must never replace
    # the data of the query the user has since applied (the 14-day request is held, 30-day proceeds)
    from datetime import datetime, timedelta, timezone

    today = datetime.now(timezone.utc).date()
    from14 = (today - timedelta(days=13)).isoformat()
    held: list = []
    page.route(re.compile(r"/api/v1/usage\?.*from=" + re.escape(from14)), lambda route: held.append(route))
    page.goto(f"{base}/usage")
    _settle(page, "text=/\\d+ of 7 days reported/")
    page.get_by_role("button", name="Last 14 days").click()
    _wait(lambda: held or None, 10)
    page.locator("main").get_by_text(re.compile(r"of 7 days reported")).wait_for(state="hidden", timeout=5000)
    assert (
        page.locator("main").get_by_text(re.compile(r"of 14 days reported")).count() == 0
    )  # nothing to draw yet
    page.get_by_role("button", name="Last 30 days").click()
    _settle(page, "text=/\\d+ of 30 days reported/")
    for r in held:
        r.continue_()  # the earlier query answers late
    page.wait_for_timeout(700)
    assert page.locator("main").get_by_text(re.compile(r"of 30 days reported")).count() == 1
    assert page.locator("main").get_by_text(re.compile(r"of 14 days reported")).count() == 0
    page.locator("main").get_by_text(re.compile(r"\(30 days, inclusive")).wait_for(timeout=2000)
    page.unroute(re.compile(r"/api/v1/usage\?.*from=" + re.escape(from14)))

    after_login = console_errors[len(pre_login_errors) :]
    assert not after_login, f"console errors after sign-in: {after_login[:5]}"
    external = [u for u in requests if not u.startswith(base) and not u.startswith("data:")]
    assert not external, f"external requests: {external[:5]}"
    served = {u.rsplit("/", 1)[-1]: st for u, st in fonts.items()}
    for face in (
        "IBMPlexSans-Regular.woff2",
        "IBMPlexSans-Medium.woff2",
        "IBMPlexSans-SemiBold.woff2",
        "IBMPlexMono-Regular.woff2",
        "IBMPlexMono-Medium.woff2",
    ):
        assert served.get(face) == 200, f"font not served from the app origin: {face} -> {served.get(face)}"
    rendered = page.evaluate("getComputedStyle(document.querySelector('h1')).fontFamily")
    assert "IBM Plex Sans" in rendered, rendered
    assert page.evaluate("document.fonts.check('600 16px \"IBM Plex Sans\"')") is True
    assert page.evaluate("document.fonts.check('400 13px \"IBM Plex Mono\"')") is True
    ctx.close()

    # ---- bounded viewer pass: read-only role sees the same settled surfaces without any
    # state-changing control (server enforces roles; the UI must not offer what it will refuse)
    r = admin.post(
        "/api/v1/users",
        json={"email": "viewer@example.com", "role": "viewer", "password": "viewer-password-1"},
        headers=WEB,
    )
    assert r.status_code in (200, 201), r.text
    vctx = browser.new_context(viewport={"width": 1440, "height": 900})
    vpage = vctx.new_page()
    vpage.goto(f"{base}/login")
    vpage.get_by_label(L("Email")).fill("viewer@example.com")
    vpage.get_by_label(L("Password")).fill("viewer-password-1")
    vpage.get_by_role("button", name="Sign in").click()
    vpage.wait_for_url(f"{base}/", timeout=10000)
    _settle(vpage, "text=/3 online/")
    vpage.goto(f"{base}/fleet/{dev}")
    _settle(vpage, f"text={rel}")
    for label in ("Deploy", "Edit settings", "Rollback", "Retire"):
        assert vpage.locator("main").get_by_role("button", name=label, exact=True).count() == 0, label
    _shot(vpage, "redesign-viewer-device-1440")
    vpage.goto(f"{base}/schedules?schedule={schedule['id']}")
    _settle(vpage, "table:has(caption:text-is('Cron preview')) tbody tr")
    for label in ("New schedule", "Edit", "Resume", "Pause"):
        assert vpage.locator("main").get_by_role("button", name=label, exact=True).count() == 0, label
    vpage.goto(f"{base}/rollouts/{rollout['id']}")
    _settle(vpage, f"a:text-is('{BOTS[0]}')")
    assert vpage.locator("main").get_by_role("button", name="Start", exact=True).count() == 0
    vpage.goto(f"{base}/settings")
    _settle(vpage, "text=admin@example.com")
    assert vpage.locator("main").get_by_role("button", name="Create user", exact=True).count() == 0
    _shot(vpage, "redesign-viewer-settings-1440")
    vctx.close()


# ---------------------------------------------------------------------------- runs inspector (R79)
_ROW_GEOMETRY_JS = """
(scope) => {
  const root = document.querySelector(scope);
  if (!root) return null;
  const box = (el) => { const r = el.getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width}; };
  return Array.from(root.querySelectorAll('.dl')).map((dl) => ({
    width: dl.getBoundingClientRect().width,
    box: box(dl),
    rows: Array.from(dl.querySelectorAll(':scope > div')).map((row) => {
      const dt = row.querySelector('dt'), dd = row.querySelector('dd');
      return {
        term: dt.textContent.trim(),
        dt: box(dt),
        dd: box(dd),
        ddScroll: dd.scrollWidth, ddClient: dd.clientWidth,
        badges: Array.from(dd.querySelectorAll('.badge')).map((b) => ({text: b.textContent.trim(), ...box(b), lines: Math.round(b.getBoundingClientRect().height / parseFloat(getComputedStyle(b).lineHeight))})),
        clipped: Array.from(dd.querySelectorAll('*')).some((e) => { const cs = getComputedStyle(e); return cs.textOverflow === 'ellipsis' || (cs.overflow === 'hidden' && e.scrollWidth > e.clientWidth + 1); }),
      };
    }),
  }));
}
"""


# the outcome the server stored for the board's Qwen3-4B deploy refusal (op_cj0vp2oakm9g, gen 3,
# docs/JETSON_GUIDE.md 9.7): a CUTOVER_MEMORY failure with launch admission numbers, the incumbent's
# clean stop and a one-attempt recovery. Numbers as recorded; ids are this stack's.
def _cutover_memory_outcome(release_id: str, incumbent: str) -> dict:
    return {
        "status": "failed",
        "failure": {
            "code": "CUTOVER_MEMORY",
            "stage": "cutover",
            "message": (
                f"MemAvailable is 5446 MiB after the previous runtime stopped but {release_id} needs "
                "5512 MiB (model 2764 + kv 300 + robot 1536 + margin 512 + runtime 700, ctx 2048); "
                "runtime not started"
            ),
            "details": {
                "release_id": release_id,
                "required_mb": 5511.8,
                "mem_available_mb": 5445.6914,
                "headroom_mb": -66.1,
                "candidate_started": False,
                "stop": {"signal": "SIGTERM", "exit_code": 0, "seconds": 0.315},
                "recovery": {
                    "recovered": True,
                    "degraded": False,
                    "attempts": 1,
                    "active_release_id": incumbent,
                },
            },
        },
        "result": {
            "active_release_id": incumbent,
            "recovery": {"recovered": True, "degraded": False, "attempts": 1, "active_release_id": incumbent},
        },
    }


def _assert_stacked_rows(lists: list, where: str) -> None:
    """Every definition row in a NARROW list: term above value, both spanning the row; nothing in
    the value is clipped, ellipsised or pushed outside the list; badges sit inside the value box."""
    assert lists, where
    for dl in lists:
        assert dl["width"] < 480, (where, dl["width"])  # the narrow case is really exercised
        inner = dl["width"] - 32  # 16 px padding each side
        for row in dl["rows"]:
            dt, dd = row["dt"], row["dd"]
            assert dd["top"] >= dt["bottom"] - 1, (where, row["term"], dt, dd)  # stacked
            assert abs(dd["left"] - dt["left"]) <= 1, (where, row["term"], dt, dd)  # same column
            assert dd["width"] >= inner - 2, (where, row["term"], dd["width"], inner)  # full width
            assert dd["right"] <= dl["box"]["right"] + 1, (where, row["term"], dd, dl["box"])
            assert row["ddScroll"] <= row["ddClient"] + 1, (
                where,
                row["term"],
                row["ddScroll"],
                row["ddClient"],
            )
            assert not row["clipped"], (where, row["term"])
            for b in row["badges"]:
                assert b["left"] >= dd["left"] - 1 and b["right"] <= dd["right"] + 1, (
                    where,
                    row["term"],
                    b,
                    dd,
                )


@pytest.mark.timeout(420)
def test_browser_runs_inspector_definition_rows_stack_by_container_width(browser, stack):
    """R79: the Runs inspector is a 2fr/1fr side panel at desktop widths (~340-380 px at
    1440-1470 px), where a viewport-driven 220 px term column starved the value column. Definition
    lists now stack by their OWN width: term above value in the side panel and in the phone drawer,
    two columns where the list is wide. Verified on a real failed deploy with the board's
    CUTOVER_MEMORY outcome shape, by bounding boxes (no clipping, no ellipsis, badges inside their
    value), no horizontal overflow, a settled screenshot at 1440x768 and at 320/400 px, and the
    Raw outcome disclosure opened from the keyboard."""
    from datetime import datetime, timezone

    from convoy_server.db import session_scope, write_txn
    from convoy_server.models import Operation

    base, admin, s, dev = stack["base"], stack["admin"], stack["seed"], stack["device_id"]
    # the incumbent release: the stack's seeded release deployed through the API first
    op = admin.post(
        f"/api/v1/devices/{dev}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    ).json()
    assert _terminal(admin, op["id"])["status"] == "succeeded"
    incumbent = s["release_id"]
    op_id = "op_r79cutovermem"
    outcome = _cutover_memory_outcome("rel_r79qwen34b", incumbent)
    now = datetime.now(timezone.utc)
    with (
        session_scope() as sess,
        write_txn(sess),
    ):  # terminal record inserted directly: never offered to the agent
        sess.add(
            Operation(
                id=op_id,
                device_id=dev,
                type="deploy",
                payload={"target_release_id": "rel_r79qwen34b", "plan_id": s["plan_id"], "simulated": True},
                payload_digest="0" * 64,
                generation=99,
                plan_id=s["plan_id"],
                release_id=incumbent,
                status="failed",
                outcome=outcome,
                progress={},
                created_by="operator@example.com",
                terminal_at=now,
                deadline_at=now,
            )
        )
    got = admin.get(f"/api/v1/operations/{op_id}").json()
    assert got["status"] == "failed" and got["outcome"]["failure"]["code"] == "CUTOVER_MEMORY"

    ctx = browser.new_context(viewport={"width": 1440, "height": 768})
    page = ctx.new_page()
    console_errors: list[str] = []
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: console_errors.append(str(e)))
    _login(page, base)
    _settle(page, "text=/3 online/")
    console_errors.clear()  # only the pre-login session probe may have logged a 401

    # ---- desktop 1440x768: side panel
    page.goto(f"{base}/runs?op={op_id}")
    _settle(page, "aside[aria-label='Operation detail'] [aria-label='Outcome summary'] .badge")
    assert page.locator("main [role=dialog]").count() == 0  # a side panel at this width, not a drawer
    panel = page.locator("aside[aria-label='Operation detail']")
    for needle in (
        "CUTOVER_MEMORY",
        "required 5,511.8 MiB",
        "available 5,445.7 MiB",
        "headroom -66.1 MiB",
        "candidate never started",
        "Active release after operation",
    ):
        assert panel.get_by_text(needle, exact=False).first.is_visible(), needle
    lists = page.evaluate(_ROW_GEOMETRY_JS, "aside[aria-label='Operation detail']")
    assert lists and len(lists) >= 2  # identity list + outcome summary list (delivery stays closed)
    _assert_stacked_rows(lists, "side panel 1440x768")
    terms = {r["term"] for dl in lists for r in dl["rows"]}
    assert {"Device", "Release", "Recovery", "Active release after operation", "Memory admission"} <= terms, (
        terms
    )
    panel_box = page.locator("aside[aria-label='Operation detail']").bounding_box()
    assert panel_box and panel_box["width"] < 480 and panel_box["x"] + panel_box["width"] <= 1440 + 1
    # the list beside it is not starved either: its table stays within the viewport
    _no_overflow(page, "runs-inspector", 1440)
    _shot(page, "runs-inspector-1440x768")

    # keyboard: the Raw outcome disclosure opens from its summary and shows the stored JSON
    summary = panel.locator("details > summary", has_text="Raw outcome")
    summary.focus()
    assert (
        page.evaluate("document.activeElement && document.activeElement.textContent.trim()") == "Raw outcome"
    )
    page.keyboard.press("Enter")
    raw = panel.locator("details:has(summary:text-is('Raw outcome')) pre")
    raw.wait_for(timeout=5000)
    assert "CUTOVER_MEMORY" in raw.text_content() and "5445.6914" in raw.text_content()
    raw_box = raw.bounding_box()
    assert raw_box and raw_box["x"] + raw_box["width"] <= panel_box["x"] + panel_box["width"] + 1
    _no_overflow(page, "runs-inspector-raw", 1440)
    page.keyboard.press("Enter")
    page.wait_for_function(
        "!document.querySelector(\"aside[aria-label='Operation detail'] details[open] pre\")", timeout=5000
    )

    # ---- a WIDE list keeps its two columns (the query is by container, not global stacking)
    page.goto(f"{base}/fleet/{dev}")
    _settle(page, f"text={incumbent}")
    wide = [dl for dl in page.evaluate(_ROW_GEOMETRY_JS, "main") if dl["width"] >= 480]
    assert wide, "expected at least one wide definition list on the device page at 1440"
    for dl in wide:
        for row in dl["rows"]:
            assert row["dd"]["left"] >= row["dt"]["right"] - 1, (row["term"], row["dt"], row["dd"])
            assert abs(row["dd"]["top"] - row["dt"]["top"]) <= 2, (row["term"], row["dt"], row["dd"])

    # ---- phones: the inspector is a full-width drawer; rows stack, nothing overflows
    for width in (400, 320):
        page.set_viewport_size({"width": width, "height": 800})
        page.goto(f"{base}/runs?op={op_id}")
        _settle(page, "[role=dialog] [aria-label='Outcome summary'] .badge")
        lists = page.evaluate(_ROW_GEOMETRY_JS, "main [role=dialog]")
        _assert_stacked_rows(lists, f"drawer {width}")
        for dl in lists:
            assert dl["box"]["right"] <= width + 1 and dl["box"]["left"] >= -1, (width, dl["box"])
        _no_overflow(page, "runs-inspector", width)
        assert page.locator("[role=dialog]").get_by_text("candidate never started").first.is_visible()
        _shot(page, f"runs-inspector-{width}")
    assert not console_errors, console_errors
    ctx.close()
