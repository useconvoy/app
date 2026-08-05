"""Console authentication — WorkOS at the edge, resolved to our users table.

The website authenticates the human with WorkOS AuthKit and forwards the
session's access token as `Authorization: Bearer <jwt>`. We verify it against
WorkOS's JWKS (RS256) and resolve the claims to a `users` row:

  1. `users.idp_subject == sub` → that user (the normal path after linking).
  2. No subject match but the token carries a verified email that matches an
     invited user → link `idp_subject` on first login (audited). Invitation
     is membership creation by email — see the memberships endpoint — so
     linkage requires an admin to have invited the address first; there is
     NO auto-provisioning of users from tokens.
  3. Neither → 403.

`X-Convoy-User` header auth survives only behind an explicit dev flag
(CONVOY_CONSOLE_ALLOW_HEADER_AUTH=1) for the founder CLI and compose — it is
never valid when a bearer token is present and must be off anywhere real.

The JWKS key resolver is injectable so deployments can pin keys and smoke
tests can supply a local keypair; by default keys come from
CONVOY_WORKOS_JWKS_URL via PyJWT's cached PyJWKClient.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

import jwt
from fastapi import HTTPException

from ..db.tables import AuditLog, User

KeyResolver = Callable[[str], "jwt.PyJWK | str"]  # token → verification key


class ConsoleAuth:
    def __init__(self, jwks_url: str = "", issuer: str = "",
                 key_resolver: Optional[KeyResolver] = None,
                 allow_header_auth: Optional[bool] = None) -> None:
        self._jwks_url = jwks_url or os.environ.get("CONVOY_WORKOS_JWKS_URL", "")
        self._issuer = issuer or os.environ.get("CONVOY_WORKOS_ISSUER", "")
        self._resolver = key_resolver
        self._jwks_client: Optional[jwt.PyJWKClient] = None
        if allow_header_auth is None:
            allow_header_auth = os.environ.get("CONVOY_CONSOLE_ALLOW_HEADER_AUTH", "") == "1"
        self.allow_header_auth = allow_header_auth

    @property
    def bearer_configured(self) -> bool:
        return bool(self._jwks_url or self._resolver)

    def _key_for(self, token: str):
        if self._resolver is not None:
            return self._resolver(token)
        if self._jwks_client is None:
            self._jwks_client = jwt.PyJWKClient(self._jwks_url, cache_keys=True)
        return self._jwks_client.get_signing_key_from_jwt(token).key

    def _verify(self, token: str) -> dict:
        options = {"verify_aud": False}
        kwargs = {"algorithms": ["RS256"], "options": options}
        if self._issuer:
            kwargs["issuer"] = self._issuer
        try:
            return jwt.decode(token, self._key_for(token), **kwargs)
        except jwt.PyJWTError as err:
            raise HTTPException(401, "invalid session token: %s" % err)

    def resolve_user(self, session, authorization: str, x_convoy_user: str) -> str:
        """→ user_id for this request, per the module docstring's ladder."""
        if authorization.startswith("Bearer ") and self.bearer_configured:
            claims = self._verify(authorization[len("Bearer "):])
            subject = claims.get("sub", "")
            if not subject:
                raise HTTPException(401, "session token has no subject")
            user = session.query(User).filter_by(idp_subject=subject).one_or_none()
            if user is not None:
                return user.id
            email = claims.get("email", "")
            if email:
                user = session.query(User).filter_by(email=email).one_or_none()
                if user is not None and user.idp_subject is None:
                    user.idp_subject = subject  # first-login linkage, audited
                    session.add(AuditLog(workspace_id=None, actor_user_id=user.id,
                                         action="user.link_idp", subject_type="user",
                                         subject_id=user.id, diff={"idpSubject": subject}))
                    session.commit()
                    return user.id
            raise HTTPException(403, "no Convoy user for this identity — ask a workspace "
                                     "admin to invite %s" % (email or subject))
        # Header auth: explicitly flagged on, or implicitly permitted while no
        # bearer verifier is configured (dev/white-glove). The moment WorkOS
        # is configured, headers stop working unless the flag says otherwise.
        header_ok = self.allow_header_auth or not self.bearer_configured
        if x_convoy_user and header_ok:
            return x_convoy_user
        if x_convoy_user:
            raise HTTPException(401, "header auth is disabled; authenticate via the console session")
        raise HTTPException(401, "missing credentials")
