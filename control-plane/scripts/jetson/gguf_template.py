#!/usr/bin/env python3
"""Print the chat template embedded in a local GGUF (and its qualification view) so a reviewed template
can be diffed against the model's own before it is pinned to a release. stdlib + convoy_agent.gguf.
Usage: gguf_template.py /var/lib/convoy-agent/cache/models/<sha256>.gguf [--json]"""

from __future__ import annotations

import json
import sys

from convoy_agent import gguf


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    meta = gguf.read_metadata(argv[0])
    q = gguf.qualification(meta)
    if "--json" in argv:
        print(
            json.dumps(
                {"qualification": q, "chat_template": meta["kv"].get("tokenizer.chat_template")}, indent=1
            )
        )
        return 0
    print(
        f"# architecture={q['architecture']} file_type={q['file_type']} tokenizer={q['tokenizer_model']}/{q['tokenizer_pre']} counts={q['kv_estimate_inputs']}",
        file=sys.stderr,
    )
    tpl = meta["kv"].get("tokenizer.chat_template")
    if tpl is None:
        print("# no embedded chat template", file=sys.stderr)
        return 1
    sys.stdout.write(tpl)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
