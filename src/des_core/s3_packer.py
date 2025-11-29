"""S3-based packer that uploads DES shards after local packing."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, cast

import boto3

from .bigfiles import build_bigfile_key
from .config import DESConfig
from .packer import ShardWriteResult, pack_files_to_directory
from .packer_planner import FileToPack, PlannerConfig
from .s3_retriever import S3Config, S3WriteClientProtocol, normalize_prefix


@dataclass
class UploadedShard:
    shard: ShardWriteResult
    bucket: str
    key: str


@dataclass
class S3PackerResult:
    """Result of packing and uploading DES shards to S3."""

    uploaded: list[UploadedShard]


def pack_files_to_s3(
    files: Iterable[FileToPack],
    planner_config: PlannerConfig,
    s3_config: S3Config,
    *,
    tmp_dir: Path | None = None,
    delete_local: bool = True,
    client: S3WriteClientProtocol | None = None,
    des_config: DESConfig | None = None,
) -> S3PackerResult:
    """Plan, write, and upload DES shard files to S3.

    The function is synchronous and blocking. It first produces local shard
    files using the existing packer and then uploads them to S3 using keys
    compatible with S3ShardRetriever.
    """

    files_list = list(files)
    if not files_list:
        return S3PackerResult(uploaded=[])
    des_cfg = des_config or DESConfig.from_env()

    def _run(directory: Path) -> S3PackerResult:
        packer_result = pack_files_to_directory(files_list, directory, planner_config, des_config=des_cfg)
        s3_client: S3WriteClientProtocol = cast(
            S3WriteClientProtocol,
            client
            or boto3.client(
                "s3",
                region_name=s3_config.region_name,
                endpoint_url=s3_config.endpoint_url,
            ),
        )
        prefix = normalize_prefix(s3_config.prefix)

        uploaded: list[UploadedShard] = []
        uploaded_bigfiles: set[str] = set()
        for shard in packer_result.shards:
            data = shard.path.read_bytes()
            key = f"{prefix}{shard.path.name}"
            s3_client.put_object(Bucket=s3_config.bucket, Key=key, Body=data)
            uploaded.append(UploadedShard(shard=shard, bucket=s3_config.bucket, key=key))
            for bf_hash in shard.bigfile_hashes:
                local_path = shard.path.parent / des_cfg.bigfiles_prefix / bf_hash
                if not local_path.exists():
                    raise FileNotFoundError(f"Missing bigfile {bf_hash} at {local_path}")
                bf_key = build_bigfile_key(key, des_cfg.bigfiles_prefix, bf_hash)
                if bf_key in uploaded_bigfiles:
                    if delete_local:
                        local_path.unlink(missing_ok=True)
                    continue
                s3_client.put_object(Bucket=s3_config.bucket, Key=bf_key, Body=local_path.read_bytes())
                uploaded_bigfiles.add(bf_key)
                if delete_local:
                    local_path.unlink(missing_ok=True)
            if delete_local:
                shard.path.unlink(missing_ok=True)

        if delete_local:
            bigfiles_root = Path(directory) / des_cfg.bigfiles_prefix
            try:
                bigfiles_root.rmdir()
            except OSError:
                pass

        return S3PackerResult(uploaded=uploaded)

    if tmp_dir is None:
        with tempfile.TemporaryDirectory() as td:
            return _run(Path(td))

    tmp_path = Path(tmp_dir)
    tmp_path.mkdir(parents=True, exist_ok=True)
    return _run(tmp_path)
