"""
Diskcache Persistent State Store for Poly-Harvester.
Caches runtime risk parameters, control states, and real-time telemetry snapshots across restarts.
"""
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("PolyHarvesterCache")

try:
    import diskcache
    HAVE_DISKCACHE = True
except ImportError:
    HAVE_DISKCACHE = False
    logger.warning("diskcache module not found; falling back to in-memory cache.")


class StateCache:
    """
    Persistent key-value cache using diskcache for fast, atomic state preservation.
    """

    def __init__(self, cache_dir: str = "data/cache"):
        self.cache_dir = cache_dir
        os.makedirs(os.path.abspath(self.cache_dir), exist_ok=True)
        self._memory_fallback: Dict[str, Any] = {}

        if HAVE_DISKCACHE:
            try:
                self.cache = diskcache.Cache(self.cache_dir)
                logger.info(f"Initialized Diskcache persistent cache at {self.cache_dir}")
            except Exception as e:
                logger.error(f"Failed to initialize Diskcache: {e}. Using fallback.")
                self.cache = None
        else:
            self.cache = None

    def set(self, key: str, value: Any, expire: Optional[float] = None):
        """Sets a key-value pair in persistent cache."""
        if self.cache is not None:
            try:
                self.cache.set(key, value, expire=expire)
                return
            except Exception as e:
                logger.error(f"Error setting cache key '{key}': {e}")
        self._memory_fallback[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieves a cached value by key."""
        if self.cache is not None:
            try:
                val = self.cache.get(key, default=default)
                if val is not None:
                    return val
            except Exception as e:
                logger.error(f"Error getting cache key '{key}': {e}")
        return self._memory_fallback.get(key, default)

    def delete(self, key: str):
        """Removes a key from cache."""
        if self.cache is not None:
            try:
                self.cache.delete(key)
            except Exception:
                pass
        self._memory_fallback.pop(key, None)

    # High-level helper methods
    def set_runtime_config(self, config_dict: dict):
        """Persists dynamic runtime risk limits."""
        self.set("runtime_config", config_dict)

    def get_runtime_config(self) -> Optional[dict]:
        """Loads persisted runtime risk limits."""
        return self.get("runtime_config", None)

    def set_bot_status(self, is_paused: bool, is_stop_loss: bool, reason: str = ""):
        """Persists operational flags (pause, circuit breaker)."""
        payload = {
            "is_paused": is_paused,
            "is_stop_loss_triggered": is_stop_loss,
            "reason": reason,
        }
        self.set("bot_status", payload)

    def get_bot_status(self) -> dict:
        """Retrieves operational flags."""
        return self.get(
            "bot_status",
            {"is_paused": False, "is_stop_loss_triggered": False, "reason": ""},
        )

    def set_telemetry(self, telemetry_payload: dict):
        """Caches the latest telemetry frame for instant UI hydration."""
        self.set("latest_telemetry", telemetry_payload, expire=300)

    def get_telemetry(self) -> Optional[dict]:
        """Retrieves the last known telemetry frame."""
        return self.get("latest_telemetry", None)

    def close(self):
        """Safely closes diskcache connection."""
        if self.cache is not None:
            try:
                self.cache.close()
            except Exception:
                pass
