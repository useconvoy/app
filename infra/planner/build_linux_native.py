"""Build one pinned Linux ARM64 native artifact; no model or cloud access.

Example: python3 infra/planner/build_linux_native.py --source /path/to/llama.cpp
         --output /absolute/fresh/qualification-directory
Only the caller's fresh output directory and this helper's named container/image
are created. Existing source/builds, weights and service containers are untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path

COMMIT = "5266f24da75dc449bd56cbed7addb9c8e4a6a73e"
HERE = Path(__file__).resolve().parent
BASE = "debian:bookworm-slim@sha256:0c8bbb8e987a035fe1d9704eb2e571b7e9a836e1caa46345290674b45b69e417"


def run(*command: str, **kwargs):
    return subprocess.run(command, check=True, text=True, **kwargs)


def capture(*command: str) -> str:
    return run(*command, capture_output=True).stdout.strip()


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="existing clean checkout at the pinned commit")
    parser.add_argument("--output", type=Path, required=True, help="fresh directory outside the repository for large artifacts")
    args = parser.parse_args()
    source, output = args.source.resolve(strict=True), args.output.resolve()
    repository = HERE.parent.parent
    if output.is_relative_to(repository) or output.is_relative_to(source):
        parser.error("output must be outside both source repositories")
    if capture("git", "-C", str(source), "rev-parse", "HEAD") != COMMIT:
        parser.error("source must be at the supported pinned llama.cpp commit")
    if capture("git", "-C", str(source), "status", "--porcelain"):
        parser.error("source checkout must be clean")
    if capture("docker", "info", "--format", "{{.Architecture}}") not in {"aarch64", "arm64"}:
        parser.error("this qualification requires a native Linux ARM64 Docker host")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    context = output / "build-context"
    context.mkdir()
    for name in ("linux-native.Dockerfile", "package_linux_native.py"):
        shutil.copy2(HERE / name, context / name)
    source_archive = output / "source.tar"
    run("git", "-C", str(source), "archive", "--format=tar", "--output", str(source_archive), COMMIT)
    source_hash = sha(source_archive)
    request = output / "request.json"
    request.write_text(json.dumps({"source_commit": COMMIT, "source_sha256": source_hash}) + "\n")
    name = "convoy-native-build-" + uuid.uuid4().hex[:12]
    image = name + ":local"
    steps = {"source_commit": COMMIT, "source_sha256": source_hash, "builder_base": BASE,
             "build_container": name, "build_image": image, "resource_limits": {"cpus": 2, "memory": "3g", "pids": 256},
             "network_during_compile": "none", "recipe_sha256": {p.name: sha(p) for p in context.iterdir()}}
    container_created = False
    try:
        print("Preparing pinned native build tools; details in toolchain.log", flush=True)
        with (output / "toolchain.log").open("w") as log:
            run("docker", "build", "--platform=linux/arm64", "-f", str(context / "linux-native.Dockerfile"),
                "-t", image, str(context), stdout=log, stderr=subprocess.STDOUT)
        steps["builder_image_id"] = capture("docker", "image", "inspect", image, "--format", "{{.Id}}")
        run("docker", "create", "--name", name, "--network=none", "--cpus=2", "--memory=3g", "--pids-limit=256",
            "--cap-drop=ALL", "--security-opt=no-new-privileges", image, capture_output=True)
        container_created = True
        run("docker", "cp", str(source_archive), name + ":/work/source.tar")
        run("docker", "cp", str(request), name + ":/work/request.json")
        print("Compiling the portable CPU target with two build jobs; details in native-build.log", flush=True)
        with (output / "native-build.log").open("w") as log:
            run("docker", "start", "--attach", name, stdout=log, stderr=subprocess.STDOUT)
        code = int(capture("docker", "inspect", name, "--format", "{{.State.ExitCode}}"))
        if code != 0:
            raise RuntimeError("native build failed; preserve native-build.log for diagnosis")
        run("docker", "cp", name + ":/work/output/.", str(output))
        result = json.loads((output / "build-result.json").read_text())
        runtime = output / "runtime"
        archive = output / "llama-cpp-5266f24-linux-aarch64-cpu.tar.gz"
        if sha(archive) != result["archive_sha256"] or sha(runtime / "bin/llama-server") != result["binary_sha256"]:
            raise RuntimeError("copied artifact differs from the container's receipt")
        for library in result["libraries"]:
            if sha(runtime / library["path"]) != library["sha256"]:
                raise RuntimeError("copied library differs from the container's receipt")
        manifest = json.loads((runtime / "build-manifest.json").read_text())
        receipt = {"schema_version": 1, "source": manifest["source"],
                   "host": {"system": "Linux", "machine": "aarch64", "libc": manifest["libc"], "cpu_baseline": "armv8-a"},
                   "archive": {"path": str(archive), "sha256": result["archive_sha256"]},
                   "binary": {"path": str(runtime / "bin/llama-server"), "sha256": result["binary_sha256"]},
                   "libraries": [{"path": str(runtime / item["path"]), "sha256": item["sha256"]} for item in result["libraries"]],
                   "runtime_root": str(runtime), "build_manifest": str(runtime / "build-manifest.json"),
                   "validation": {**manifest["validation"], "version": result["version"],
                                  "deterministic_archive_verified": True, "fresh_extraction_version_passed": True}}
        (output / "native-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        steps["success"] = True
        print("Native artifact and native-receipt.json verified. Model qualification is a separate step.", flush=True)
    finally:
        if container_created:
            # Only our exact generated container is removed, even after a failed build.
            run("docker", "rm", "--force", name, capture_output=True)
        steps["container_removed"] = not capture("docker", "ps", "-aq", "--filter", "name=^" + name + "$")
        steps["source_still_clean"] = not capture("git", "-C", str(source), "status", "--porcelain")
        (output / "build-receipt.json").write_text(json.dumps(steps, indent=2) + "\n")


if __name__ == "__main__":
    main()
