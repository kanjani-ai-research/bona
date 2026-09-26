"""Query model and scan engine.

A Query has:
  - name: unique identifier (slug)
  - score: severity 1-10
  - title: human-readable one-liner
  - description: detailed explanation
  - tags: categorization (security, compliance, cost, best-practice)
  - traversal: function that takes nodes+edges, returns findings
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..schema.model import AssetNode, AssetEdge

logger = logging.getLogger(__name__)


@dataclass
class Finding:
    """A single finding from a query match."""

    query_name: str
    score: float
    title: str
    resource_id: str
    resource_type: str
    resource_name: str
    region: str = ""
    account_id: str = ""
    detail: str = ""
    remediation: str = ""

    def to_dict(self) -> dict:
        return {
            "query_name": self.query_name,
            "score": self.score,
            "title": self.title,
            "resource_id": self.resource_id,
            "resource_type": self.resource_type,
            "resource_name": self.resource_name,
            "region": self.region,
            "account_id": self.account_id,
            "detail": self.detail,
            "remediation": self.remediation,
        }


@dataclass
class Query:
    """A scan query — traverses the graph to find issues."""

    name: str
    score: float
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    remediation: str = ""
    traversal: Callable[[list[AssetNode], list[AssetEdge]], list[Finding]] = field(default=None)

    def run(self, nodes: list[AssetNode], edges: list[AssetEdge]) -> list[Finding]:
        """Execute the query against the graph."""
        if self.traversal is None:
            return []
        try:
            return self.traversal(nodes, edges)
        except Exception as e:
            logger.warning("Query %s failed: %s", self.name, e)
            return []


@dataclass
class ScanResult:
    """Results from running a scan."""

    findings: list[Finding] = field(default_factory=list)
    queries_run: int = 0
    queries_matched: int = 0
    duration_ms: int = 0

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.score >= 9)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if 7 <= f.score < 9)

    def summary(self) -> str:
        return (
            f"{self.finding_count} findings "
            f"({self.critical_count} critical, {self.high_count} high) "
            f"from {self.queries_matched}/{self.queries_run} queries "
            f"in {self.duration_ms}ms"
        )


def run_scan(
    nodes: list[AssetNode],
    edges: list[AssetEdge],
    queries: list[Query],
    tags: list[str] | None = None,
    names: list[str] | None = None,
    min_score: float = 0,
) -> ScanResult:
    """Run queries against the graph.

    Args:
        nodes: all discovered nodes
        edges: all discovered edges
        queries: query database to run
        tags: filter queries by tag (None = all)
        names: filter queries by name (None = all)
        min_score: minimum score threshold for results
    """
    t0 = time.time()

    # Filter queries
    filtered = queries
    if tags:
        filtered = [q for q in filtered if any(t in q.tags for t in tags)]
    if names:
        filtered = [q for q in filtered if q.name in names]

    findings: list[Finding] = []
    queries_matched = 0

    for query in filtered:
        results = query.run(nodes, edges)
        if results:
            queries_matched += 1
            for f in results:
                if f.score >= min_score:
                    findings.append(f)

    duration = int((time.time() - t0) * 1000)

    # Sort by score descending
    findings.sort(key=lambda f: f.score, reverse=True)

    result = ScanResult(
        findings=findings,
        queries_run=len(filtered),
        queries_matched=queries_matched,
        duration_ms=duration,
    )

    logger.info("Scan complete: %s", result.summary())
    return result
