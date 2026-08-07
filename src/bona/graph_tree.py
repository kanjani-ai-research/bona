"""Graph Tree — builds CSP hierarchy nodes from graph_tree.yaml config.

Reads the tree schema and emits structural nodes (Provider, Account, Region)
and CONTAINS/BELONGS_TO edges that anchor resources in a navigable hierarchy.

Usage:
    from bona.graph_tree import build_hierarchy
    hierarchy_nodes, hierarchy_edges = build_hierarchy(
        csp="aws",
        context={"account_id": "705909755305", "region": "us-east-1"},
        resource_nodes=discovered_nodes,
    )
"""

import fnmatch
import logging
from pathlib import Path
from typing import Any, Optional

from .schema.model import AssetNode, AssetEdge

logger = logging.getLogger(__name__)

# Search order for graph_tree.yaml
_SEARCH_PATHS = [
    Path.cwd() / "graph_tree.yaml",
    Path.home() / ".bona" / "graph_tree.yaml",
    Path.cwd() / "frameworks" / "graph_tree.yaml",
    Path(__file__).parent.parent.parent / "frameworks" / "graph_tree.yaml",
]


def _load_tree_config(config_path: Optional[str] = None) -> dict:
    """Load graph_tree.yaml from config path or search locations."""
    if config_path:
        p = Path(config_path)
        if p.exists():
            return _parse_yaml(p.read_text())

    for path in _SEARCH_PATHS:
        if path.exists():
            logger.debug("Loading graph_tree from %s", path)
            return _parse_yaml(path.read_text())

    logger.warning("graph_tree.yaml not found, hierarchy nodes disabled")
    return {}


def _parse_yaml(text: str) -> dict:
    """Parse YAML text."""
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:
        from .config import _parse_yaml as basic_parse
        return basic_parse(text)


def _interpolate(template: str, context: dict) -> str:
    """Replace {variable} placeholders in a template string."""
    result = template
    for key, value in context.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


def build_hierarchy(
    csp: str,
    context: dict,
    resource_nodes: list[AssetNode],
    config_path: Optional[str] = None,
) -> tuple[list[AssetNode], list[AssetEdge]]:
    """Build hierarchy nodes and edges for a CSP.

    Args:
        csp: cloud provider key ("aws", "gcp", "azure")
        context: variables for template interpolation (account_id, region, project_id, etc.)
        resource_nodes: discovered resource nodes to attach to hierarchy
        config_path: optional explicit path to graph_tree.yaml

    Returns:
        (hierarchy_nodes, hierarchy_edges) — includes resource attachment edges
    """
    config = _load_tree_config(config_path)

    settings = config.get("settings", {})
    if not settings.get("enabled", True):
        return [], []

    hierarchies = config.get("hierarchies", {})
    csp_config = hierarchies.get(csp)
    if not csp_config:
        logger.debug("No hierarchy defined for CSP '%s'", csp)
        return [], []

    nodes: list[AssetNode] = []
    edges: list[AssetEdge] = []

    # Emit root node if configured
    root_node_id = None
    if settings.get("emit_root", False):
        root_cfg = settings.get("root_node", {})
        root_node_id = root_cfg.get("id", "cloud::root")
        root_node = AssetNode(
            id=root_node_id,
            node_type=root_cfg.get("type", "Cloud::Root"),
            name=root_cfg.get("name", "Cloud Infrastructure"),
            provider="multi",
            properties={"synthetic": True},
        )
        nodes.append(root_node)

    # Emit hierarchy layers
    layers = csp_config.get("layers", [])
    prev_node_id = None

    layer_nodes: dict[str, AssetNode] = {}  # type → node

    for layer in layers:
        node_type = layer.get("type", "")
        id_template = layer.get("id_template", "")
        name_template = layer.get("name_template", "")
        static_name = layer.get("name", "")
        edge_to_parent = layer.get("edge_to_parent")
        properties = layer.get("properties", {})

        node_id = _interpolate(id_template, context)
        node_name = static_name or _interpolate(name_template, context)

        node = AssetNode(
            id=node_id,
            node_type=node_type,
            name=node_name,
            provider=csp,
            account_id=context.get("account_id", context.get("project_id", "")),
            region=context.get("region", "global"),
            properties={**properties, "synthetic": True},
        )
        nodes.append(node)
        layer_nodes[node_type] = node

        # Edge to parent layer
        if edge_to_parent and prev_node_id:
            edges.append(AssetEdge(
                source_id=prev_node_id,
                target_id=node_id,
                edge_type=edge_to_parent,
            ))
        elif prev_node_id is None and root_node_id:
            # First layer connects to root via root's edge_to_children
            root_edge_type = settings.get("root_node", {}).get("edge_to_children", "HAS_PROVIDER")
            edges.append(AssetEdge(
                source_id=root_node_id,
                target_id=node_id,
                edge_type=root_edge_type,
            ))

        prev_node_id = node_id

    # Attach resources to hierarchy
    resource_edge_type = csp_config.get("resource_edge", "BELONGS_TO")
    attach_to_type = csp_config.get("resource_attach_to", "Cloud::Region")
    global_patterns = settings.get("global_resource_patterns", [])

    attach_node = layer_nodes.get(attach_to_type)
    # For global resources, attach to Account/Project (one level up from Region)
    account_types = ("Cloud::Account", "Cloud::Project", "Cloud::Subscription")
    global_attach_node = None
    for at in account_types:
        if at in layer_nodes:
            global_attach_node = layer_nodes[at]
            break

    if attach_node:
        for resource in resource_nodes:
            # Check if global resource
            is_global = any(fnmatch.fnmatch(resource.node_type, pat) for pat in global_patterns)
            target = global_attach_node if (is_global and global_attach_node) else attach_node

            edges.append(AssetEdge(
                source_id=resource.id,
                target_id=target.id,
                edge_type=resource_edge_type,
            ))

    logger.info("Hierarchy built for %s: %d nodes, %d edges (attached %d resources)",
                csp, len(nodes), len(edges), len(resource_nodes))

    return nodes, edges
