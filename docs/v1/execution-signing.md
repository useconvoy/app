# Execution grant signing

The API can issue Ed25519-signed JWT mission grants. The action worker and planner
receive public verification keys, which cannot create a valid new grant. Original
robot, device, mission, release, boot, incarnation, authority epoch and expiry
remain bound to each request. This authorizes bounded inference; it does not
guarantee that a model's output is correct or safe.

## Configuration and deployment

| Process | Configuration | Authority |
|---|---|---|
| API | `CONVOY_EXECUTION_SIGNING_KEYS_FILE` | Private action and planner signing keys |
| Action worker | `CONVOY_ACTION_VERIFICATION_KEYS_FILE` | Public action keys only |
| Planner | `CONVOY_PLANNER_VERIFICATION_KEYS_FILE` | Public planner keys only |
| Local-owner coordinator | Both public-file paths and separate probe credentials | Distributes each public path to its model process |
| External-endpoint coordinator | Device/probe credentials and received grants | Forwards issued grants; no signing keys |
| Evaluation jobs | Database access | No execution keys |

The signing document contains exactly `schema_version: 1`, `issuer`, an `active`
map from `action` and `planner` to key IDs, and a `keys` array. Entries contain
`kid`, `purpose`, `audience`, and `private_key_pem`. Each purpose needs its own key
material and audience. Overlapping keys for a purpose share its audience.

A public document contains exactly `schema_version: 1`, `issuer`, `audience`,
`purpose`, and `keys`; entries contain only `kid` and `public_key_pem`.
`SigningKeys.verification_document(purpose)` derives this public-only document.
An empty public key set revokes all grants for that verifier. These are trusted
operator-installed files; a token never selects a URL or filesystem path.

Files must be regular, nonsymlink, user- or root-owned, and at most 64 KiB with at
most eight keys. Private files must have no group/other permissions; public files
must not be group/other writable. Use protected parent directories and atomic
replacement. If a secret store projects symlinks, copy into an appropriately owned
regular file before process startup and on rotation.

Asymmetric configuration cannot coexist with either legacy HMAC execution setting.
Invalid, missing or empty configured paths fail closed, without automatic fallback.
Legacy HMAC remains for existing development fixtures. Scripted Compose and
unapplied AWS staging templates still select that legacy mode; migrate and qualify
them before crossing provider or customer trust boundaries.

The local activation harness generates disposable keys and passes the private
path only to its API process. Processes still share an OS user, so this proves
distribution, not filesystem isolation. Hosted services need separate identities,
API-only private mounts, read-only public mounts, verified TLS and restricted
networking. This change does not provision a hosted deployment.

## Verification and rotation

Verification fixes `EdDSA`, `convoy-execution+jwt` type, issuer, single-string
audience, purpose and version. Only Ed25519 keys are accepted. Header/payload
fields are exact; duplicate fields, unsupported headers, algorithm substitutions,
malformed identities and nonfinite timestamps are rejected. PyJWT verifies the
signature and issuer/audience. Strict time checks enforce original expiry and at
most 3,601 seconds from issue. There is no clock leeway: remote hosts need measured
clock agreement. The coordinator independently enforces monotonic deadlines.

Every verification rereads the bounded public file. The verifier pins issuer,
audience and purpose at construction; changing those anchors needs a restart.
Key membership can change without restarting:

1. Publish old and new public keys to every verifier for that purpose.
2. Switch the API's active key atomically. Keep the old public key while issued
   grants drain through their original expiry.
3. Remove the old public key. The next check rejects its grants.

Emergency revocation removes the public key immediately. Model services check
again after session preparation and inference before returning results. Rejection
poisons that session rather than replaying its consumed decision. Revocation does
not retract an already returned action, deliver a physical stop, or stop code
already executing inside an inference call. Local cancellation and deadline
authority remain with the coordinator and controller.

Repeat claims preserve the stored action token. Planner grants may be signed
again with the same identity and original expiry; normalized session identity
excludes key ID and issue time. Rotation never resets sequence numbers or extends
a mission. After retirement, an old stored action token stays rejected even if
management returns it again; new work requires normal admission/reconciliation.

Crypto lives in optional `convoy-contracts[signing]`. Base contracts and the device
agent remain dependency-free. Server/model packages pin PyJWT 2.15.0 and
cryptography 50.0.1. Fixed algorithms and audience checks follow the
[PyJWT guidance](https://pyjwt.readthedocs.io/en/stable/api.html) and
[JWT best current practices](https://www.rfc-editor.org/info/rfc8725/).
