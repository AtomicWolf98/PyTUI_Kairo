"""Atomic, content-addressed blob storage."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import uuid
from pathlib import Path

from kairo_kernel.contracts.enums import ErrorCode
from kairo_kernel.contracts.identifiers import ResourceId
from kairo_kernel.contracts.json import JsonObject
from kairo_kernel.contracts.support import ResourceDescriptor
from kairo_kernel.errors import KernelResult
from kairo_kernel.storage._errors import failure

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BlobStore:
    """Store immutable bytes under their SHA-256 digest."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self._lock = asyncio.Lock()

    async def put(
        self,
        content: bytes,
        *,
        name: str = "",
        media_type: str = "application/octet-stream",
        metadata: JsonObject = JsonObject(),
    ) -> KernelResult[ResourceDescriptor]:
        digest = hashlib.sha256(content).hexdigest()
        try:
            async with self._lock:
                await asyncio.to_thread(self._write_atomic, digest, content)
            return KernelResult.success(
                ResourceDescriptor(
                    resource_id=ResourceId(digest),
                    uri=f"blob://sha256/{digest}",
                    name=name or digest,
                    media_type=media_type,
                    size_bytes=len(content),
                    sha256=digest,
                    metadata=metadata,
                )
            )
        except Exception as exc:
            return failure(
                ErrorCode.INTERNAL,
                f"Failed to store blob: {exc}",
                "blob.put",
                retryable=True,
            )

    async def get(self, resource_id: ResourceId) -> KernelResult[bytes]:
        digest = self._digest(resource_id)
        if digest is None:
            return failure(ErrorCode.INVALID_ARGUMENT, "Blob id is not a SHA-256 digest.", "blob.get")
        try:
            path = self._path(digest)
            if not path.is_file():
                return failure(ErrorCode.NOT_FOUND, "Blob not found.", "blob.get")
            content = await asyncio.to_thread(path.read_bytes)
            if hashlib.sha256(content).hexdigest() != digest:
                return failure(ErrorCode.INTERNAL, "Blob checksum verification failed.", "blob.get")
            return KernelResult.success(content)
        except Exception as exc:
            return failure(
                ErrorCode.INTERNAL,
                f"Failed to read blob: {exc}",
                "blob.get",
                retryable=True,
            )

    async def exists(self, resource_id: ResourceId) -> bool:
        digest = self._digest(resource_id)
        return False if digest is None else await asyncio.to_thread(self._path(digest).is_file)

    async def delete(self, resource_id: ResourceId) -> KernelResult[bool]:
        digest = self._digest(resource_id)
        if digest is None:
            return failure(ErrorCode.INVALID_ARGUMENT, "Blob id is not a SHA-256 digest.", "blob.delete")
        try:
            async with self._lock:
                path = self._path(digest)
                if not path.exists():
                    return failure(ErrorCode.NOT_FOUND, "Blob not found.", "blob.delete")
                await asyncio.to_thread(path.unlink)
            return KernelResult.success(True)
        except Exception as exc:
            return failure(
                ErrorCode.INTERNAL,
                f"Failed to delete blob: {exc}",
                "blob.delete",
                retryable=True,
            )

    def _write_atomic(self, digest: str, content: bytes) -> None:
        path = self._path(digest)
        if path.is_file():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise OSError("existing blob checksum mismatch")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.parent / f".{digest}.{uuid.uuid4().hex}.tmp"
        try:
            with open(temp, "xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    def _path(self, digest: str) -> Path:
        return self.root / digest[:2] / digest[2:]

    @staticmethod
    def _digest(resource_id: ResourceId) -> str | None:
        value = str(resource_id)
        if value.startswith("sha256:"):
            value = value[7:]
        return value if _SHA256.fullmatch(value) else None
