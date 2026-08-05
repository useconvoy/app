"""Compatibility shim — the event taxonomy now lives in the co-owned
convoy_core package (core/convoy_core/events.py). Import from convoy_core in
new code; this module re-exports so existing imports and the committed corpus
keep working unchanged."""

from convoy_core.mission_events import *  # noqa: F401,F403
from convoy_core.mission_events import _EventBase, _EventEnvelope  # noqa: F401
