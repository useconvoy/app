"""Command line: run, evaluate, record and check the bimanual pills_to_bottle task.

Rendering (``--record``, ``render``) needs an OpenGL context: on a headless Linux
host run under ``xvfb-run -a`` with ``MUJOCO_GL=glfw`` (or use EGL/OSMesa).
``--replay offline`` records in the offline replay format that
``scripts/import_offline_eval.py`` uploads; ``--replay journal`` (the default)
writes the hosted coordinator journal.

``edge_qwen_edge_skills`` (Edge Qwen) asks the model on a connected device for every
decision: give the website origin (``--planner-server`` or ``CONVOY_SERVER``) and a
signed-in operator session, either a file holding the ``convoy_session=…`` cookie
(``--planner-session-file`` or ``CONVOY_SESSION_FILE``) or ``CONVOY_EMAIL`` and
``CONVOY_PASSWORD`` (one sign-in, signed out at the end). Credentials are never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import physics as P
from .configs import CONFIGS, DEFAULT_HORIZON_S, SLICES


def _ids(value: str, known: dict) -> list[str]:
    if value == "all":
        return list(known)
    ids = [v.strip() for v in value.split(",") if v.strip()]
    unknown = [v for v in ids if v not in known]
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown: {', '.join(unknown)} (choose from {', '.join(known)})")
    return ids


def _triples(value: str) -> set[tuple[str, str, int]]:
    out = set()
    for item in value.split(","):
        if item.strip():
            c, s, n = item.strip().split(":")
            out.add((c, s, int(n)))
    return out


def _seeds(value: str) -> list[int]:
    out: list[int] = []
    for part in value.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def _planner_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--planner-server", default=os.environ.get("CONVOY_SERVER"),
                        help="website origin for the device planner (default: $CONVOY_SERVER)")
    parser.add_argument("--planner-session-file", type=Path, default=os.environ.get("CONVOY_SESSION_FILE"),
                        help="file holding a signed-in convoy_session cookie (default: $CONVOY_SESSION_FILE)")


def _device_planner(args, configs: list[str]):
    """(client, close) for the device-planner configurations among `configs`, else (None, no-op)."""
    if not any(CONFIGS[c].skill_planner.source == "device" for c in configs):
        return None, lambda: None
    from .device_planner import PortalChatClient, sign_in, sign_out

    if not args.planner_server:
        raise SystemExit("a device-planner configuration needs --planner-server (or CONVOY_SERVER)")

    def log(line: str) -> None:
        print(line, flush=True)

    if args.planner_session_file:
        return PortalChatClient.from_session_file(args.planner_server, args.planner_session_file, log=log), lambda: None
    email, password = os.environ.get("CONVOY_EMAIL"), os.environ.get("CONVOY_PASSWORD")
    if not email or not password:
        raise SystemExit("a device-planner configuration needs --planner-session-file, or CONVOY_EMAIL and CONVOY_PASSWORD")
    cookie = sign_in(args.planner_server, email, password)
    client = PortalChatClient(args.planner_server, cookie, log=log)
    return client, lambda: sign_out(args.planner_server, cookie)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m convoy_sim.bimanual_pill_task", description=__doc__)
    parser.add_argument("--timestep", type=float, help=f"physics step in seconds (default {P.TIMESTEP_S})")
    sub = parser.add_subparsers(dest="command", required=True)

    one = sub.add_parser("episode", help="run one episode")
    one.add_argument("--config", choices=CONFIGS, default="edge_qwen_edge_skills")
    one.add_argument("--slice", choices=SLICES, default="nominal")
    one.add_argument("--seed", type=int, default=0)
    one.add_argument("--horizon", type=float, default=DEFAULT_HORIZON_S)
    one.add_argument("--record", action="store_true", help="record the episode (needs GL)")
    one.add_argument("--replay", choices=("journal", "offline"), default="journal",
                     help="recording format: hosted journal, or the offline import format")
    one.add_argument("--preview-camera", help="add this camera (e.g. photo) beside the head view in previews")
    one.add_argument("--output", type=Path, required=True, help="new directory")
    _planner_options(one)

    ev = sub.add_parser("evaluate", help="seeds x slices x configs")
    ev.add_argument("--configs", default="all")
    ev.add_argument("--slices", default="all")
    ev.add_argument("--seeds", default="0-4")
    ev.add_argument("--horizon", type=float, default=DEFAULT_HORIZON_S)
    ev.add_argument("--jobs", type=int, default=4)
    ev.add_argument("--seed-stride", type=int, default=0,
                    help="the i-th slice runs seeds i*STRIDE+SEED (distinct seeds per slice)")
    ev.add_argument("--record", default="", help="CONFIG:SLICE:SEED entries to record, comma separated, or 'all'")
    ev.add_argument("--replay", choices=("journal", "offline"), default="journal",
                    help="recording format; offline also writes OUTPUT/CONFIG/evaluation.json for the import")
    ev.add_argument("--preview-camera", help="add this camera (e.g. photo) beside the head view in previews")
    ev.add_argument("--previews", default="", help="CONFIG:SLICE:SEED entries that get previews (default: all recorded)")
    ev.add_argument("--name", help="the offline evaluation's name (default: Pills to bottle · <configuration>)")
    ev.add_argument("--output", type=Path, required=True, help="new directory")
    _planner_options(ev)

    ph = sub.add_parser("check-physics", help="settling, drop, bottle fill and grasp-slip measurements")
    ph.add_argument("--output", type=Path, help="write JSON here")

    rd = sub.add_parser("render", help="still images of the robot and scene (needs GL)")
    rd.add_argument("--slice", choices=SLICES, default="nominal")
    rd.add_argument("--seed", type=int, default=0)
    rd.add_argument("--output", type=Path, required=True)

    vf = sub.add_parser("verify", help="check a recorded episode with the platform's replay rules")
    vf.add_argument("episode_dir", type=Path)

    args = parser.parse_args(argv)
    if args.timestep:
        P.TIMESTEP_S = args.timestep

    if args.command == "episode":
        from .evaluate import run_job

        planner, close = _device_planner(args, [args.config])
        try:
            if planner is not None:
                state = planner.device()
                if not (state.online and state.eligible):
                    raise SystemExit(f"the device is not ready for chat: {state.reason or state.status}")
            summary = run_job({"config": args.config, "slice": args.slice, "seed": args.seed, "horizon": args.horizon,
                               "record": args.record, "replay": args.replay, "output": str(args.output),
                               "timestep": args.timestep, "preview_camera": args.preview_camera}, planner)
        finally:
            close()
        print(json.dumps({k: v for k, v in summary.items() if k != "events"}, indent=2, default=float))
        return 0 if summary.get("status") == "completed" else 2
    if args.command == "evaluate":
        from .evaluate import evaluate

        configs, slices, seeds = _ids(args.configs, CONFIGS), _ids(args.slices, SLICES), _seeds(args.seeds)
        record = "all" if args.record == "all" else _triples(args.record)
        planner, close = _device_planner(args, configs)
        try:
            report = evaluate(args.output, configs, slices, seeds, jobs=args.jobs, horizon=args.horizon, record=record,
                              timestep=args.timestep, preview_camera=args.preview_camera, replay=args.replay,
                              seed_stride=args.seed_stride, previews=_triples(args.previews) if args.previews else None,
                              planner=planner, name=args.name)
        finally:
            close()
        print((args.output / "results.md").read_text())
        return 0 if all(e.get("status") == "completed" for e in report["episodes"]) else 2
    if args.command == "check-physics":
        from .physics_check import check_physics

        result = check_physics()
        text = json.dumps(result, indent=2, default=float)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n")
        print(text)
        return 0
    if args.command == "render":
        from .stills import render_stills

        for path in render_stills(args.output, SLICES[args.slice], args.seed):
            print(path)
        return 0
    if args.command == "verify":
        from .replay_check import platform_reader, validate_journal

        result = {"local_rules": validate_journal(args.episode_dir)}
        try:
            result["platform_reader"] = platform_reader(args.episode_dir)
        except ImportError as error:
            result["platform_reader"] = f"not run: {error} (install the 'managed' extra)"
        print(json.dumps(result, indent=2, default=str))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
