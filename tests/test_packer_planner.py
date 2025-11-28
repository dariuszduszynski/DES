from datetime import datetime

import pytest

from des_core.packer_planner import (
    FileToPack,
    PlannerConfig,
    ShardKey,
    build_pack_plan,
    estimate_shard_counts,
)


def test_simple_grouping_single_shard() -> None:
    files = [
        FileToPack(uid="100", created_at=datetime(2024, 1, 1), size_bytes=10),
        FileToPack(uid="356", created_at=datetime(2024, 1, 1), size_bytes=15),  # same modulo shard as 100
    ]
    config = PlannerConfig(max_shard_size_bytes=100, n_bits=8)

    plan = build_pack_plan(files, config)

    assert len(plan.shards) == 1
    shard = plan.shards[0]
    assert shard.key.date_dir == "20240101"
    assert shard.total_size_bytes == 25
    assert [f.uid for f in shard.files] == ["100", "356"]


def test_shard_splitting_by_size() -> None:
    files = [
        FileToPack(uid="200", created_at=datetime(2024, 1, 2), size_bytes=60),
        FileToPack(uid="201", created_at=datetime(2024, 1, 2), size_bytes=60),
        FileToPack(uid="202", created_at=datetime(2024, 1, 2), size_bytes=60),
    ]
    config = PlannerConfig(max_shard_size_bytes=100, n_bits=8)

    plan = build_pack_plan(files, config)

    assert len(plan.shards) == 3
    assert all(shard.total_size_bytes <= config.max_shard_size_bytes for shard in plan.shards)
    assert [len(shard.files) for shard in plan.shards] == [1, 1, 1]


def test_multiple_shard_keys_are_separated() -> None:
    files = [
        FileToPack(uid="12345", created_at=datetime(2024, 1, 3), size_bytes=5),   # shard 39
        FileToPack(uid="abc123", created_at=datetime(2024, 1, 3), size_bytes=7),  # shard 5C
        FileToPack(uid="12345", created_at=datetime(2024, 1, 4), size_bytes=9),   # different date_dir
    ]
    config = PlannerConfig(max_shard_size_bytes=50, n_bits=8)

    plan = build_pack_plan(files, config)

    keys = [shard.key for shard in plan.shards]
    assert ShardKey("20240103", "39") in keys
    assert ShardKey("20240103", "5C") in keys
    assert ShardKey("20240104", "39") in keys


def test_validation_errors() -> None:
    config = PlannerConfig(max_shard_size_bytes=50, n_bits=8)

    with pytest.raises(ValueError):
        build_pack_plan([FileToPack(uid="x", created_at=datetime(2024, 1, 1), size_bytes=-1)], config)

    bad_config = PlannerConfig(max_shard_size_bytes=0, n_bits=8)
    with pytest.raises(ValueError):
        build_pack_plan([], bad_config)


def test_determinism_same_input_same_plan() -> None:
    files = [
        FileToPack(uid="300", created_at=datetime(2024, 2, 1), size_bytes=30),
        FileToPack(uid="301", created_at=datetime(2024, 2, 1), size_bytes=40),
    ]
    config = PlannerConfig(max_shard_size_bytes=100, n_bits=8)

    plan1 = build_pack_plan(files, config)
    plan2 = build_pack_plan(files, config)

    assert plan1 == plan2


def test_estimate_shard_counts_matches_total_size() -> None:
    files = [
        FileToPack(uid="400", created_at=datetime(2024, 3, 1), size_bytes=60),
        FileToPack(uid="656", created_at=datetime(2024, 3, 1), size_bytes=60),  # same shard modulo
    ]
    config = PlannerConfig(max_shard_size_bytes=100, n_bits=8)

    counts = estimate_shard_counts(files, config)

    key = next(iter(counts.keys()))
    assert counts[key] == 2
