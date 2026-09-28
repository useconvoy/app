"""Small factual failure records, independent of remote error prose and secrets."""

from __future__ import annotations

from .transport import RemoteError, TransportError

COMPONENTS = {
    "mission_claim": "management", "running_acknowledgement": "management",
    "planner_session": "planner", "planner_proposal": "planner",
    "policy_session": "action_policy", "policy_inference": "action_policy",
    "adapter_initialization": "robot_adapter", "adapter_step": "robot_adapter",
    "adapter_cleanup": "robot_adapter", "action_admission": "coordinator",
}
LABELS = {
    "mission_claim": "Mission admission", "running_acknowledgement": "Running acknowledgement",
    "planner_session": "Planner session", "planner_proposal": "Planner proposal",
    "policy_session": "Action-policy session", "policy_inference": "Action-policy inference",
    "adapter_initialization": "Robot adapter initialization", "adapter_step": "Robot command",
    "adapter_cleanup": "Robot adapter cleanup", "action_admission": "Action admission",
}


def failure_record(phase: str, error: Exception, *, authorization_elapsed: bool,
                   category: str | None = None, request: dict | None = None) -> dict:
    component = COMPONENTS[phase]
    if category is None:
        if isinstance(error, RemoteError):
            category = ({401: "authorization", 403: "authorization", 410: "authorization",
                         404: "compatibility", 409: "compatibility", 429: "capacity",
                         408: "deadline", 504: "deadline", 502: "runtime", 503: "transport"}
                        .get(error.status, "runtime" if error.status >= 500 else "protocol"))
        elif isinstance(error, TransportError):
            category = error.category
        elif isinstance(error, TimeoutError):
            category = "deadline"
        elif isinstance(error, OSError) and component in {"management", "planner", "action_policy"}:
            category = "transport"
        elif component == "robot_adapter":
            category = "runtime"
        elif isinstance(error, (ValueError, KeyError, TypeError)):
            category = "protocol"
        else:
            category = "internal"
    result = {"schema_version": 1, "component": component, "phase": phase, "category": category,
              "authorization_elapsed": authorization_elapsed}
    if isinstance(error, RemoteError):
        result["http_status"] = error.status
    if request:
        result["request_id"] = request["request_id"]
        if "sequence" in request:
            result["sequence"] = request["sequence"]
    return result


def failure_detail(record: dict) -> str:
    reason = {
        "authorization": "authorization was rejected", "compatibility": "endpoint, release or session did not match",
        "capacity": "service was busy", "deadline": "deadline was exceeded",
        "transport": "service could not be reached or was unavailable",
        "protocol": "request or response did not satisfy the contract",
        "runtime": "service or runtime reported a failure", "internal": "execution stopped",
        "uncertain": "outcome is uncertain", "cancelled": "execution was cancelled",
        "declined": "planner declined the supported task",
    }[record["category"]]
    return f"{LABELS[record['phase']]}: {reason}"
