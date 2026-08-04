"""Shared test configuration (fast lane defaults: no network, no containers)."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
