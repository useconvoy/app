"""ArtifactRef claim-check library over S3-compatible object stores.

No blob crosses a Temporal boundary — `ArtifactRef` only. Works against MinIO
in compose and AWS S3 in stacks. Sync boto3 calls are pushed to worker
threads so activities stay non-blocking.
"""

import hashlib
import json
from typing import TYPE_CHECKING, Any, Callable, cast

import anyio.to_thread
import boto3
from botocore.config import Config as BotoConfig
from botocore.credentials import AssumeRoleCredentialFetcher, DeferredRefreshableCredentials
from botocore.exceptions import ClientError
from botocore.session import get_session

from convoy_core import ArtifactRef

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


class ArtifactIntegrityError(RuntimeError):
    """Raised when a fetched blob does not match its ref's sha256."""


def _assumed_role_session(role_arn: str, region: str) -> boto3.Session:
    """A boto3 session whose STS credentials refresh before expiration."""

    source = boto3.Session(region_name=region)
    source_credentials = source.get_credentials()
    if source_credentials is None:
        raise RuntimeError("CONVOY_DATA_ACCESS_ROLE_ARN is set but no source AWS credentials exist")

    def client_creator(service_name: str, **kwargs: Any) -> Any:
        factory = cast("Callable[..., Any]", getattr(source, "client"))
        return factory(service_name, **kwargs)

    fetcher = cast("Any", AssumeRoleCredentialFetcher)(
        client_creator=client_creator,
        source_credentials=source_credentials,
        role_arn=role_arn,
        extra_args={"RoleSessionName": "convoy-runtime"},
    )
    botocore_session = get_session()
    botocore_session._credentials = DeferredRefreshableCredentials(  # type: ignore[attr-defined]
        method="assume-role",
        refresh_using=fetcher.fetch_credentials,
    )
    botocore_session.set_config_variable("region", region)
    return boto3.Session(botocore_session=botocore_session)


class ArtifactStore:
    """Claim-check I/O: put bytes/JSON, get them back by `ArtifactRef`."""

    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
        access_key: str | None = None,
        secret_key: str | None = None,
        role_arn: str = "",
    ) -> None:
        self._bucket = bucket
        # SigV4 explicitly: presigned URLs default to the legacy signature
        # otherwise, which real S3 rejects (and KMS-encrypted buckets require
        # v4 unconditionally). MinIO speaks v4 as well.
        session = _assumed_role_session(role_arn, region) if role_arn else boto3.Session()
        self._client: S3Client = session.client(  # pyright: ignore[reportUnknownMemberType]
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
    @property
    def bucket(self) -> str:
        return self._bucket

    async def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> ArtifactRef:
        def _put() -> None:
            self._client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )

        await anyio.to_thread.run_sync(_put)
        return ArtifactRef(
            bucket=self._bucket,
            key=key,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            content_type=content_type,
        )

    async def put_json(self, key: str, obj: Any) -> ArtifactRef:
        data = json.dumps(obj, sort_keys=True, default=str).encode()
        return await self.put_bytes(key, data, content_type="application/json")

    async def get_bytes(self, ref: ArtifactRef) -> bytes:
        def _get() -> bytes:
            response = self._client.get_object(Bucket=ref.bucket, Key=ref.key)
            return response["Body"].read()

        data = await anyio.to_thread.run_sync(_get)
        digest = hashlib.sha256(data).hexdigest()
        if digest != ref.sha256:
            raise ArtifactIntegrityError(
                f"sha256 mismatch for s3://{ref.bucket}/{ref.key}: "
                f"expected {ref.sha256}, got {digest}"
            )
        return data

    async def get_json(self, ref: ArtifactRef) -> Any:
        return json.loads(await self.get_bytes(ref))

    async def get_json_at(self, key: str) -> Any:
        """Read a runtime-owned conventional key from this store's bucket.

        Claim-checked data always travels as an `ArtifactRef` (with its
        integrity hash); this path exists for the control plane re-reading
        documents it wrote itself at deterministic keys, where no ref is in
        hand.
        """

        def _get() -> bytes:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()

        return json.loads(await anyio.to_thread.run_sync(_get))

    # Presigned URLs are the credential-free data plane for sandboxes: the
    # trusted worker mints short-lived, capability-scoped URLs (one object, one
    # verb, one expiry) and hands them across the boundary. The holder can do
    # exactly what the URL says and nothing else — no IAM credentials move.

    def presign_get(self, ref: ArtifactRef, *, expires_seconds: int) -> str:
        """Short-lived GET capability for one existing artifact."""
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": ref.bucket, "Key": ref.key},
            ExpiresIn=expires_seconds,
        )

    def presign_get_key(self, key: str, *, expires_seconds: int) -> str:
        """GET capability for a runtime-owned conventional key in this bucket.

        The object may not exist yet — polling the URL simply returns 404
        until something writes it, which is what makes it usable as a
        one-way mailbox the worker fills after minting.
        """
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )

    def presign_put(
        self,
        key: str,
        *,
        expires_seconds: int,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Short-lived PUT capability for one conventional key. The upload
        must send the same Content-Type header the signature covers."""
        return self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self._bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_seconds,
        )

    def presign_post_prefix(
        self, key_prefix: str, *, expires_seconds: int
    ) -> tuple[str, dict[str, str]]:
        """POST-policy capability scoped to a key prefix.

        Unlike a PUT (one exact key), the policy admits any key under the
        prefix — how a sandbox uploads output files whose names are only known
        at runtime, while still being unable to write anywhere else. Returns
        the form URL and the signed fields the uploader must include.
        """
        prefix = key_prefix if key_prefix.endswith("/") else f"{key_prefix}/"
        post = self._client.generate_presigned_post(
            Bucket=self._bucket,
            Key=f"{prefix}${{filename}}",
            Conditions=[["starts-with", "$key", prefix]],
            ExpiresIn=expires_seconds,
        )
        return post["url"], {str(k): str(v) for k, v in post["fields"].items()}

    async def ensure_bucket(self) -> None:
        """Create the bucket if missing (dev/compose convenience)."""

        def _ensure() -> None:
            try:
                self._client.head_bucket(Bucket=self._bucket)
            except ClientError:
                self._client.create_bucket(Bucket=self._bucket)

        await anyio.to_thread.run_sync(_ensure)
