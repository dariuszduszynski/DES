#!/usr/bin/env python3
"""CLI tool for managing DES watermark-based migration.

Usage:
    des-watermark-migrate --config config.yaml [--mode single|continuous]
    des-watermark-stats --config config.yaml
    des-watermark-adjust --config config.yaml --set-date 2024-12-01
"""

import argparse
import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psycopg
import yaml

from watermark_orchestrator import WatermarkMigrationOrchestrator
from des_core.database_source import SourceDatabaseConfig
from des_core.packer_planner import PackerConfig
from des_core.archive_config import ArchiveConfigRepository

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def load_config(config_path: str) -> dict:
    """Load YAML configuration file."""
    with open(config_path) as f:
        return yaml.safe_load(f)


async def run_migration(config_path: str, mode: str = "single", interval: int = 3600):
    """Run watermark migration in single or continuous mode."""
    config = load_config(config_path)
    
    # Database connection
    db_url = config["database"]["url"]
    conn = psycopg.connect(db_url)
    
    # Config connection (may be same as db)
    config_db_url = config.get("watermark", {}).get("config_db_url", db_url)
    config_conn = psycopg.connect(config_db_url) if config_db_url != db_url else conn
    
    # Source config
    source_config = SourceDatabaseConfig(
        dsn=db_url,
        table_name=config["database"]["table_name"],
        uid_column=config["database"].get("uid_column", "uid"),
        created_at_column=config["database"].get("created_at_column", "created_at"),
        location_column=config["database"].get("location_column", "file_location"),
        lag_days=config["database"].get("lag_days", 7),
        page_size=config["database"].get("page_size", 1000),
    )
    
    # Packer config
    packer_config = PackerConfig(
        output_dir=config["packer"]["output_dir"],
        n_bits=config["packer"].get("n_bits", 8),
        max_shard_size=config["packer"].get("max_shard_size", 1_000_000_000),
    )
    
    # Create orchestrator
    orchestrator = WatermarkMigrationOrchestrator(
        db_connection=conn,
        config_connection=config_conn,
        source_config=source_config,
        packer_config=packer_config,
        delete_source_files=config.get("migration", {}).get("delete_source_files", False),
    )
    
    # Initialize
    await orchestrator.initialize()
    logger.info("Watermark migration initialized")
    
    if mode == "single":
        # Run once
        logger.info("Running single migration cycle...")
        result = await orchestrator.run_cycle()
        
        print("\n" + "="*60)
        print("Migration Cycle Complete")
        print("="*60)
        print(f"Window:          {result.window_start} → {result.window_end}")
        print(f"Files processed: {result.files_processed:,}")
        print(f"Files migrated:  {result.files_migrated:,}")
        print(f"Files failed:    {result.files_failed:,}")
        print(f"Shards created:  {result.shards_created:,}")
        print(f"Total size:      {result.total_size_bytes / (1024**3):.2f} GB")
        print(f"Duration:        {result.duration_seconds:.1f}s")
        print(f"Throughput:      {result.total_size_bytes / (1024**2) / result.duration_seconds:.1f} MB/s")
        if result.errors:
            print(f"\nErrors ({len(result.errors)}):")
            for error in result.errors[:10]:  # Show first 10
                print(f"  - {error}")
        print("="*60 + "\n")
        
    elif mode == "continuous":
        # Run continuously
        logger.info("Running in continuous mode (interval=%ds)...", interval)
        cycle_count = 0
        
        while True:
            try:
                cycle_count += 1
                logger.info("Starting cycle %d...", cycle_count)
                
                result = await orchestrator.run_cycle()
                
                logger.info(
                    "Cycle %d complete: processed=%d, migrated=%d, failed=%d, duration=%.1fs",
                    cycle_count,
                    result.files_processed,
                    result.files_migrated,
                    result.files_failed,
                    result.duration_seconds,
                )
                
                # Sleep until next cycle
                logger.info("Sleeping for %d seconds...", interval)
                time.sleep(interval)
                
            except KeyboardInterrupt:
                logger.info("Received interrupt, shutting down...")
                break
            except Exception as e:
                logger.error("Cycle failed: %s", e, exc_info=True)
                logger.info("Retrying in %d seconds...", interval)
                time.sleep(interval)
    
    # Cleanup
    conn.close()
    if config_conn != conn:
        config_conn.close()


async def show_stats(config_path: str):
    """Show migration statistics and pending files."""
    config = load_config(config_path)
    
    db_url = config["database"]["url"]
    conn = psycopg.connect(db_url)
    
    config_db_url = config.get("watermark", {}).get("config_db_url", db_url)
    config_conn = psycopg.connect(config_db_url) if config_db_url != db_url else conn
    
    # Get watermark info
    repo = ArchiveConfigRepository(config_conn)
    archived_until, lag_days = await repo.get_config()
    window = await repo.compute_window(datetime.now(timezone.utc))
    
    # Query pending files
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 
            COUNT(*) as count,
            MIN(created_at) as oldest,
            MAX(created_at) as newest,
            SUM(size_bytes) as total_bytes
        FROM files
        WHERE created_at > %s AND created_at <= %s
    """, (window.window_start, window.window_end))
    
    row = cursor.fetchone()
    pending_count = row[0] or 0
    oldest = row[1]
    newest = row[2]
    total_bytes = row[3] or 0
    
    # Query archived files
    cursor.execute("""
        SELECT 
            COUNT(*) as count,
            SUM(size_bytes) as total_bytes
        FROM files
        WHERE created_at <= %s
    """, (archived_until,))
    
    row = cursor.fetchone()
    archived_count = row[0] or 0
    archived_bytes = row[1] or 0
    
    print("\n" + "="*70)
    print("DES Watermark Migration - Statistics")
    print("="*70)
    print("\n📊 Watermark Status:")
    print(f"  Current watermark:    {archived_until}")
    print(f"  Lag days:             {lag_days}")
    print(f"  Target cutoff:        {window.window_end}")
    print(f"  Lag behind target:    {(window.window_end - archived_until).days} days")
    
    print("\n📦 Already Archived:")
    print(f"  Files archived:       {archived_count:,}")
    print(f"  Size archived:        {archived_bytes / (1024**3):.2f} GB")
    
    print("\n⏳ Pending (Current Window):")
    print(f"  Window start:         {window.window_start}")
    print(f"  Window end:           {window.window_end}")
    print(f"  Files pending:        {pending_count:,}")
    if oldest and newest:
        print(f"  Oldest file:          {oldest}")
        print(f"  Newest file:          {newest}")
    print(f"  Total size:           {total_bytes / (1024**3):.2f} GB")
    
    if pending_count > 0:
        avg_file_size = total_bytes / pending_count
        print(f"  Avg file size:        {avg_file_size / (1024**2):.2f} MB")
        
        # Estimate processing time (assume 50 MB/s throughput)
        est_duration = total_bytes / (50 * 1024**2)
        print(f"  Est. processing time: {est_duration / 60:.1f} minutes")
    
    print("\n" + "="*70 + "\n")
    
    conn.close()
    if config_conn != conn:
        config_conn.close()


async def adjust_watermark(config_path: str, set_date: Optional[str] = None, days_offset: Optional[int] = None):
    """Manually adjust the watermark."""
    config = load_config(config_path)
    
    config_db_url = config.get("watermark", {}).get("config_db_url", config["database"]["url"])
    conn = psycopg.connect(config_db_url)
    
    cursor = conn.cursor()
    
    # Get current watermark
    cursor.execute("SELECT archived_until, lag_days FROM des_archive_config WHERE id = 1")
    row = cursor.fetchone()
    current_watermark = row[0]
    lag_days = row[1]
    
    print(f"\n⚠️  Watermark Adjustment")
    print(f"Current watermark: {current_watermark}")
    
    if set_date:
        # Set to specific date
        new_watermark = datetime.fromisoformat(set_date)
        cursor.execute(
            "UPDATE des_archive_config SET archived_until = %s WHERE id = 1",
            (new_watermark,)
        )
        conn.commit()
        print(f"✅ Watermark set to: {new_watermark}")
        
    elif days_offset is not None:
        # Adjust by days offset
        cursor.execute(
            "UPDATE des_archive_config SET archived_until = archived_until + INTERVAL '%s days' WHERE id = 1",
            (days_offset,)
        )
        conn.commit()
        
        # Get new value
        cursor.execute("SELECT archived_until FROM des_archive_config WHERE id = 1")
        new_watermark = cursor.fetchone()[0]
        print(f"✅ Watermark adjusted by {days_offset} days to: {new_watermark}")
    
    else:
        print("❌ No adjustment specified (use --set-date or --days-offset)")
    
    conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="DES Watermark Migration Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run single migration cycle
  %(prog)s migrate --config config.yaml --mode single
  
  # Run continuous migration (hourly)
  %(prog)s migrate --config config.yaml --mode continuous --interval 3600
  
  # Show statistics
  %(prog)s stats --config config.yaml
  
  # Adjust watermark to specific date
  %(prog)s adjust --config config.yaml --set-date 2024-12-01
  
  # Move watermark forward by 1 day (skip files)
  %(prog)s adjust --config config.yaml --days-offset 1
  
  # Move watermark back by 1 day (replay)
  %(prog)s adjust --config config.yaml --days-offset -1
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Migrate command
    migrate_parser = subparsers.add_parser("migrate", help="Run migration")
    migrate_parser.add_argument("--config", required=True, help="Path to config file")
    migrate_parser.add_argument(
        "--mode",
        choices=["single", "continuous"],
        default="single",
        help="Migration mode"
    )
    migrate_parser.add_argument(
        "--interval",
        type=int,
        default=3600,
        help="Interval between cycles in continuous mode (seconds)"
    )
    
    # Stats command
    stats_parser = subparsers.add_parser("stats", help="Show migration statistics")
    stats_parser.add_argument("--config", required=True, help="Path to config file")
    
    # Adjust command
    adjust_parser = subparsers.add_parser("adjust", help="Adjust watermark")
    adjust_parser.add_argument("--config", required=True, help="Path to config file")
    adjust_parser.add_argument("--set-date", help="Set watermark to specific date (ISO format)")
    adjust_parser.add_argument("--days-offset", type=int, help="Adjust watermark by days (+/-)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    if args.command == "migrate":
        asyncio.run(run_migration(args.config, args.mode, args.interval))
    elif args.command == "stats":
        asyncio.run(show_stats(args.config))
    elif args.command == "adjust":
        asyncio.run(adjust_watermark(args.config, args.set_date, args.days_offset))


if __name__ == "__main__":
    main()
