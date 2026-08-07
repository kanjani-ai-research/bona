"""Workers — parallel execution with rate limiting for bona providers.

Provides:
  - ThreadPoolExecutor for parallel provider execution
  - Rate limiter to respect AWS API throttle limits
  - Max results cap per provider to prevent timeouts
"""

import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_MAX_WORKERS = 4
DEFAULT_RATE_LIMIT = 10  # calls per second
DEFAULT_MAX_FINDINGS = 1000


class RateLimiter:
    """Token bucket rate limiter — thread-safe."""

    def __init__(self, calls_per_second: float = DEFAULT_RATE_LIMIT):
        self._rate = calls_per_second
        self._lock = threading.Lock()
        self._last_call = 0.0

    def wait(self):
        """Block until we can make the next call."""
        with self._lock:
            now = time.time()
            min_interval = 1.0 / self._rate
            elapsed = now - self._last_call
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            self._last_call = time.time()


# Global rate limiter
_rate_limiter = RateLimiter()


def set_rate_limit(calls_per_second: float):
    """Configure global rate limit."""
    global _rate_limiter
    _rate_limiter = RateLimiter(calls_per_second)


def rate_limited_call(fn: Callable, *args, **kwargs) -> Any:
    """Call a function with rate limiting."""
    _rate_limiter.wait()
    return fn(*args, **kwargs)


def run_parallel(tasks: list[tuple[str, Callable, list]], max_workers: int = DEFAULT_MAX_WORKERS) -> dict[str, Any]:
    """Run multiple tasks in parallel using ThreadPoolExecutor.

    Args:
        tasks: list of (name, function, args) tuples
        max_workers: thread pool size

    Returns:
        dict mapping task name → result (or exception)
    """
    results = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for name, fn, args in tasks:
            future = executor.submit(fn, *args)
            futures[future] = name

        for future in as_completed(futures):
            name = futures[future]
            try:
                results[name] = future.result()
            except Exception as e:
                logger.warning("Task %s failed: %s", name, e)
                results[name] = ([], [])  # empty nodes, edges

    return results


def run_providers_parallel(
    providers: list,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> "DiscoveryResult":
    """Run multiple providers in parallel and merge results.

    Args:
        providers: list of AssetProvider instances
        max_workers: thread pool size (default 4)

    Returns:
        Merged DiscoveryResult with all nodes/edges from all providers.
    """
    from .schema.model import DiscoveryResult

    if not providers:
        return DiscoveryResult(resources=[], relationships=[])

    if len(providers) == 1:
        return providers[0].discover()

    tasks = [(p.name or p.__class__.__name__, p.discover, []) for p in providers]
    results_map = run_parallel(tasks, max_workers=max_workers)

    all_nodes = []
    all_edges = []
    total_ms = 0

    for name, result in results_map.items():
        if isinstance(result, DiscoveryResult):
            all_nodes.extend(result.resources)
            all_edges.extend(result.relationships)
            total_ms += getattr(result, "duration_ms", 0)
        elif isinstance(result, tuple) and len(result) == 2:
            all_nodes.extend(result[0])
            all_edges.extend(result[1])

    logger.info("Parallel discovery complete: %d providers, %d nodes, %d edges, %dms",
                len(providers), len(all_nodes), len(all_edges), total_ms)

    return DiscoveryResult(resources=all_nodes, relationships=all_edges, duration_ms=total_ms)
