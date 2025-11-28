"""Simple in-memory cache utilities."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import Generic, Hashable, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class Cache(Generic[K, V]):
    """Minimal cache interface used by DES retrievers."""

    def get(self, key: K) -> V | None:  # pragma: no cover - interface
        raise NotImplementedError

    def set(self, key: K, value: V) -> None:  # pragma: no cover - interface
        raise NotImplementedError


@dataclass
class LRUCacheConfig:
    max_size: int = 1024


class LRUCache(Cache[K, V]):
    """Thread-safe in-memory LRU cache with a fixed max size."""

    def __init__(self, config: LRUCacheConfig | None = None) -> None:
        cfg = config or LRUCacheConfig()
        self._max_size = cfg.max_size
        self._store: OrderedDict[K, V] = OrderedDict()
        self._lock = RLock()

    def get(self, key: K) -> V | None:
        with self._lock:
            try:
                value = self._store.pop(key)
            except KeyError:
                return None
            self._store[key] = value
            return value

    def set(self, key: K, value: V) -> None:
        with self._lock:
            if key in self._store:
                self._store.pop(key)
            self._store[key] = value
            if len(self._store) > self._max_size:
                self._store.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
