"""Real grant signatures, fixed authority bounds, and live trust-store rotation."""

import base64
import copy
import json
import os

import jwt
import pytest
from convoy_contracts.grants import GrantVerifier, SigningKeys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

NOW = 1_800_000_000.125
IDENTITY = {"robot_id": "robot", "device_id": "device", "mission_id": "mission", "boot_id": "boot",
            "incarnation": "incarnation", "release_digest": "a" * 64, "authority_epoch": 1}


def key_entry(purpose, kid=None):
    key = Ed25519PrivateKey.generate()
    return {"kid": kid or purpose + "-1", "purpose": purpose, "audience": "convoy-" + purpose,
            "private_key_pem": key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                  serialization.NoEncryption()).decode()}


def write(path, value):
    temporary = path.with_suffix(".next")
    temporary.write_text(json.dumps(value) if not isinstance(value, str) else value)
    temporary.chmod(0o600)
    temporary.replace(path)


@pytest.fixture
def authority(tmp_path, monkeypatch):
    monkeypatch.setattr("convoy_contracts.grants.time.time", lambda: NOW)
    private = {"schema_version": 1, "issuer": "convoy-test", "active": {"action": "action-1", "planner": "planner-1"},
               "keys": [key_entry("action"), key_entry("planner")]}
    path = tmp_path / "private.json"
    write(path, private)
    signer = SigningKeys(path)
    public = tmp_path / "action.json"
    write(public, signer.verification_document("action"))
    return path, private, signer, public, GrantVerifier(public, purpose="action")


def claims(**changes):
    return {"v": 1, "iss": "convoy-test", "aud": "convoy-action", "purpose": "action", "identity": IDENTITY,
            "iat": NOW, "nbf": NOW, "exp": NOW + 60, **changes}


def token(private, payload=None, **headers):
    return jwt.encode(claims() if payload is None else payload, private["keys"][0]["private_key_pem"],
                      algorithm="EdDSA", headers={"typ": "convoy-execution+jwt", "kid": "action-1", **headers})


def raw_token(private, header, payload):
    def b64(data):
        return base64.urlsafe_b64encode(data.encode()).rstrip(b"=")
    body = b64(header) + b"." + b64(payload)
    key = serialization.load_pem_private_key(private["keys"][0]["private_key_pem"].encode(), password=None)
    return (body + b"." + base64.urlsafe_b64encode(key.sign(body)).rstrip(b"=")).decode()


def test_real_signatures_keep_original_expiry_and_normalized_legacy_shape(authority, tmp_path):
    _, _, signer, _, verifier = authority
    original_identity = copy.deepcopy(IDENTITY)
    for purpose in ("action", "planner"):
        public = signer.verification_document(purpose)
        assert "PRIVATE" not in json.dumps(public)
        assert {key["kid"] for key in public["keys"]} == {purpose + "-1"}
        path = tmp_path / (purpose + ".json")
        write(path, public)
        verifier = GrantVerifier(str(path), purpose=purpose)
        assert (verifier.issuer, verifier.audience, verifier.purpose) == ("convoy-test", "convoy-" + purpose, purpose)
        signed = signer.sign(IDENTITY, purpose, NOW + 60)
        expected = {**IDENTITY, "expires_at": NOW + 60, **({"purpose": "planner"} if purpose == "planner" else {})}
        assert verifier.verify(signed) == verifier.verify(signed, now=NOW + 59.999) == expected
        with pytest.raises(ValueError):
            verifier.verify(signed, now=NOW + 60)
        with pytest.raises(AttributeError):
            verifier.purpose = "other"
    assert IDENTITY == original_identity


@pytest.mark.parametrize("changes", [
    {"iss": "another-issuer"}, {"aud": "convoy-planner"}, {"aud": ["convoy-action"]}, {"purpose": "planner"},
    {"v": True}, {"identity": {**IDENTITY, "authority_epoch": True}}, {"identity": {**IDENTITY, "extra": 1}},
    {"iat": NOW + 1, "nbf": NOW + 1}, {"nbf": NOW - 1}, {"exp": NOW + 3601.001}, {"exp": NOW},
    {"exp": True}, {"iat": "1800000000"}, {"nbf": float("nan")}, {"exp": float("inf")}, {"extra": 1},
])
def test_signed_tokens_still_require_exact_trust_and_time_contract(authority, changes):
    _, private, _, _, verifier = authority
    with pytest.raises(ValueError, match="invalid or expired execution grant"):
        verifier.verify(token(private, claims(**changes)))


@pytest.mark.parametrize("headers", [{"typ": "JWT"}, {"kid": "unknown"}, {"kid": "../private"},
                                      {"jku": "https://example.invalid/keys"}, {"crit": ["extra"]}])
def test_headers_are_local_fixed_and_exact(authority, headers):
    _, private, _, _, verifier = authority
    with pytest.raises(ValueError):
        verifier.verify(token(private, **headers))


def test_algorithm_confusion_wrong_key_and_private_material_cannot_verify(authority):
    _, private, _, public_path, verifier = authority
    forged = jwt.encode(claims(), "x" * 32, algorithm="HS256",
                        headers={"kid": "action-1", "typ": "convoy-execution+jwt"})
    with pytest.raises(ValueError):
        verifier.verify(forged)
    wrong_private = {"keys": [key_entry("action")]}
    with pytest.raises(ValueError):
        verifier.verify(token(wrong_private))
    with pytest.raises(ValueError):
        GrantVerifier(public_path, purpose="planner")
    write(public_path, private)
    with pytest.raises(ValueError):
        GrantVerifier(public_path, purpose="action")
    write(public_path, {"schema_version": 1, "issuer": "convoy-test", "audience": "convoy-action", "purpose": "action",
                        "keys": [{"kid": "action-1", "public_key_pem": private["keys"][0]["private_key_pem"]}]})
    with pytest.raises(ValueError):
        GrantVerifier(public_path, purpose="action")


def test_rotation_overlap_removal_and_empty_revocation_have_no_cached_key_fallback(authority):
    path, private, signer, public_path, verifier = authority
    old = signer.sign(IDENTITY, "action", NOW + 60)
    rotated = copy.deepcopy(private)
    rotated["keys"].append(key_entry("action", "action-2"))
    rotated["active"]["action"] = "action-2"
    write(path, rotated)
    new = signer.sign(IDENTITY, "action", NOW + 60)
    with pytest.raises(ValueError):
        verifier.verify(new)  # verifier must first receive the public rotation
    public = signer.verification_document("action")
    write(public_path, public)
    assert verifier.verify(old) == verifier.verify(new)
    public["keys"] = [entry for entry in public["keys"] if entry["kid"] == "action-2"]
    write(public_path, public)
    with pytest.raises(ValueError):
        verifier.verify(old)  # also models a service's after-inference recheck
    assert verifier.verify(new)["expires_at"] == NOW + 60
    public["keys"] = []
    write(public_path, public)
    assert GrantVerifier(public_path, purpose="action").purpose == "action"
    with pytest.raises(ValueError):
        verifier.verify(new)
    public_path.unlink()
    with pytest.raises(ValueError):
        verifier.verify(new)
    path.unlink()
    with pytest.raises(ValueError):
        signer.sign(IDENTITY, "action", NOW + 60)
    with pytest.raises(ValueError):
        signer.verification_document("action")


@pytest.mark.parametrize("field", ["issuer", "audience"])
def test_trust_anchors_cannot_change_under_live_objects(authority, field):
    path, private, signer, public_path, verifier = authority
    signed = signer.sign(IDENTITY, "action", NOW + 60)
    public = signer.verification_document("action")
    public[field] = "replacement"
    write(public_path, public)
    with pytest.raises(ValueError):
        verifier.verify(signed)
    if field == "issuer":
        private[field] = "replacement"
    else:
        private["keys"][0][field] = "replacement"
    write(path, private)
    with pytest.raises(ValueError):
        signer.sign(IDENTITY, "action", NOW + 60)
    with pytest.raises(ValueError):
        signer.verification_document("action")


def test_duplicate_json_fields_are_rejected_even_when_signature_is_valid(authority):
    path, private, _, public_path, verifier = authority
    valid_header = '{"typ":"convoy-execution+jwt","kid":"action-1","alg":"EdDSA"}'
    valid_payload = json.dumps(claims())
    bad_header = valid_header[:-1] + ',"alg":"EdDSA"}'
    bad_payload = valid_payload[:-1] + ',"exp":1800000060.125}'
    nested_duplicate = valid_payload.replace('"robot_id": "robot"', '"robot_id":"robot","robot_id":"robot"')
    for header, payload in ((bad_header, valid_payload), (valid_header, bad_payload), (valid_header, nested_duplicate)):
        with pytest.raises(ValueError):
            verifier.verify(raw_token(private, header, payload))
    write(path, json.dumps(private).replace('"issuer": "convoy-test"', '"issuer":"convoy-test","issuer":"convoy-test"'))
    with pytest.raises(ValueError):
        SigningKeys(path)
    public_path.write_text('{"keys":[],"keys":[]}')
    with pytest.raises(ValueError):
        verifier.verify(token(private))


@pytest.mark.parametrize("bad_token", [None, b"token", "", "x" * 8193, "a.b", "a.b.c.d", "a.b.\N{SNOWMAN}",
                                       "a.b.c", "=.b.c"])
def test_malformed_bounded_tokens_fail_without_leaking_input(authority, bad_token):
    verifier = authority[-1]
    with pytest.raises(ValueError) as raised:
        verifier.verify(bad_token)
    assert str(raised.value) == "invalid or expired execution grant"


@pytest.mark.parametrize("change", ["duplicate-kid", "same-key", "same-audience", "missing-purpose", "wrong-active",
                                    "unknown-field", "wrong-version", "too-many", "bad-purpose", "audience-drift"])
def test_signing_configuration_rejects_ambiguous_authority(authority, change):
    path, private, _, _, _ = authority
    if change == "duplicate-kid":
        private["keys"][1]["kid"] = "action-1"
    elif change == "same-key":
        private["keys"][1]["private_key_pem"] = private["keys"][0]["private_key_pem"]
    elif change == "same-audience":
        private["keys"][1]["audience"] = "convoy-action"
    elif change == "missing-purpose":
        private["keys"].pop()
    elif change == "wrong-active":
        private["active"]["action"] = "planner-1"
    elif change == "unknown-field":
        private["url"] = "https://example.invalid/key"
    elif change == "wrong-version":
        private["schema_version"] = True
    elif change == "too-many":
        private["keys"] *= 5
    elif change == "bad-purpose":
        private["keys"][0]["purpose"] = ["action"]
    else:
        extra = key_entry("action", "action-2")
        extra["audience"] = "different"
        private["keys"].append(extra)
    write(path, private)
    with pytest.raises(ValueError):
        SigningKeys(path)


def test_file_permissions_types_and_size_are_checked_on_every_use(authority, tmp_path):
    path, _, signer, public, verifier = authority
    signed = signer.sign(IDENTITY, "action", NOW + 60)
    path.chmod(0o640)
    with pytest.raises(ValueError):
        signer.sign(IDENTITY, "action", NOW + 60)
    public.chmod(0o666)
    with pytest.raises(ValueError):
        verifier.verify(signed)
    public.chmod(0o644)
    assert verifier.verify(signed)["mission_id"] == "mission"
    link = tmp_path / "linked.json"
    link.symlink_to(public)
    with pytest.raises(ValueError):
        GrantVerifier(link, purpose="action")
    with pytest.raises(ValueError):
        GrantVerifier(tmp_path, purpose="action")
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo, 0o600)
    with pytest.raises(ValueError):
        GrantVerifier(fifo, purpose="action")
    public.write_bytes(b" " * (64 * 1024 + 1))
    with pytest.raises(ValueError):
        verifier.verify(signed)


@pytest.mark.parametrize("expiry", [NOW, NOW + 3601.001, True, "later", float("inf"), 10**1000])
def test_signer_rejects_invalid_or_renewed_authority(authority, expiry):
    with pytest.raises(ValueError, match="cannot sign execution grant"):
        authority[2].sign(IDENTITY, "action", expiry)


def test_injected_clock_and_identity_types_are_strict(authority):
    signer, verifier = authority[2], authority[-1]
    signed = signer.sign(IDENTITY, "action", NOW + 60)
    for now in (True, "now", float("nan"), 10**1000, NOW - 0.001):
        with pytest.raises(ValueError):
            verifier.verify(signed, now=now)
    for identity in (None, [], {**IDENTITY, "authority_epoch": True}):
        with pytest.raises(ValueError):
            signer.sign(identity, "action", NOW + 60)
    for purpose in (None, [], "other"):
        with pytest.raises(ValueError):
            signer.sign(IDENTITY, purpose, NOW + 60)
