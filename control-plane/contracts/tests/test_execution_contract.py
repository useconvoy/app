"""Cross-process credentials never authorize another mission or a renewed expiry."""

import time

import pytest
from convoy_contracts.execution import sign_grant, verify_grant

SECRET = "test-secret-only-not-for-deployment-123456"
IDENTITY = {
    "robot_id": "r", "device_id": "d", "mission_id": "m", "boot_id": "b", "incarnation": "i",
    "release_digest": "a" * 64, "authority_epoch": 1,
}


def test_grant_is_authenticated_bounded_and_expiry_is_not_renewed():
    expiry = time.time() + 60
    token = sign_grant(IDENTITY, SECRET, expiry)
    assert verify_grant(token, SECRET) == {**IDENTITY, "expires_at": expiry}
    for invalid in (token + "a", "arbitrary", token.replace(".", "a.", 1)):
        with pytest.raises(ValueError):
            verify_grant(invalid, SECRET)
    with pytest.raises(ValueError):
        verify_grant(token, SECRET + "wrong")
    with pytest.raises(ValueError):
        verify_grant(token, SECRET, now=expiry)
    with pytest.raises(ValueError):
        sign_grant({**IDENTITY, "authority_epoch": True}, SECRET, expiry)
