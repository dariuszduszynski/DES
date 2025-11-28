from datetime import datetime

import pytest

from des_core import (
    compute_shard_index_from_uid,
    format_date_dir,
    locate_shard,
    normalize_uid,
    shard_index_to_hex,
)


def test_normalize_uid_handles_int_and_str() -> None:
    assert normalize_uid(12345) == "12345"
    assert normalize_uid("abc-123") == "abc-123"


def test_format_date_dir_formats_as_yyyymmdd() -> None:
    assert format_date_dir(datetime(2024, 1, 2, 3, 4, 5)) == "20240102"
    assert format_date_dir(datetime(2023, 12, 31, 23, 59, 59)) == "20231231"


def test_compute_shard_index_numeric_uid_modulo() -> None:
    assert compute_shard_index_from_uid("12345", n_bits=8) == 57


def test_compute_shard_index_string_uid_crc32() -> None:
    assert compute_shard_index_from_uid("abc123", n_bits=8) == 92


def test_compute_shard_index_validates_n_bits_range() -> None:
    with pytest.raises(ValueError):
        compute_shard_index_from_uid("abc", n_bits=3)
    with pytest.raises(ValueError):
        compute_shard_index_from_uid("abc", n_bits=24)


def test_shard_index_to_hex_formats_and_validates() -> None:
    assert shard_index_to_hex(0, n_bits=8) == "00"
    assert shard_index_to_hex(255, n_bits=8) == "FF"
    assert shard_index_to_hex(4095, n_bits=12) == "FFF"

    with pytest.raises(ValueError):
        shard_index_to_hex(256, n_bits=8)


def test_locate_shard_end_to_end_numeric_uid() -> None:
    created = datetime(2024, 5, 17, 10, 0, 0)
    location = locate_shard(12345, created_at=created, n_bits=8)

    assert location.uid == "12345"
    assert location.date_dir == "20240517"
    assert location.shard_hex == "39"
    assert location.object_key == "20240517/39.des"


def test_locate_shard_end_to_end_string_uid() -> None:
    created = datetime(2023, 1, 1, 0, 0, 0)
    location = locate_shard("abc123", created_at=created, n_bits=8)

    assert location.uid == "abc123"
    assert location.date_dir == "20230101"
    assert location.shard_hex == "5C"
    assert location.object_key == "20230101/5C.des"
