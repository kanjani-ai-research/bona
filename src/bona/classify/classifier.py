"""Domain classifier — assigns resources to functional domains.

Classification is rule-based, driven by a YAML configuration. Each domain
declares matching rules (tags, name patterns, resource types). A resource
matches a domain if ANY rule in that domain matches. Resources may belong
to multiple domains.

Priority order (highest wins for tie-breaking display, but all matches kept):
  1. Tag match — explicit operator intent
  2. Name pattern match — conventional naming
  3. Resource type match — structural affinity
"""

import fnmatch
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from ..schema.model import AssetNode, DiscoveryResult
from .assignments import DomainAssignments

logger = logging.getLogger(__name__)


@dataclass
class DomainRule:
    """Classification rules for a single domain."""

    name: str
    description: str = ""
    tags: dict[str, list[str]] = field(default_factory=dict)
    name_patterns: list[str] = field(default_factory=list)
    resource_types: list[str] = field(default_factory=list)

    def matches(self, node: AssetNode) -> bool:
        """Return True if the node matches any rule in this domain."""
        if self._matches_tags(node):
            return True
        if self._matches_name(node):
            return True
        if self._matches_resource_type(node):
            return True
        return False

    def _matches_tags(self, node: AssetNode) -> bool:
        """Check if node tags match any declared tag rule."""
        if not self.tags:
            return False
        node_tags = node.tags or node.properties.get("tags", {})
        if not node_tags:
            return False
        for tag_key, tag_values in self.tags.items():
            node_value = node_tags.get(tag_key, "").lower()
            if node_value and any(v.lower() == node_value for v in tag_values):
                return True
        return False

    def _matches_name(self, node: AssetNode) -> bool:
        """Check if node name or ID matches any name pattern."""
        if not self.name_patterns:
            return False
        targets = [
            (node.name or "").lower(),
            (node.id or "").lower(),
            node.properties.get("resource_name", "").lower(),
        ]
        for pattern in self.name_patterns:
            pat = pattern.lower()
            for target in targets:
                if target and fnmatch.fnmatch(target, pat):
                    return True
        return False

    def _matches_resource_type(self, node: AssetNode) -> bool:
        """Check if node resource_type matches any declared type pattern."""
        if not self.resource_types:
            return False
        rt = (node.resource_type or "").strip()
        if not rt:
            return False
        for pattern in self.resource_types:
            if fnmatch.fnmatch(rt, pattern):
                return True
        return False


@dataclass
class DomainConfig:
    """Parsed domain classification configuration."""

    domains: list[DomainRule] = field(default_factory=list)
    unclassified_domain: str = "shared"
    include_shared_in_all: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "DomainConfig":
        """Parse configuration from a dictionary."""
        domains_data = data.get("domains", {})
        defaults = data.get("defaults", {})

        rules = []
        for name, spec in domains_data.items():
            if spec is None:
                spec = {}
            rules.append(DomainRule(
                name=name,
                description=spec.get("description", ""),
                tags=spec.get("tags", {}),
                name_patterns=spec.get("name_patterns", []),
                resource_types=spec.get("resource_types", []),
            ))

        return cls(
            domains=rules,
            unclassified_domain=defaults.get("unclassified_domain", "shared"),
            include_shared_in_all=defaults.get("include_shared_in_all", True),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "DomainConfig":
        """Load configuration from a YAML file."""
        path = Path(path)
        with path.open() as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data)


class DomainClassifier:
    """Classifies discovered resources into functional domains.

    Usage:
        classifier = DomainClassifier.from_file("bona-domains.yaml")
        assignments = classifier.classify(discovery_result)
        # assignments["identity"] → list of AssetNodes
        # assignments["shared"] → infrastructure nodes
    """

    def __init__(self, config: DomainConfig):
        self._config = config
        self._rules_by_name = {r.name: r for r in config.domains}

    @classmethod
    def from_file(cls, path: str | Path) -> "DomainClassifier":
        """Create a classifier from a YAML config file."""
        config = DomainConfig.from_file(path)
        return cls(config)

    @classmethod
    def from_dict(cls, data: dict) -> "DomainClassifier":
        """Create a classifier from a config dictionary."""
        config = DomainConfig.from_dict(data)
        return cls(config)

    @property
    def domain_names(self) -> list[str]:
        """Return the list of configured domain names."""
        return [r.name for r in self._config.domains]

    def classify(self, result: DiscoveryResult) -> DomainAssignments:
        """Classify all resources in a discovery result into domains.

        Each resource is tested against every domain rule. If it matches
        none, it goes to the unclassified domain (default: 'shared').

        Returns a DomainAssignments object with per-domain node lists
        and edge routing.
        """
        assignments = DomainAssignments(
            config=self._config,
            all_nodes=result.resources,
            all_edges=result.relationships,
        )

        # Classify each node
        for node in result.resources:
            matched_domains = self._match_node(node)
            if matched_domains:
                for domain in matched_domains:
                    assignments.assign(node, domain)
            else:
                assignments.assign(node, self._config.unclassified_domain)

        # Route edges to domains
        assignments.route_edges()

        logger.info(
            "Classification complete: %d resources → %d domains (%d shared)",
            len(result.resources),
            len(assignments.domains_with_nodes),
            len(assignments.get_nodes(self._config.unclassified_domain)),
        )

        return assignments

    def classify_node(self, node: AssetNode) -> list[str]:
        """Classify a single node, returning matching domain names."""
        matched = self._match_node(node)
        return matched if matched else [self._config.unclassified_domain]

    def _match_node(self, node: AssetNode) -> list[str]:
        """Test a node against all domain rules, return matching domain names."""
        matches = []
        for rule in self._config.domains:
            if rule.matches(node):
                matches.append(rule.name)
        return matches
