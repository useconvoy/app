"""Explicit, pinned download; never called by a model worker or PR checks."""

from __future__ import annotations

import argparse
from pathlib import Path

from .artifact import ASSETS, verify_assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    from huggingface_hub import snapshot_download

    for folder, repo, revision in (
        ("checkpoint", ASSETS["policy_repo"], ASSETS["policy_revision"]),
        ("base-assets", ASSETS["base_repo"], ASSETS["base_revision"]),
    ):
        files = [name.removeprefix(folder + "/") for name in ASSETS["assets"] if name.startswith(folder + "/")]
        snapshot_download(repo, revision=revision, local_dir=args.output / folder,
                          allow_patterns=files, max_workers=2)
    verify_assets(args.output)
    print("Pinned policy, processors and tokenizer verified. Base VLM weights were not downloaded.")


if __name__ == "__main__":
    main()
