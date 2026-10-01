from __future__ import annotations

import copy
import importlib.util
import json
import os
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

import yaml

DEPLOY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("compose_release", DEPLOY / "compose-release.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
SHA = "abcdef123456"
OLD = {
    "services": {
        "web": {"image": "node:22-alpine", "volumes": ["./releases/old:/app:ro"], "env_file": ["./portal/web.env"]},
        "control-plane": {"image": "convoy-control-plane:old", "env_file": ["./portal/control-plane.env"], "volumes": ["./portal/data:/data"]},
        "postgres": {"image": "postgres:16", "volumes": ["db:/var/lib/postgresql/data"]},
        "caddy": {"image": "caddy:2", "ports": ["443:443"]},
    },
    "volumes": {"db": {}},
}


class ComposeContract(unittest.TestCase):
    def test_release_is_scoped_and_runtime_mounts_are_preserved(self):
        result = module.deploy(OLD, SHA, True)
        self.assertEqual(result["services"]["postgres"], OLD["services"]["postgres"])
        self.assertEqual(result["services"]["caddy"], OLD["services"]["caddy"])
        self.assertEqual(result["volumes"], OLD["volumes"])
        self.assertEqual(result["services"]["web"]["env_file"], ["./portal/web.env"])
        self.assertEqual(result["services"]["web"]["labels"], {"io.convoy.portal": "true"})
        api = result["services"]["control-plane"]
        self.assertEqual(api["volumes"], ["./portal/data:/data"])
        self.assertEqual(api["env_file"], ["./portal/control-plane.env"])
        self.assertEqual(api["mem_limit"], "768m")
        self.assertEqual(api["logging"]["options"], {"max-size": "10m", "max-file": "3"})
        self.assertNotIn("ports", api)
        self.assertIn("no-new-privileges:true", api["security_opt"])

    def test_enabled_evaluations_and_private_signer_survive_release_and_rollback(self):
        before = copy.deepcopy(OLD)
        signing_mount = "./portal/execution-signing:/run/execution-signing:ro"
        before["services"]["control-plane"]["volumes"].append(signing_mount)
        before["services"]["evaluations"] = {"image": "convoy-control-plane:old", "volumes": ["./portal/data:/data"]}
        released = module.deploy(before, SHA, True)
        self.assertIn(signing_mount, released["services"]["control-plane"]["volumes"])
        self.assertEqual(released["services"]["evaluations"]["image"], f"convoy-control-plane:{SHA}")
        self.assertNotIn(signing_mount, released["services"]["evaluations"]["volumes"])
        self.assertEqual(module.restore(released, before)["services"]["evaluations"], before["services"]["evaluations"])
        self.assertNotIn("evaluations", module.restore(released, OLD)["services"])

    def test_web_only_release_keeps_api_and_restore_keeps_unrelated_current_services(self):
        result = module.deploy(OLD, SHA, False)
        self.assertEqual(result["services"]["control-plane"], OLD["services"]["control-plane"])
        result["services"]["postgres"]["labels"] = {"new": "retained"}
        restored = module.restore(result, OLD)
        self.assertEqual(restored["services"]["web"], OLD["services"]["web"])
        self.assertEqual(restored["services"]["control-plane"], OLD["services"]["control-plane"])
        self.assertEqual(restored["services"]["postgres"]["labels"], {"new": "retained"})

    def test_old_backup_gains_persistent_web_env_and_invalid_sha_is_rejected(self):
        restored = module.restore(OLD, {"services": {"web": {"image": "old-console"}}})
        self.assertEqual(restored["services"]["web"]["env_file"], ["./portal/web.env"])
        self.assertNotIn("control-plane", restored["services"])
        with self.assertRaises(ValueError):
            module.deploy(OLD, "../../escape", True)


class ShellRelease(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "portal/data").mkdir(parents=True)
        (self.root / "portal/web.env").write_text("PORTAL_DEMO_PASSWORD_HASH='scrypt$salt$key'\n")
        (self.root / "portal/control-plane.env").write_text("CONVOY_DATA_DIR=/data\n")
        (self.root / "portal/data/convoy.db").write_bytes(b"database-must-never-be-replaced")
        (self.root / "compose.yaml").write_text(yaml.safe_dump(OLD))
        (self.root / "Caddyfile").write_text("routing-must-not-change")
        (self.root / "compose.override.yaml").write_text("services: {}\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.script("python3", f"#!/bin/sh\nexec {shlex.quote(sys.executable)} \"$@\"\n")
        self.script("flock", "#!/bin/sh\nexit 0\n")
        self.script("df", "#!/bin/sh\nprintf 'Avail\\n4000000\\n'\n")
        self.script("sleep", "#!/bin/sh\nexit 0\n")
        self.script("docker", f"#!{sys.executable}\n" + r'''
import json, os, sys
from pathlib import Path
import yaml
root = Path(os.environ["CONVOY_APP_DIR"])
a = sys.argv[1:]
doc = yaml.safe_load((root / "compose.yaml").read_text())
with (root / "docker-calls").open("a") as log:
    log.write(json.dumps({"args": a, "services": doc["services"]}) + "\n")
fail = os.environ.get("FAIL_AT")
if a[:3] == ["compose", "ps", "-q"]:
    if a[-1] in doc["services"]: print("known-" + a[-1] + "-container")
elif a[:2] == ["compose", "up"]:
    if fail == "web-start" and a[-1] == "web" and "abcdef123456" in str(doc["services"]["web"]): sys.exit(42)
elif a[:2] == ["compose", "exec"]:
    if "control-plane:8080" in a[-1]:
        if fail == "api-health" and doc["services"]["control-plane"]["image"].endswith("abcdef123456"): sys.exit(1)
        print('{"ok":true}')
    elif "api/portal/session" in a[-1]:
        if doc["services"]["web"].get("labels", {}).get("io.convoy.portal") != "true": sys.exit(1)
        if fail == "portal-health": sys.exit(1)
        print('{"authenticated":false}')
    else: print('<title>Convoy</title>')
''')
        staging = self.root / "staging"
        staging.mkdir()
        (staging / "server.js").write_text("// standalone test")
        self.archive = self.root / "web.tar.gz"
        with tarfile.open(self.archive, "w:gz") as tar:
            tar.add(staging / "server.js", arcname="server.js")
        self.api = self.root / "api.tar.gz"
        self.api.write_bytes(b"docker-load-is-stubbed")

    def script(self, name, content):
        path = self.bin / name
        path.write_text(content)
        path.chmod(0o755)

    def run_release(self, fail=None):
        return subprocess.run(
            ["bash", str(DEPLOY / "remote-release.sh"), "deploy", SHA, str(self.archive), str(self.api)],
            env={**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "CONVOY_APP_DIR": str(self.root), "FAIL_AT": fail or ""},
            text=True, capture_output=True, timeout=30, check=False,
        )

    def assert_runtime_retained(self):
        self.assertEqual((self.root / "portal/data/convoy.db").read_bytes(), b"database-must-never-be-replaced")
        self.assertEqual((self.root / "portal/web.env").read_text(), "PORTAL_DEMO_PASSWORD_HASH='scrypt$salt$key'\n")
        self.assertEqual((self.root / "portal/control-plane.env").read_text(), "CONVOY_DATA_DIR=/data\n")
        self.assertEqual((self.root / "Caddyfile").read_text(), "routing-must-not-change")
        self.assertEqual((self.root / "compose.override.yaml").read_text(), "services: {}\n")

    def test_success_updates_both_services_without_touching_runtime(self):
        result = self.run_release()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_runtime_retained()
        doc = yaml.safe_load((self.root / "compose.yaml").read_text())
        self.assertEqual(doc["services"]["control-plane"]["image"], f"convoy-control-plane:{SHA}")
        backups = list((self.root / "rollback").glob("*/compose.yaml"))
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0].parent / "Caddyfile").exists())

    def test_first_portal_failure_restores_old_landing_with_api_and_no_portal_endpoint(self):
        result = self.run_release("web-start")
        # Recovery must succeed with cached images while preserving the initial
        # nonzero deployment status. Linux Bash 5 previously inherited 42 from
        # the ERR trap in ensure_web_image's argumentless return and aborted it.
        self.assertEqual(result.returncode, 42)
        self.assertIn("previous application services restored", result.stderr)
        self.assert_runtime_retained()
        self.assertEqual(yaml.safe_load((self.root / "compose.yaml").read_text()), OLD)
        calls = [json.loads(line) for line in (self.root / "docker-calls").read_text().splitlines()]
        starts = [c for c in calls if c["args"][:2] == ["compose", "up"]]
        self.assertEqual(starts[-2]["args"][-1], "control-plane")
        self.assertEqual(starts[-2]["services"]["control-plane"]["image"], "convoy-control-plane:old")
        self.assertEqual(starts[-1]["args"][-1], "web")
        self.assertTrue(all(c["args"][-1] in ("web", "control-plane") for c in starts))
        old_portal_checks = [c for c in calls if c["args"][:2] == ["compose", "exec"] and "api/portal/session" in c["args"][-1] and "labels" not in c["services"]["web"]]
        self.assertEqual(old_portal_checks, [])

    def test_api_health_failure_rolls_back_both_services_without_database_restore(self):
        result = self.run_release("api-health")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("previous application services restored", result.stderr)
        self.assert_runtime_retained()
        self.assertEqual(yaml.safe_load((self.root / "compose.yaml").read_text()), OLD)

    def test_default_rollback_uses_mtime_with_mixed_migration_and_release_names(self):
        rollback = self.root / "rollback"
        migration = rollback / "portal-20260914T055520Z"
        recent = rollback / "20260914T070000Z-abcdef"
        incomplete = rollback / "new-incomplete-backup"
        for directory in (migration, recent, incomplete):
            directory.mkdir(parents=True)
        ancient = module.deploy(OLD, "111111111111", True)
        (migration / "compose.yaml").write_text(yaml.safe_dump(ancient))
        (recent / "compose.yaml").write_text(yaml.safe_dump(OLD))
        os.utime(migration, (100, 100))
        os.utime(recent, (200, 200))
        self.assertEqual(module.newest_backup(rollback), recent)
        (self.root / "compose.yaml").write_text(yaml.safe_dump(module.deploy(OLD, SHA, True)))
        result = subprocess.run(
            ["bash", str(DEPLOY / "remote-release.sh"), "rollback"],
            env={**os.environ, "PATH": f"{self.bin}:{os.environ['PATH']}", "CONVOY_APP_DIR": str(self.root)},
            text=True, capture_output=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_runtime_retained()
        self.assertEqual(yaml.safe_load((self.root / "compose.yaml").read_text()), OLD)

    def test_labeled_portal_must_serve_session_endpoint_even_when_api_is_healthy(self):
        result = self.run_release("portal-health")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("previous application services restored", result.stderr)
        self.assert_runtime_retained()
        self.assertEqual(yaml.safe_load((self.root / "compose.yaml").read_text()), OLD)


if __name__ == "__main__":
    unittest.main()
