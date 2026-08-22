"""Domain assignments — the result of classification.

Tracks which nodes belong to which domains and routes edges accordingly,
ensuring edge completeness (no dangling targets within a domain).
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..schema.model import AssetEdge, AssetNode

if TYPE_CHECKING:
    from .classifier import DomainConfig

logger = logging.getLogger(__name__)


@dataclass
class DomainAssignments:
    """Per-domain node and edge assignments with completeness guarantees.

    After classification, call `route_edges()` to ensure every edge's
    endpoints are resolvable within their assigned domain.
    """

    config: "DomainConfig"
    all_nodes: list[AssetNode] = field(default_factory=list)
    all_edges: list[AssetEdge] = field(default_factory=list)

    # Internal state
    _node_to_domains: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    _domain_nodes: dict[str, list[AssetNode]] = field(default_factory=lambda: defaultdict(list))
    _domain_edges: dict[str, list[AssetEdge]] = field(default_factory=lambda: defaultdict(list))
    _node_index: dict[str, AssetNode] = field(default_factory=dict)

    def __post_init__(self):
        """Build the node lookup index."""
        self._node_index = {n.id: n for n in self.all_nodes}

    def assign(self, node: AssetNode, domain: str) -> None:
        """Assign a node to a domain."""
        if domain not in self._node_to_domains[node.id]:
            self._node_to_domains[node.id].add(domain)
            self._domain_nodes[domain].append(node)

    def route_edges(self) -> None:
        """Route edges to domains, ensuring completeness.

        An edge belongs to domain X if:
          - Its source is in domain X, AND
          - Its target is in domain X or in the shared domain

        If a target is referenced but not classified, it is pulled into
        the shared domain to maintain completeness.
        """
        shared = self.config.unclassified_domain

        # First pass: identify referenced nodes not yet classified
        all_classified = set(self._node_to_domains.keys())
        for edge in self.all_edges:
            for endpoint in (edge.source_id, edge.target_id):
                if endpoint not in all_classified and endpoint in self._node_index:
                    # Pull into shared
                    node = self._node_index[endpoint]
                    self.assign(node, shared)
                    logger.debug("Pulled %s into shared (edge completeness)", endpoint)

        # Second pass: route edges to domains
        for edge in self.all_edges:
            source_domains = self._node_to_domains.get(edge.source_id, set())
            target_domains = self._node_to_domains.get(edge.target_id, set())

            for domain in source_domains:
                # Edge belongs to this domain if target is also in this domain
                # or target is in shared (and include_shared_in_all is set)
                if domain in target_domains:
                    self._domain_edges[domain].append(edge)
                elif shared in target_domains and self.config.include_shared_in_all:
                    self._domain_edges[domain].append(edge)

            # Also add to shared if both endpoints are shared
            if shared in source_domains and shared in target_domains:
                if edge not in self._domain_edges[shared]:
                    self._domain_edges[shared].append(edge)

    def get_nodes(self, domain: str) -> list[AssetNode]:
        """Return nodes assigned to a domain."""
        return self._domain_nodes.get(domain, [])

    def get_edges(self, domain: str) -> list[AssetEdge]:
        """Return edges routed to a domain."""
        return self._domain_edges.get(domain, [])

    @property
    def domains_with_nodes(self) -> list[str]:
        """Return domain names that have at least one node."""
        return [d for d, nodes in self._domain_nodes.items() if nodes]

    @property
    def stats(self) -> dict[str, dict[str, int]]:
        """Return per-domain statistics."""
        return {
            domain: {
                "nodes": len(self._domain_nodes.get(domain, [])),
                "edges": len(self._domain_edges.get(domain, [])),
            }
            for domain in self.domains_with_nodes
        }

    def dangling_edges(self, domain: str) -> list[AssetEdge]:
        """Return edges in a domain whose targets have no node in that domain.

        Should be empty after `route_edges()` if the source tree is complete.
        """
        domain_node_ids = {n.id for n in self.get_nodes(domain)}
        shared_node_ids = {n.id for n in self.get_nodes(self.config.unclassified_domain)}
        valid_ids = domain_node_ids | shared_node_ids

        dangling = []
        for edge in self.get_edges(domain):
            if edge.target_id not in valid_ids:
                dangling.append(edge)
        return dangling
