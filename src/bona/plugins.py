"""Bona Plugin System — pluggy-based provider discovery.

Allows external packages to register providers without modifying bona core.

Hook specs:
  - bona_reference_providers() → dict of name → provider_fn
  - bona_supplemental_providers() → dict of resource_type → provider_fn
  - bona_relationship_classifiers() → list of classifier functions

External plugins register via entry_points:
  [project.entry-points."bona"]
  my_plugin = "my_package.plugin"

Or programmatically:
  from bona.plugins import get_plugin_manager
  pm = get_plugin_manager()
  pm.register(my_plugin_module)
"""

import logging
from typing import Any, Callable

import pluggy

logger = logging.getLogger(__name__)

# Project name for pluggy markers
PROJECT_NAME = "bona"

hookspec = pluggy.HookspecMarker(PROJECT_NAME)
hookimpl = pluggy.HookimplMarker(PROJECT_NAME)


# ==================================================================
# Hook Specifications — define what plugins can provide
# ==================================================================


class BonaHookSpec:
    """Hook specifications for bona plugins."""

    @hookspec
    def bona_reference_providers(self) -> dict[str, Callable]:
        """Return reference providers to register.

        Each provider: fn(session, region, resources, refresh) → (nodes, edges)

        Example:
            def bona_reference_providers(self):
                return {"datadog_monitors": my_datadog_provider}
        """

    @hookspec
    def bona_supplemental_providers(self) -> dict[str, Callable]:
        """Return supplemental providers keyed by resource type.

        Each provider: fn(session, region, resources, refresh) → (nodes, edges)
        Only runs when that resource type is discovered.

        Example:
            def bona_supplemental_providers(self):
                return {"AWS::ECS::Service": my_ecs_enricher}
        """

    @hookspec
    def bona_relationship_classifiers(self) -> list[Callable]:
        """Return property classifiers for the relationship learner.

        Each classifier: fn(node_type, property_name, value) → (target_type, edge_type) or None

        Example:
            def bona_relationship_classifiers(self):
                return [my_custom_classifier]
        """


# ==================================================================
# Built-in plugin — registers bona's own providers
# ==================================================================


class BonaBuiltinPlugin:
    """Built-in plugin that registers bona's default providers."""

    @hookimpl
    def bona_reference_providers(self) -> dict[str, Callable]:
        from .enrichment.reference_providers import REFERENCE_PROVIDERS
        return REFERENCE_PROVIDERS

    @hookimpl
    def bona_supplemental_providers(self) -> dict[str, Callable]:
        from .enrichment.type_schemas import _SUPPLEMENTAL_PROVIDERS
        return _SUPPLEMENTAL_PROVIDERS


# ==================================================================
# Plugin Manager — singleton
# ==================================================================


_plugin_manager: pluggy.PluginManager | None = None


def get_plugin_manager() -> pluggy.PluginManager:
    """Get or create the global plugin manager.

    On first call:
      1. Creates manager with hook specs
      2. Registers built-in plugin
      3. Loads external plugins via entry_points ("bona" group)
    """
    global _plugin_manager
    if _plugin_manager is not None:
        return _plugin_manager

    pm = pluggy.PluginManager(PROJECT_NAME)
    pm.add_hookspecs(BonaHookSpec)

    # Register built-in providers
    pm.register(BonaBuiltinPlugin(), name="bona-builtin")

    # Load external plugins from entry_points
    num_loaded = pm.load_setuptools_entrypoints(PROJECT_NAME)
    if num_loaded:
        logger.info("Loaded %d external plugin(s) via entry_points", num_loaded)

    _plugin_manager = pm

    # Log registered plugins
    plugins = pm.get_plugins()
    logger.debug("Registered plugins: %s", [pm.get_name(p) or type(p).__name__ for p in plugins])

    return pm


def get_all_reference_providers() -> dict[str, Callable]:
    """Collect reference providers from all registered plugins."""
    pm = get_plugin_manager()
    all_providers = {}

    results = pm.hook.bona_reference_providers()
    for provider_dict in results:
        if isinstance(provider_dict, dict):
            all_providers.update(provider_dict)

    logger.debug("Reference providers from plugins: %s", list(all_providers.keys()))
    return all_providers


def get_all_supplemental_providers() -> dict[str, Callable]:
    """Collect supplemental providers from all registered plugins."""
    pm = get_plugin_manager()
    all_providers = {}

    results = pm.hook.bona_supplemental_providers()
    for provider_dict in results:
        if isinstance(provider_dict, dict):
            all_providers.update(provider_dict)

    logger.debug("Supplemental providers from plugins: %s", list(all_providers.keys()))
    return all_providers


def get_all_relationship_classifiers() -> list[Callable]:
    """Collect relationship classifiers from all registered plugins."""
    pm = get_plugin_manager()
    all_classifiers = []

    results = pm.hook.bona_relationship_classifiers()
    for classifier_list in results:
        if isinstance(classifier_list, list):
            all_classifiers.extend(classifier_list)

    return all_classifiers
