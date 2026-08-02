"""Discovery Pipeline — orchestrates discover → transform → load → enrich."""

import logging
from typing import Optional

from ..providers.provider_base import AssetProvider
from ..schema.model import DiscoveryResult

logger = logging.getLogger(__name__)


class DiscoveryPipeline:
    """Orchestrates the full discovery pipeline.

    Usage:
        provider = AWSProvider(account_id="123", region="us-east-1")
        pipeline = DiscoveryPipeline(provider)
        result = pipeline.run()
    """

    def __init__(self, provider: AssetProvider, graph_store=None, vector_store=None):
        self._provider = provider
        self._graph_store = graph_store
        self._vector_store = vector_store

    def run(self) -> DiscoveryResult:
        """Execute full pipeline: discover → transform → load."""
        # 1. Discover
        logger.info("Pipeline: discovering assets from %s...", self._provider.__class__.__name__)
        result = self._provider.discover()
        logger.info("Pipeline: %s", result.summary())

        # 2. Transform (normalize — already done by provider)
        # Future: cross-provider normalization here

        # 3. Load to graph (if configured)
        if self._graph_store:
            self._load_to_graph(result)

        # 4. Index for search (if configured)
        if self._vector_store:
            self._index_for_search(result)

        return result

    def run_services_only(self) -> list[dict]:
        """Just enumerate services (for AI recipe generation)."""
        return self._provider.discover_services()

    def run_compliance_only(self) -> DiscoveryResult:
        """Just get compliance state."""
        return self._provider.discover_compliance()

    def _load_to_graph(self, result: DiscoveryResult):
        """Write nodes + edges to Neptune."""
        logger.info("Pipeline: loading %d nodes + %d edges to graph...",
                    result.resource_count, result.relationship_count)
        # Future: use graphrag-helper writer via AI-LENS
        pass

    def _index_for_search(self, result: DiscoveryResult):
        """Index resources for vector/semantic search."""
        logger.info("Pipeline: indexing %d resources for search...", result.resource_count)
        # Future: use lexical-graph vector indexer via AI-LENS
        pass
