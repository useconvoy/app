"""Deployment profile types.

`DeploymentProfile` and `ModelGatewayConfig` are transcribed verbatim from
DESIGN.md section 3. Every external touchpoint resolves through
`DeploymentProfile` or `EnvironmentBinding` — no component assumes shared infra.
"""

from typing import Literal

from pydantic import AnyUrl, BaseModel


class ModelEndpoint(BaseModel):
    """One model gateway endpoint. Secrets are referenced, never inlined."""

    provider: str
    base_url: AnyUrl
    api_key_ref: str = ""  # secret reference (vault/SSM), never the key


class TemporalTarget(BaseModel):
    """Temporal Cloud namespace or self-hosted endpoint."""

    host: str
    namespace: str = "default"
    tls: bool = True


class PostgresTarget(BaseModel):
    host: str
    port: int = 5432
    database: str
    user: str
    password_ref: str = ""  # secret reference, never the password


class ObjectStoreTarget(BaseModel):
    bucket: str
    region: str = "us-east-1"
    endpoint_url: AnyUrl | None = None  # None = provider default (AWS S3)


class IdentityTarget(BaseModel):
    provider: Literal["workos", "oidc", "stub"] = "workos"
    issuer: AnyUrl | None = None  # direct customer OIDC (customer-VPC mode)


class SandboxProviderConfig(BaseModel):
    provider: Literal["ecs", "e2b_hosted", "local"] = "ecs"
    default_template: str = "default"


class ModelGatewayConfig(BaseModel):
    endpoints: dict[str, ModelEndpoint]  # MVP: Convoy keys; BYO = profile edit
    approved_models: list[str]  # fallback may only permute within this set
    fallback_chains: dict[str, list[str]]  # per primary model, per deployment


class DeploymentProfile(BaseModel):
    mode: Literal["dedicated", "customer_vpc", "saas"]  # MVP: dedicated
    temporal: TemporalTarget  # cloud namespace | self-hosted endpoint
    db: PostgresTarget
    artifact_store: ObjectStoreTarget
    identity: IdentityTarget  # workos | direct customer OIDC
    sandbox: SandboxProviderConfig  # ecs (MVP) | e2b_hosted (SaaS later)
    model_gateway: ModelGatewayConfig  # endpoints, approved_models, fallback policy
