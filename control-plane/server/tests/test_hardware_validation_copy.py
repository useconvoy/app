"""The `hardware_validation` field on /overview and /settings describes the PROJECT's documented
validation scope (docs/VERIFICATION.md §3): partial, with the incomplete items named. It is one shared
string, never a per-installation or per-device verdict, and the old blanket denial cannot return."""

from __future__ import annotations

from convoy_server.routers.evidence import HARDWARE_VALIDATION


def test_overview_and_settings_report_the_same_partial_validation_scope(admin):
    values = {
        path: admin.get(f"/api/v1/{path}").json()["hardware_validation"] for path in ("overview", "settings")
    }
    assert values["overview"] == values["settings"] == HARDWARE_VALIDATION
    hv = values["overview"]
    assert hv.startswith("Partial physical validation: one Orin Nano has measured")
    assert "automatic backup is accepted with ten simulators" in hv and "verification record" in hv
    assert "physical bounded shutdown and all-eleven live continuity remain pending" in hv
    for stale in ("deferred", "has not been run", "qualified", "all devices", "remain incomplete"):
        assert stale not in hv, stale
    # the field is project scope only: it names no installation or device (the ten simulators are the
    # documented scope of the accepted backup, not a per-device or per-installation verdict)
    assert "dev_" not in hv and "simulated device" not in hv and "this installation" not in hv
