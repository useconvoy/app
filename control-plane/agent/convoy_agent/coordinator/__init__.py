"""Local execution coordinator. Robot-specific dependencies belong in adapters."""

from .engine import Coordinator, StepResult
from .journal import ExecutionJournal

__all__ = ["Coordinator", "ExecutionJournal", "StepResult"]
