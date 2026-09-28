"""No JSON repair, tool execution, task substitution or partial response acceptance."""

import json

from convoy_contracts.pairing import FIXED_TASK, PLANNER_PROTOCOL, SYSTEM_PROMPT, validate_decision


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate planner output key")
        result[key] = value
    return result


def parse_decision(content: str) -> dict:
    if not isinstance(content, str) or len(content.encode("utf-8")) > PLANNER_PROTOCOL["max_output_bytes"]:
        raise ValueError("planner output exceeds bound")
    value = json.loads(content, object_pairs_hook=_unique_object,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    return validate_decision(value)


def completion_request() -> dict:
    return {"model": "convoy-active", **PLANNER_PROTOCOL["generation"], "messages": [
        {"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": FIXED_TASK},
    ]}
