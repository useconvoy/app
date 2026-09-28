"""Installation preservation and actual Compose authority mounts; no running services changed."""

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("services", ROOT / "services.py")
services = importlib.util.module_from_spec(spec)
spec.loader.exec_module(services)


class ServicesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "installation"
        self.state_patch = patch.object(services, "STATE", self.state)
        self.project_patch = patch.object(services, "PROJECT", "convoy-key-test")
        self.state_patch.start()
        self.project_patch.start()
        self.addCleanup(self.state_patch.stop)
        self.addCleanup(self.project_patch.stop)

    @staticmethod
    def openssl_fixture(command, **_):
        # Certificate correctness is covered by real HTTPS container acceptance.
        # This check only needs the helper's generated installation state.
        if command[0] != "openssl":
            raise AssertionError("unexpected subprocess during initialization")
        for flag in ("-keyout", "-out"):
            if flag in command:
                Path(command[command.index(flag) + 1]).write_text("test certificate fixture")
        return subprocess.CompletedProcess(command, 0)

    def initialize(self):
        with patch.object(services, "run", self.openssl_fixture):
            services.initialize(3391, 8491, "convoy-key-test")

    def test_existing_legacy_installation_is_preserved_and_fails_before_docker(self):
        self.state.mkdir(mode=0o700)
        legacy = self.state / ".env"
        legacy.write_text("CONVOY_EXECUTION_SECRET='legacy-test-secret'\n")
        before = legacy.read_bytes(), legacy.stat().st_mtime_ns
        with patch.object(services, "PROJECT", services.DEFAULT_PROJECT), patch.object(services, "run") as run:
            with self.assertRaisesRegex(services.InstallationError, "legacy shared signing"):
                services.initialize(3391, 8491)
            self.assertEqual((legacy.read_bytes(), legacy.stat().st_mtime_ns), before)
            self.assertEqual(set(self.state.iterdir()), {legacy})
            run.assert_not_called()
            services.validate_installation(require_public=False)  # status/down still identify old project

    @unittest.skipUnless(shutil.which("docker"), "Docker Compose is required for its read-only config renderer")
    def test_fresh_installation_isolated_images_and_compose_role_authority(self):
        self.initialize()
        before = (self.state / ".env").read_bytes()
        values = services.environment_values()
        self.assertNotIn("CONVOY_EXECUTION_SECRET", values)
        self.assertNotIn("CONVOY_PLANNER_EXECUTION_SECRET", values)
        self.assertEqual(values["STACK_PROJECT"], "convoy-key-test")
        services.initialize(3392, 8492)  # preserving a setup does not rewrite ports or image tags
        self.assertEqual((self.state / ".env").read_bytes(), before)
        with self.assertRaisesRegex(services.InstallationError, "different image tags"):
            services.initialize(3392, 8492, "another-image")
        with patch.object(services, "PROJECT", "another-project"), self.assertRaisesRegex(services.InstallationError, "different Compose project"):
            services.validate_installation(require_public=True)
        with patch.dict(os.environ, {"API_IMAGE": "wrong-api:local", "POSTGRES_PASSWORD": "wrong-password"}):
            config = json.loads(services.compose("--profile", "tools", "config", "--format", "json", capture=True).stdout)
        roles = config["services"]
        self.assertEqual(roles["api"]["image"], "convoy-key-test-api:local")
        self.assertEqual(roles["web"]["image"], "convoy-key-test-web:local")
        self.assertEqual(roles["inference"]["image"], "convoy-key-test-reference:local")
        private, public = set(), set()
        for name, service in roles.items():
            env = service.get("environment", {})
            self.assertNotIn("CONVOY_EXECUTION_SECRET", env)
            self.assertNotIn("CONVOY_PLANNER_EXECUTION_SECRET", env)
            for mount in service.get("volumes", []):
                if mount.get("source") == "execution-signing":
                    private.add(name)
                    if name == "api":
                        self.assertTrue(mount["read_only"])
                if mount.get("source") == "action-verification":
                    public.add(name)
                    if name == "inference":
                        self.assertTrue(mount["read_only"])
        self.assertEqual(private, {"api", "execution-keys"})
        self.assertEqual(public, {"inference", "execution-keys"})
        self.assertEqual(roles["api"]["environment"]["CONVOY_EXECUTION_SIGNING_KEYS_FILE"],
                         "/run/execution-signing/keys.json")
        self.assertEqual(roles["inference"]["environment"]["CONVOY_ACTION_VERIFICATION_KEYS_FILE"],
                         "/run/action-verification/keys.json")
        for name in ("jobs", "evaluations", "simulator", "web", "acceptance", "migrate"):
            self.assertNotIn("CONVOY_EXECUTION_SIGNING_KEYS_FILE", roles[name].get("environment", {}))


    def test_new_state_cannot_adopt_existing_project_resources(self):
        for outputs in (("existing-container",), ("", "retained-volume")):
            calls = [subprocess.CompletedProcess([], 0, stdout=value) for value in outputs]
            with patch.object(services, "run", side_effect=calls) as run, self.assertRaisesRegex(services.InstallationError, "original state is missing"):
                services.require_unused_project()
            self.assertTrue(all("ls" in call.args[0] for call in run.call_args_list))
            self.assertFalse(self.state.exists())

    def test_key_initialization_receipt_prevents_implicit_replacement_after_volume_loss(self):
        self.initialize()
        receipt = self.state / "execution-keys.json"
        with patch.object(services, "compose", side_effect=subprocess.CalledProcessError(1, ["key-initializer"])), self.assertRaises(subprocess.CalledProcessError):
            services.initialize_execution_keys()
        self.assertFalse(receipt.exists())
        with patch.object(services, "compose") as compose:
            services.initialize_execution_keys()
            self.assertNotIn("--require-existing", compose.call_args.args)
        previous = receipt.read_bytes()
        with patch.object(services, "compose", side_effect=subprocess.CalledProcessError(1, ["key-initializer"])) as compose:
            with self.assertRaises(subprocess.CalledProcessError):
                services.initialize_execution_keys()
            self.assertIn("--require-existing", compose.call_args.args)
        self.assertEqual(receipt.read_bytes(), previous)
        with patch.object(services, "compose") as compose:
            services.initialize_execution_keys()
            self.assertIn("--require-existing", compose.call_args.args)
        self.assertEqual(receipt.read_bytes(), previous)
        receipt.unlink()
        (self.state / "installation.json").write_text('{"retained": true}')
        with patch.object(services, "compose") as compose:
            services.initialize_execution_keys()
            self.assertIn("--require-existing", compose.call_args.args)


if __name__ == "__main__":
    unittest.main()
