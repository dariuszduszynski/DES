from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, cast

from .config import S3SourceConfig
from .db_connector import SourceDatabase
from .migration_orchestrator import MigrationOrchestrator, MigrationResult
from .packer import PackerResult, pack_files_to_directory
from .packer_planner import FileToPack, PlannerConfig

logger = logging.getLogger("des_migrate")


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s des-migrate %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run DES migration from database to archive.")
    parser.add_argument("--config", required=True, help="Path to migration config (json or yaml).")
    parser.add_argument("--dry-run", action="store_true", help="Show statistics without packing or marking.")
    parser.add_argument("--continuous", action="store_true", help="Run migration cycles continuously.")
    parser.add_argument("--interval", type=int, default=None, help="Interval between cycles in seconds (continuous).")
    args = parser.parse_args(argv)
    if args.interval is not None and args.interval <= 0:
        parser.error("--interval must be a positive integer")
    return args


def _load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    suffix = path.suffix.lower()
    raw: Dict[str, Any]
    if suffix == ".json":
        loaded = json.loads(path.read_text())
        if not isinstance(loaded, dict):
            raise ValueError("JSON config must decode to an object")
        raw = loaded
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:
            raise RuntimeError("pyyaml is required to read YAML configs") from exc

        loaded_yaml = yaml.safe_load(path.read_text())
        if not isinstance(loaded_yaml, dict):
            raise ValueError("YAML config must decode to a mapping")
        raw = loaded_yaml
    else:
        raise ValueError("Unsupported config format; use .json, .yaml, or .yml")
    return cast(Dict[str, Any], _substitute_env(raw))


def _substitute_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    if isinstance(value, str):
        return _replace_placeholders(value)
    return value


# Extracted placeholder parsing/resolution helpers to flatten branching in _replace_placeholders.
def _find_placeholder_end(text: str, start_idx: int) -> int:
    end = text.find("}", start_idx)
    if end == -1:
        raise ValueError(f"Unclosed placeholder in {text!r}")
    return end


def _parse_placeholder(placeholder: str) -> tuple[str, str | None]:
    if ":" in placeholder:
        var, default = placeholder.split(":", 1)
        return var, default
    return placeholder, None


def _substitute_placeholder(var: str, default: str | None) -> str:
    if var in os.environ:
        return os.environ[var]
    if default is not None:
        return default
    raise ValueError(f"Missing environment variable {var} for placeholder in config")


def _replace_placeholders(text: str) -> str:
    result: list[str] = []
    idx = 0
    while idx < len(text):
        if not text.startswith("${", idx):
            result.append(text[idx])
            idx += 1
            continue

        end = _find_placeholder_end(text, idx)
        placeholder = text[idx + 2 : end]
        var, default = _parse_placeholder(placeholder)
        result.append(_substitute_placeholder(var, default))
        idx = end + 1
    return "".join(result)


class LocalPacker:
    """Simple packer using local filesystem output."""

    def __init__(
        self,
        output_dir: Path,
        max_shard_size: int = 1_000_000_000,
        n_bits: int = 8,
        s3_source_config: S3SourceConfig | None = None,
    ) -> None:
        self._output_dir = output_dir
        self._config = PlannerConfig(max_shard_size_bytes=max_shard_size, n_bits=n_bits)
        self._s3_source_config = s3_source_config

    def pack_files(self, files: list[FileToPack]) -> PackerResult:
        return pack_files_to_directory(
            files,
            self._output_dir,
            self._config,
            s3_source_config=self._s3_source_config,
        )


def _build_db(cfg: Dict[str, Any]) -> SourceDatabase:
    db_cfg = cfg.get("database", {})
    return SourceDatabase(
        db_url=db_cfg["url"],
        table_name=db_cfg.get("table_name", "files"),
        uid_column=db_cfg.get("uid_column", "uid"),
        created_at_column=db_cfg.get("created_at_column", "created_at"),
        file_location_column=db_cfg.get("file_location_column", "file_location"),
        size_bytes_column=db_cfg.get("size_bytes_column", "size_bytes"),
        archived_column=db_cfg.get("archived_column", "archived"),
    )


def _build_packer(cfg: Dict[str, Any]) -> LocalPacker:
    packer_cfg = cfg.get("packer", {})
    output_dir = Path(packer_cfg.get("output_dir", "./des_output")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    max_shard_size = int(packer_cfg.get("max_shard_size", 1_000_000_000))
    n_bits = int(packer_cfg.get("n_bits", 8))
    s3_source_raw = packer_cfg.get("s3_source", {})
    s3_source_config = S3SourceConfig.from_mapping(s3_source_raw) if s3_source_raw else None
    return LocalPacker(output_dir, max_shard_size=max_shard_size, n_bits=n_bits, s3_source_config=s3_source_config)


def _build_orchestrator(cfg: Dict[str, Any]) -> MigrationOrchestrator:
    db = _build_db(cfg)
    packer = _build_packer(cfg)
    mig_cfg = cfg.get("migration", {})
    archive_age_days = int(mig_cfg.get("archive_age_days", 7))
    batch_size = int(mig_cfg.get("batch_size", 1000))
    delete_source_files = bool(mig_cfg.get("delete_source_files", False))
    return MigrationOrchestrator(
        db=db,
        packer=packer,
        archive_age_days=archive_age_days,
        batch_size=batch_size,
        delete_source_files=delete_source_files,
    )


def _run_cycle(orchestrator: MigrationOrchestrator) -> MigrationResult:
    result = orchestrator.run_migration_cycle()
    logger.info(
        "mode=single_run files_processed=%d files_migrated=%d files_failed=%d shards_created=%d "
        "total_size_bytes=%d duration=%.2f",
        result.files_processed,
        result.files_migrated,
        result.files_failed,
        result.shards_created,
        result.total_size_bytes,
        result.duration_seconds,
    )
    for err in result.errors:
        logger.error("error=%s", err)
    return result


def _run_dry_run(db: SourceDatabase, archive_age_days: int) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=archive_age_days)
    stats = db.get_archive_statistics(cutoff)
    logger.info(
        "mode=dry_run total_files=%d total_size_bytes=%d oldest=%s newest=%s",
        stats["total_files"],
        stats["total_size_bytes"],
        stats["oldest_file"],
        stats["newest_file"],
    )


def main(argv: Iterable[str] | None = None) -> None:
    _setup_logging()
    args = _parse_args(list(argv) if argv is not None else None)
    try:
        cfg_path = Path(args.config)
        config = _load_config(cfg_path)
        orchestrator = _build_orchestrator(config)
        db = _build_db(config)
        archive_age_days = int(config.get("migration", {}).get("archive_age_days", 7))
    except Exception as exc:
        logger.error('stage="config" error="%s"', exc)
        sys.exit(1)

    stop = False

    def _handle_signal(signum: int, frame: Any) -> None:  # pragma: no cover - signal path
        nonlocal stop
        stop = True
        logger.info("Received signal %s, stopping after current cycle", signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle_signal)
        except ValueError:
            pass

    if args.dry_run:
        try:
            _run_dry_run(db, archive_age_days)
            sys.exit(0)
        except Exception as exc:  # pragma: no cover - unexpected
            logger.error('stage="dry_run" error="%s"', exc)
            sys.exit(1)

    interval = args.interval or 3600
    try:
        while True:
            _run_cycle(orchestrator)
            if not args.continuous or stop:
                break
            time.sleep(interval)
            if stop:
                break
        sys.exit(0)
    except Exception as exc:  # pragma: no cover - unexpected
        logger.error('stage="run" error="%s"', exc)
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
