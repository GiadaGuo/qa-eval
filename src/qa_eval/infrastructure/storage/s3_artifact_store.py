# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description:
# S3 / MinIO 兼容 ArtifactStore 实现。
# 通过 endpoint_url 支持本地 MinIO 部署。
# ====================================================

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from qa_eval.domain.ports.artifact_store import StoredArtifact


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class S3ArtifactStore:
    """将 artifact 持久化到 S3 或 MinIO 兼容对象存储的实现。"""

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        *,
        endpoint_url: str | None = None,
        region_name: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> None:
        import boto3

        self.bucket = bucket
        self.prefix = prefix.strip().rstrip("/")
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region_name,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )
        self._local_staging = Path(".s3-staging")
        self._local_staging.mkdir(parents=True, exist_ok=True)

    def _object_key(self, relative_path: str) -> str:
        s3_prefix = f"s3://{self.bucket}/"
        if relative_path.startswith(s3_prefix):
            return relative_path.removeprefix(s3_prefix)
        safe = relative_path.lstrip("/")
        if self.prefix:
            return f"{self.prefix}/{safe}"
        return safe

    def prepare_write(self, relative_path: str) -> Path:
        staging_path = self._local_staging / relative_path.replace("/", "_")
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        return staging_path

    def commit(
        self,
        relative_path: str,
        *,
        sha256: str | None = None,
    ) -> StoredArtifact:
        staging_path = self.prepare_write(relative_path)
        if not staging_path.exists():
            raise FileNotFoundError(f"staging artifact not found: {staging_path}")

        actual_sha256 = _sha256_file(staging_path)
        if sha256 is not None and actual_sha256 != sha256.lower():
            raise ValueError(
                f"checksum mismatch for {relative_path}: "
                f"expected {sha256}, got {actual_sha256}"
            )

        key = self._object_key(relative_path)
        self._client.upload_file(
            str(staging_path),
            self.bucket,
            key,
        )

        return StoredArtifact(
            uri=f"s3://{self.bucket}/{key}",
            path=staging_path,
            size_bytes=staging_path.stat().st_size,
            sha256=actual_sha256,
        )

    def read(self, relative_path: str) -> bytes:
        key = self._object_key(relative_path)
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def exists(self, relative_path: str) -> bool:
        key = self._object_key(relative_path)
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:  # pragma: no cover - boto3 exception polymorphism
            error_code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if error_code == "404":
                return False
            raise
