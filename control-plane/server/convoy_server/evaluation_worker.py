"""Separate durable evaluation job process. Does not load models or run physics."""

import argparse
import logging
import signal
import threading
import uuid

from .config import get_settings
from .db import init_engine, reset_engine
from .services.evaluations import claim_job, step_job


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=get_settings().log_level)
    init_engine()
    owner, stop = uuid.uuid4().hex, threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        while not stop.is_set():
            try:
                claim = claim_job(owner)
                if claim:
                    step_job(claim[0], owner, claim[1])
            except Exception as error:
                logging.error(
                    "evaluation reconciliation failed (%s); original case remains durable",
                    type(error).__name__,
                )
                if args.once:
                    raise
            if args.once:
                break
            stop.wait(0.2)
    finally:
        reset_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
