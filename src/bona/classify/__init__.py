"""Domain classification for discovered infrastructure assets.

Assigns resources to functional domains based on configurable rules:
tags, naming conventions, resource type affinity, and network boundaries.
"""

from .classifier import DomainClassifier, DomainConfig, DomainRule
from .assignments import DomainAssignments

__all__ = [
    "DomainClassifier",
    "DomainConfig",
    "DomainRule",
    "DomainAssignments",
]
