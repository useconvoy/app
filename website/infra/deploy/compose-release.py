#!/usr/bin/env python3
"""Change only the two owned application services; retain host routing and runtime files."""
from __future__ import annotations

import copy
import os
import re
import sys
from pathlib import Path

import yaml


def web_runtime(service):
    service = copy.deepcopy(service)
    files = service.get("env_file", [])
    if isinstance(files, str):
        files = [files]
    if "./portal/web.env" not in files:
        files.append("./portal/web.env")
    service["env_file"] = files
    return service


def deploy(document, sha, with_api):
    if not re.fullmatch(r"[a-f0-9]{12,40}", sha):
        raise ValueError("invalid release id")
    result = copy.deepcopy(document)
    services = result.setdefault("services", {})
    services["web"] = web_runtime({
        "image": "node:22-alpine", "restart": "unless-stopped",
        "working_dir": "/app", "command": ["node", "server.js"], "user": "node",
        "labels": {"io.convoy.portal": "true"},
        "environment": {"NODE_ENV": "production", "PORT": "3000", "HOSTNAME": "0.0.0.0", "NEXT_TELEMETRY_DISABLED": "1",
                        "CONVOY_API_URL": "http://control-plane:8080", "CONVOY_API_INTERNAL_HTTP": "1"},
        "volumes": [f"./releases/{sha}:/app:ro"], "expose": ["3000"],
    })
    if with_api:
        services["control-plane"] = {
            "image": f"convoy-control-plane:{sha}", "restart": "unless-stopped",
            "env_file": ["./portal/control-plane.env"], "volumes": ["./portal/data:/data"],
            "expose": ["8080"], "init": True, "stop_grace_period": "35s",
            "mem_limit": "768m", "security_opt": ["no-new-privileges:true"],
            "logging": {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}},
        }
    return result


def restore(current, saved):
    result = copy.deepcopy(current)
    services = result.setdefault("services", {})
    previous = saved.get("services", {})
    if "web" not in previous:
        raise ValueError("backup has no web service")
    services["web"] = web_runtime(previous["web"])
    if "control-plane" in previous:
        services["control-plane"] = copy.deepcopy(previous["control-plane"])
    else:
        services.pop("control-plane", None)
    return result


def write_atomic(path, value):
    target = Path(path)
    temporary = target.with_suffix(target.suffix + ".release-tmp")
    temporary.write_text(yaml.safe_dump(value, sort_keys=False))
    os.chmod(temporary, target.stat().st_mode & 0o777)
    temporary.replace(target)


def newest_backup(path):
    candidates = [p for p in Path(path).iterdir() if p.is_dir() and not p.is_symlink() and (p / "compose.yaml").is_file()]
    return max(candidates, key=lambda p: (p.stat().st_mtime_ns, p.name), default=None)


def main():
    mode, path, *args = sys.argv[1:]
    if mode == "newest-backup":
        selected = newest_backup(path)
        print(selected if selected is not None else "")
        return
    document = yaml.safe_load(Path(path).read_text())
    if mode == "deploy":
        write_atomic(path, deploy(document, args[0], args[1] == "yes"))
    elif mode == "restore":
        write_atomic(path, restore(document, yaml.safe_load(Path(args[0]).read_text())))
    elif mode == "image":
        print(document.get("services", {}).get(args[0], {}).get("image", ""))
    elif mode == "portal":
        labels = document.get("services", {}).get("web", {}).get("labels", {})
        print("yes" if isinstance(labels, dict) and labels.get("io.convoy.portal") == "true" else "no")
    else:
        raise ValueError("unsupported mode")


if __name__ == "__main__":
    main()
