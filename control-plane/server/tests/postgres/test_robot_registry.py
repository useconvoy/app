"""The registry HTTP boundary is also exercised on real PostgreSQL."""
from test_robot_registry import (
    test_fleet_assignment_is_atomic_project_scoped_and_detects_stale_writes,
    test_invalid_mechanics_are_rejected,
    test_physical_robot_and_simulation_pin_same_immutable_profile,
    test_registry_is_owner_scoped_and_requires_operator,
    test_source_revision_engine_and_device_kind_must_match,
)

__all__ = [
    "test_fleet_assignment_is_atomic_project_scoped_and_detects_stale_writes",
    "test_invalid_mechanics_are_rejected",
    "test_physical_robot_and_simulation_pin_same_immutable_profile",
    "test_registry_is_owner_scoped_and_requires_operator",
    "test_source_revision_engine_and_device_kind_must_match",
]
