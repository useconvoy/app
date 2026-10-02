from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from . import __version__


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="convoy-agent", description="Convoy device agent (outbound only, stdlib)"
    )
    ap.add_argument("--data-dir", default=os.environ.get("CONVOY_AGENT_DIR", "/var/lib/convoy-agent"))
    ap.add_argument("--log-level", default=os.environ.get("CONVOY_LOG_LEVEL", "INFO"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enroll", help="one-time enrollment (or rebinding) with a token from the console")
    e.add_argument("--server", required=True)
    e.add_argument("--token", required=True)
    e.add_argument("--name", required=True)
    e.add_argument("--simulate", action="store_true")
    e.add_argument("--host-inventory", action="store_true", help="report the real computer running a simulator instead of synthetic demo hardware")
    e.add_argument("--seed", type=int, default=1)
    e.add_argument("--ca-file", default=None)
    e.add_argument(
        "--insecure",
        action="store_true",
        help="skip TLS verification (LAN self-signed only; never on the internet)",
    )
    r = sub.add_parser("run", help="run the agent loop")
    r.add_argument("--once", action="store_true", help="one tick (plus any operation it started) then exit")
    r.add_argument("--no-robot-sim", action="store_true")
    r.add_argument("--gateway-port", type=int, default=None)
    sub.add_parser("status", help="print local journal state")
    p = sub.add_parser(
        "runtime-probe",
        help="optional host harness: exercise a real llama-server binary through the supervisor + gateway (no Convoy server needed)",
    )
    p.add_argument("--llama-server", default=os.environ.get("LLAMA_SERVER_BIN"))
    p.add_argument("--gguf", default=os.environ.get("TEST_GGUF_PATH"))
    p.add_argument(
        "--template-file",
        default=None,
        help="reviewed Jinja template file (only for fixtures without an embedded template)",
    )
    p.add_argument("--gpu-layers", default="all")
    p.add_argument("--json", action="store_true")
    f = sub.add_parser("sim-fleet", help="run N simulated agents in one process (each with its own data dir)")
    f.add_argument("--server", required=True)
    f.add_argument(
        "--tokens", required=True, help="comma-separated enrollment tokens (one per simulated device)"
    )
    f.add_argument("--root", default="./sim-fleet")
    f.add_argument(
        "--stop-timeout", type=float, default=30.0, help="bounded shutdown after SIGTERM/SIGINT (s)"
    )
    f.add_argument("--name-prefix", default="sim")
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "enroll":
        from .agent import enroll

        res = enroll(
            Path(args.data_dir),
            server=args.server,
            token=args.token,
            name=args.name,
            simulate=args.simulate,
            host_inventory=args.host_inventory,
            seed=args.seed,
            ca_file=args.ca_file,
            insecure=args.insecure,
        )
        print(
            json.dumps(
                {"device_id": res["device_id"], "replay": res.get("replay"), "data_dir": args.data_dir}
            )
        )
        return 0
    if args.cmd == "run":
        from .agent import Agent

        res = Agent(
            Path(args.data_dir),
            once=args.once,
            robot_sim=(False if args.no_robot_sim else None),
            gateway_port=args.gateway_port,
        ).run()
        return 0 if (res or {}).get("complete", True) else 1
    if args.cmd == "status":
        from .journal import Journal

        j = Journal(Path(args.data_dir) / "journal.db")
        print(
            json.dumps(
                {
                    "active_release_id": j.get("active_release_id"),
                    "recovery_release_id": j.get("recovery_release_id"),
                    "generation": j.get("generation"),
                    "health": j.get("health"),
                    "failed_generation_latch": j.get("failed_generation_latch"),
                    "operation": j.current_operation(),
                    "lanes": j.lane_status(),
                    "pins": j.pins(),
                },
                indent=2,
                default=str,
            )
        )
        return 0
    if args.cmd == "runtime-probe":
        from .probe import runtime_probe

        if not args.llama_server or not args.gguf:
            print(
                "runtime-probe needs --llama-server/LLAMA_SERVER_BIN and --gguf/TEST_GGUF_PATH",
                file=sys.stderr,
            )
            return 2
        res = runtime_probe(
            Path(args.llama_server),
            Path(args.gguf),
            template_file=Path(args.template_file) if args.template_file else None,
            gpu_layers=args.gpu_layers,
        )
        print(json.dumps(res, indent=2, default=str) if args.json else json.dumps(res, default=str))
        return 0 if res.get("ok") else 1
    if args.cmd == "sim-fleet":
        from .agent import Agent, enroll

        tokens = [t for t in args.tokens.split(",") if t]
        agents = []
        import threading

        for i, tok in enumerate(tokens, 1):
            d = Path(args.root) / f"{args.name_prefix}-{i}"
            if not (d / "agent.json").exists() or not json.loads((d / "agent.json").read_text()).get(
                "device_id"
            ):
                enroll(
                    d, server=args.server, token=tok, name=f"{args.name_prefix}-{i}", simulate=True, seed=i
                )
            a = Agent(d)
            agents.append(a)
        import signal

        threads = [threading.Thread(target=a.run, name=f"agent-{i}") for i, a in enumerate(agents, 1)]
        for t in threads:
            t.start()
        stop = threading.Event()

        def handler(signum, frame):  # bounded, controlled stop for every agent (container SIGTERM, Ctrl-C)
            for a in agents:
                a.request_stop()
            stop.set()

        signal.signal(signal.SIGTERM, handler)
        signal.signal(signal.SIGINT, handler)
        print(f"{len(agents)} simulated agents running; SIGTERM or Ctrl-C to stop", flush=True)
        try:
            while not stop.is_set():
                stop.wait(3600)
        except KeyboardInterrupt:
            handler(signal.SIGINT, None)
        deadline = time.monotonic() + float(args.stop_timeout)
        for t in threads:
            t.join(timeout=max(0.1, deadline - time.monotonic()))
        if any(t.is_alive() for t in threads):
            # non-daemon threads still blocked (e.g. in a control-plane retry or a final flush): the
            # interpreter would wait for them indefinitely, so the deadline is enforced explicitly
            print(
                "sim-fleet: shutdown deadline reached with agents still busy; exiting",
                file=sys.stderr,
                flush=True,
            )
            logging.shutdown()
            os._exit(1)
        return 0
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


_ = __version__
