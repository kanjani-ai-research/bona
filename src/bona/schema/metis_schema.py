"""Metis Schema Generation — produces schema_extension packages from discovery results.

Generates a dict matching the Metis schema_extension YAML format with `extend` blocks
declaring each discovered edge_type and its associated node/target types.
"""

from ..schema.model import DiscoveryResult


def generate_schema_package(result: DiscoveryResult, schema_id: str = "bona-discovered") -> dict:
    """Generate a Metis schema_extension package from a DiscoveryResult.

    Collects all unique edge_types from relationships and all unique resource_types
    from nodes, then builds a schema_extension dict with `extend` blocks.

    Args:
        result: A DiscoveryResult containing resources and relationships.
        schema_id: Identifier for the schema extension package.

    Returns:
        A dict matching the Metis schema_extension YAML format:
        {
            "schema_extension": {
                "id": schema_id,
                "extend": [
                    {
                        "anchor": {"node_type": <source_resource_type>},
                        "relationships": [
                            {
                                "edge_type": <edge_type>,
                                "target_type": <target_resource_type>,
                                "properties": {}
                            }
                        ]
                    },
                    ...
                ]
            }
        }
    """
    # Build a mapping: source_resource_type → set of (edge_type, target_resource_type)
    # First, create a lookup from node id → resource_type
    node_type_map: dict[str, str] = {}
    for node in result.resources:
        node_type_map[node.id] = node.resource_type

    # Collect relationships grouped by source node_type
    # anchor_type → set of (edge_type, target_type)
    extends: dict[str, set[tuple[str, str]]] = {}

    for edge in result.relationships:
        source_type = node_type_map.get(edge.source_id, "")
        target_type = node_type_map.get(edge.target_id, "")

        if not source_type:
            continue

        if source_type not in extends:
            extends[source_type] = set()

        extends[source_type].add((edge.edge_type, target_type or "AWS::Unknown::Resource"))

    # Build the schema_extension structure
    extend_blocks = []
    for anchor_type in sorted(extends.keys()):
        relationships = []
        for edge_type, target_type in sorted(extends[anchor_type]):
            relationships.append({
                "edge_type": edge_type,
                "target_type": target_type,
                "properties": {},
            })

        extend_blocks.append({
            "anchor": {"node_type": anchor_type},
            "relationships": relationships,
        })

    return {
        "schema_extension": {
            "id": schema_id,
            "extend": extend_blocks,
        }
    }
