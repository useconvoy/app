"""Bundle-backed world query — the replay-mode stand-in for WorldStore.query().
Same path grammar as convoy_evals.schema.match.resolve_path, over the root shape:

    records:  { [collection]: { [id]: fields } }
    messages: { sent: [msg], inbound: [msg], all: [msg] }
    files:    { [id]: file }

e.g. "records.policy.POL-1042.renewal_status", "messages.sent.*.to",
"files.*.name". Messages are dicts with a 'from' key (wire shape).

Port of src/graders/world-query.ts.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable, Dict, List

from ..sandbox.api import WorldBundle
from ..schema.match import resolve_path
from .util import message_to_dict


def build_query_root(bundle: WorldBundle) -> Dict[str, Any]:
    records: Dict[str, Dict[str, Any]] = {}
    for r in bundle.records:
        records.setdefault(r.collection, {})[r.id] = r.fields
    files: Dict[str, Any] = {}
    for f in bundle.files:
        files[f.id] = asdict(f)
    messages = [message_to_dict(m) for m in bundle.messages]
    return {
        "records": records,
        "messages": {
            "sent": [m for m in messages if m["direction"] == "outbound"],
            "inbound": [m for m in messages if m["direction"] == "inbound"],
            "all": messages,
        },
        "files": files,
    }


def bundle_query(bundle: WorldBundle) -> Callable[[str], List[Any]]:
    """GradeRecord.worldQuery implementation for replay grading."""
    root = build_query_root(bundle)

    def query(q: str) -> List[Any]:
        return resolve_path(root, q)

    return query


# Alias kept for parity with the TS export name.
make_bundle_query = bundle_query
