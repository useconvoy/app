"""Constants shared by the agent and the server. Keep stdlib-only and 3.10-compatible."""

PROTOCOL_VERSION = 1

COMMAND_TYPES = ("deploy", "rollback", "eval", "health", "collect")
MUTATING_COMMANDS = {"deploy", "rollback"}

# Agent deploy stages, in order. Any stage may transition to ROLLBACK/FAILED.
STAGES = (
    "idle",
    "pending",
    "preflight",
    "downloading",
    "staged",
    "cutover",
    "health",
    "eval",
    "active",
    "rollback",
    "failed",
)

HEALTH = ("unknown", "ok", "degraded", "failed")

# Failure codes emitted by the agent. Structured, stable, redacted.
FAILURE_CODES = {
    "PREFLIGHT_MEMORY": "Release does not fit the shared-memory budget",
    "PREFLIGHT_DISK": "Not enough free disk for the release artifacts",
    "PREFLIGHT_COMPAT": "Runtime/JetPack tuple is not compatible with the release",
    "DOWNLOAD_FAILED": "Artifact download failed after retries",
    "DIGEST_MISMATCH": "Downloaded artifact hash or size did not match the release manifest",
    "RUNTIME_START_FAILED": "Runtime process failed to start",
    "HEALTH_TIMEOUT": "Runtime did not become healthy within the timeout",
    "HEALTH_FAILED": "Runtime health probe failed",
    "EVAL_FAILED": "Eval gate did not pass thresholds",
    "EVAL_ERROR": "Eval run errored",
    "OOM": "Runtime was killed or reported out-of-memory",
    "ROLLBACK_FAILED": "Rollback to known-good release failed",
    "ROBOT_HOOK_FAILED": "Robot integration hook failed",
    "STALE_ACK": "Server rejected an acknowledgement as stale",
    "INTERRUPTED": "Agent restarted during a deploy; recovered by rollback",
}

# Sensor value meaning "not available". Never report 0 for a missing sensor.
UNAVAILABLE = None
