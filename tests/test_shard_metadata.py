from datetime import datetime, timezone

from des_core.shard_metadata import ShardMetadata


def test_metadata_serialization() -> None:
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    updated = datetime(2024, 1, 2, tzinfo=timezone.utc)
    key = ShardMetadata.build_key("uid-1", created)
    meta = ShardMetadata(
        version=1,
        shard_file="20240101_39_0000.des",
        shard_size=123,
        created_at=created,
        last_updated=updated,
        index={key: {"uid": "uid-1", "offset": 0, "length": 10, "codec": "none", "compressed_size": 10}},
        tombstones={},
        stats={"entries": 1},
    )

    payload = meta.to_json()
    restored = ShardMetadata.from_json(payload)

    assert restored.version == meta.version
    assert restored.shard_file == meta.shard_file
    assert restored.shard_size == meta.shard_size
    assert restored.created_at == meta.created_at
    assert restored.last_updated == meta.last_updated
    assert restored.index == meta.index
    assert restored.tombstones == meta.tombstones
    assert restored.stats == meta.stats


def test_is_tombstoned() -> None:
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    key = ShardMetadata.build_key("uid-1", created)
    meta = ShardMetadata(
        version=1,
        shard_file="shard.des",
        shard_size=10,
        created_at=created,
        last_updated=created,
        index={},
        tombstones={key: {"uid": "uid-1"}},
    )

    assert meta.is_tombstoned("uid-1", created) is True
    assert meta.is_tombstoned("uid-2", created) is False


def test_get_entry() -> None:
    created = datetime(2024, 1, 1, tzinfo=timezone.utc)
    key = ShardMetadata.build_key("uid-1", created)
    entry = {"uid": "uid-1", "offset": 0}
    meta = ShardMetadata(
        version=1,
        shard_file="shard.des",
        shard_size=10,
        created_at=created,
        last_updated=created,
        index={key: entry},
        tombstones={},
    )

    assert meta.get_entry("uid-1", created) == entry

    fallback_meta = ShardMetadata(
        version=1,
        shard_file="shard.des",
        shard_size=10,
        created_at=created,
        last_updated=created,
        index={"uid-2": {"uid": "uid-2", "offset": 5}},
        tombstones={},
    )
    assert fallback_meta.get_entry("uid-2", created) == {"uid": "uid-2", "offset": 5}
