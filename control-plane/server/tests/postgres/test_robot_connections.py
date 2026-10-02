"""Run connection ownership and one-use enrollment against real PostgreSQL."""
from test_robot_connections import (
    test_connection_claim_discovery_and_no_secret_recovery,
    test_connection_setup_ownership_cancellation_and_expiry,
)

__all__ = ["test_connection_claim_discovery_and_no_secret_recovery", "test_connection_setup_ownership_cancellation_and_expiry"]
