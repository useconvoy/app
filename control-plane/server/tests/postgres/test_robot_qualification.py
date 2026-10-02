"""Run the same device-bound request/report lifecycle against real PostgreSQL."""
from test_robot_qualification import (
    test_expiry_rebinding_and_failed_reports_do_not_appear_ready,
    test_registered_deployment_pins_interfaces_and_rechecks_qualification_at_claim,
    test_report_is_immutable_scoped_and_bound_to_real_request,
)

__all__ = [
    "test_expiry_rebinding_and_failed_reports_do_not_appear_ready",
    "test_report_is_immutable_scoped_and_bound_to_real_request",
    "test_registered_deployment_pins_interfaces_and_rechecks_qualification_at_claim",
]
