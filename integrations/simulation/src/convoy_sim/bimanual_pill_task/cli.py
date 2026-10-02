"""Command line: run, evaluate, record and check the bimanual pills_to_bottle task.

Rendering (``--record``, ``render``) needs an OpenGL context: on a headless Linux
host run under ``xvfb-run -a`` with ``MUJOCO_GL=glfw`` (or use EGL/OSMesa).
``--replay offline`` records in the offline replay format that
``scripts/import_offline_eval.py`` uploads; ``--replay journal`` (the default)
writes the hosted coordinator journal.

``edge_qwen_edge_skills`` (the edge device planner) asks the model on a connected
device for every decision, through the website's device chat contract
(``platform-chat-v1``): give the website origin (``--planner-server`` or
``CONVOY_SERVER``) and a signed-in operator session, either a file holding the
``convoy_session=…`` cookie (``--planner-session-file`` or ``CONVOY_SESSION_FILE``)
or ``CONVOY_EMAIL`` and ``CONVOY_PASSWORD`` (one sign-in, signed out at the end).
The device is the one named by ``--planner-device`` (``CONVOY_PLANNER_DEVICE``) or
the only physical device the account sees; with several, the run refuses to start.
Credentials are never printed.

``cloud_luna_vision`` (the cloud vision planner) calls GPT-6 Luna through the OpenAI
Responses API with the head camera image: the key comes from ``OPEN_AI_API_KEY``
(never printed), and every call's cost is appended to the spend ledger
(``--spend-ledger`` or ``CONVOY_SPEND_LEDGER``). No call is sent once the ledger's
spend plus the call's worst case would pass ``--spend-cap-usd`` (default 3.00).
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


def _print(line: str) -> None:
    print(line, flush=True)


def _planner_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--planner-server", default=os.environ.get("CONVOY_SERVER"),
                        help="website origin for the device planner (default: $CONVOY_SERVER)")
    parser.add_argument("--planner-session-file", type=Path, default=os.environ.get("CONVOY_SESSION_FILE"),
                        help="file holding a signed-in convoy_session cookie (default: $CONVOY_SESSION_FILE)")
    parser.add_argument("--planner-device", default=os.environ.get("CONVOY_PLANNER_DEVICE"),
                        help="the device (dev_…) to plan on; required when the account sees several "
                             "(default: $CONVOY_PLANNER_DEVICE, else the only one listed)")
    parser.add_argument("--spend-ledger", type=Path, default=os.environ.get("CONVOY_SPEND_LEDGER"),
                        help="the cloud vision planner's spend ledger (JSON lines, shared across runs; default: "
                             "$CONVOY_SPEND_LEDGER); its key comes from $OPEN_AI_API_KEY")
    parser.add_argument("--spend-cap-usd", type=float, default=float(os.environ.get("CONVOY_SPEND_CAP_USD", "3.0")),
                        help="no call is sent once the ledger's spend plus the call's worst case would pass this (USD)")


def _vision_planner(args, configs: list[str]):
    """The cloud vision planner's client for the vision configurations among `configs`, else None. The key is
    read from the environment by the client and never printed."""
    if not any(CONFIGS[c].skill_planner.source == "vision" for c in configs):
        return None
    from .openai_responses import KEY_ENV, OpenAIResponsesClient, SpendLedger

    if not getattr(args, "spend_ledger", None):
        raise SystemExit("the cloud vision planner needs --spend-ledger (or CONVOY_SPEND_LEDGER): every call's cost "
                         "is recorded there and capped")
    if not os.environ.get(KEY_ENV):
        raise SystemExit(f"the cloud vision planner needs {KEY_ENV}")
    ledger = SpendLedger(args.spend_ledger, args.spend_cap_usd)
    client = OpenAIResponsesClient(ledger)
    _print(f"cloud vision planner: {client.model} · {client.provider} · {client.effort} effort · {client.transport} · "
           f"spent ${ledger.spent_usd:.4f} of ${ledger.cap_usd:.2f}")
    return client


def _device_planner(args, configs: list[str]):
    """(client, close) for the device-planner configurations among `configs`, else (None, no-op). The client
    has checked the device: it is the one asked for (or the only one listed), ready for chat, and its active
    release and model are pinned for the run."""
    if not any(CONFIGS[c].skill_planner.source == "device" for c in configs):
        return None, lambda: None
    from .device_planner import (
        AccessRefused,
        DeviceSelectionError,
        PlatformChatClient,
        SessionEnded,
        model_label,
        runtime_label,
        sign_in,
        sign_out,
    )

    if not args.planner_server:
        raise SystemExit("a device-planner configuration needs --planner-server (or CONVOY_SERVER)")
    device = getattr(args, "planner_device", None)
    if args.planner_session_file:
        client = PlatformChatClient.from_session_file(args.planner_server, args.planner_session_file, device_id=device,
                                                      log=_print)
        close = lambda: None  # noqa: E731 - a session from a file is the caller's to end
    else:
        email, password = os.environ.get("CONVOY_EMAIL"), os.environ.get("CONVOY_PASSWORD")
        if not email or not password:
            raise SystemExit("a device-planner configuration needs --planner-session-file, or CONVOY_EMAIL and "
                             "CONVOY_PASSWORD")
        cookie = sign_in(args.planner_server, email, password)
        client = PlatformChatClient(args.planner_server, cookie, device_id=device, log=_print)
        close = lambda: sign_out(args.planner_server, cookie)  # noqa: E731
    try:
        state = client.device()
    except (DeviceSelectionError, SessionEnded, AccessRefused) as error:
        close()
        raise SystemExit(f"the device planner cannot start: {error}") from None
    except OSError as error:  # unreachable website: say so, and end a session this run opened
        close()
        raise SystemExit(f"the device planner cannot reach {args.planner_server}: {type(error).__name__}") from None
    if not (state.online and state.eligible):
        close()
        raise SystemExit(f"the device is not ready for chat: {state.reason or state.status}")
    _print(f"device planner: {state.device_id} · release {state.release_id} · {model_label(client.model)} · "
           f"{runtime_label(client.model)} · {client.transport}")
    return client, close


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
    ev.add_argument("--name", help="the offline evaluation's name (default: Pills to bottle · <configuration>; for the "
                                   "device planner also the model the platform reports and the transport)")
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
        planner = planner or _vision_planner(args, [args.config])
        try:
            summary = run_job({"config": args.config, "slice": args.slice, "seed": args.seed, "horizon": args.horizon,
                               "record": args.record, "replay": args.replay, "output": str(args.output),
                               "timestep": args.timestep, "preview_camera": args.preview_camera}, planner,
                              _print if planner else None)
        finally:
            close()
        print(json.dumps({k: v for k, v in summary.items() if k != "events"}, indent=2, default=float))
        return 0 if summary.get("status") == "completed" else 2
    if args.command == "evaluate":
        from .evaluate import evaluate

        configs, slices, seeds = _ids(args.configs, CONFIGS), _ids(args.slices, SLICES), _seeds(args.seeds)
        record = "all" if args.record == "all" else _triples(args.record)
        planner, close = _device_planner(args, configs)
        planner = planner or _vision_planner(args, configs)
        try:
            report = evaluate(args.output, configs, slices, seeds, jobs=args.jobs, horizon=args.horizon, record=record,
                              timestep=args.timestep, preview_camera=args.preview_camera, replay=args.replay,
                              seed_stride=args.seed_stride, previews=_triples(args.previews) if args.previews else None,
                              planner=planner, planner_log=_print if planner else None, name=args.name)
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
