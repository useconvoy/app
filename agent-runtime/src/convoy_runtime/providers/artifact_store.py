"""ArtifactRef claim-check library over S3-compatible object stores.

No blob crosses a Temporal boundary — `ArtifactRef` only (CLAUDE.md rule 2).
Works against MinIO in compose and AWS S3 in stacks. Sync boto3 calls are
pushed to worker threads so activities stay non-blocking.
"""

import hashlib
import json
from typing import TYPE_CHECKING, Any

import anyio.to_thread
import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from convoy_core import ArtifactRef

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


class ArtifactIntegrityError(RuntimeError):
    """Raised when a fetched blob does not match its ref's sha256."""


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
    ) -> None:
        self._bucket = bucket
        self._client: S3Client = boto3.client(  # pyright: ignore[reportUnknownMemberType]
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=BotoConfig(s3={"addressing_style": "path"}),
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

    async def ensure_bucket(self) -> None:
        """Create the bucket if missing (dev/compose convenience)."""

        def _ensure() -> None:
            try:
                self._client.head_bucket(Bucket=self._bucket)
            except ClientError:
                self._client.create_bucket(Bucket=self._bucket)

        await anyio.to_thread.run_sync(_ensure)
