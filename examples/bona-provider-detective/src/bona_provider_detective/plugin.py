"""Bona Provider: AWS Detective — behavior graphs and investigations.

Discovers:
  - Detective graphs (behavior graphs)
  - Member accounts in a graph
  - Investigations and indicators

Graph structure:
  [DetectiveGraph] --HAS_MEMBER--> [Account]
  [Investigation] --INVESTIGATES--> [Resource]
  [Investigation] --IN_GRAPH--> [DetectiveGraph]
"""

import logging
from typing import Callable

import boto3
from botocore.exceptions import ClientError

from bona.plugins import hookimpl
from bona.schema.model import AssetNode, AssetEdge

logger = logging.getLogger(__name__)


def enrich_detective(session: boto3.Session, region: str, resources: list, refresh: bool = False):
    """Discover Detective behavior graphs, members, and investigations."""
    all_nodes = []
    all_edges = []

    try:
        detective = session.client("detective", region_name=region)
    except Exception:
        return [], []

    # List behavior graphs
    try:
        resp = detective.list_graphs()
        for graph in resp.get("GraphList", []):
            graph_arn = graph.get("Arn", "")
            all_nodes.append(AssetNode(
                id=graph_arn,
                node_type="AWS::Detective::Graph",
                name=graph_arn.split("/")[-1] if "/" in graph_arn else graph_arn,
                provider="aws",
                account_id=graph_arn.split(":")[4] if ":" in graph_arn else "",
                region=region,
                resource_type="AWS::Detective::Graph",
                state="ACTIVE",
                properties={
                    "created_time": str(graph.get("CreatedTime", "")),
                },
            ))

            # List members in this graph
            try:
                members_resp = detective.list_members(GraphArn=graph_arn)
                for member in members_resp.get("MemberDetails", []):
                    account_id = member.get("AccountId", "")
                    all_nodes.append(AssetNode(
                        id=f"detective-member:{graph_arn}:{account_id}",
                        node_type="AWS::Detective::Member",
                        name=account_id,
                        provider="aws",
                        account_id=account_id,
                        region=region,
                        resource_type="AWS::Detective::Member",
                        state=member.get("Status", "ACTIVE"),
                        properties={
                            "email": member.get("EmailAddress", ""),
                            "invited_time": str(member.get("InvitedTime", "")),
                            "volume_usage": member.get("VolumeUsageInBytes", 0),
                        },
                    ))
                    all_edges.append(AssetEdge(
                        source_id=graph_arn,
                        target_id=f"detective-member:{graph_arn}:{account_id}",
                        edge_type="HAS_MEMBER",
                        provider="aws",
                    ))
            except ClientError:
                pass

            # List investigations
            try:
                inv_resp = detective.list_investigations(GraphArn=graph_arn)
                for inv in inv_resp.get("InvestigationDetails", []):
                    inv_id = inv.get("InvestigationId", "")
                    entity_arn = inv.get("EntityArn", "")
                    all_nodes.append(AssetNode(
                        id=f"detective-investigation:{inv_id}",
                        node_type="AWS::Detective::Investigation",
                        name=inv_id,
                        provider="aws",
                        account_id=inv.get("EntityArn", "").split(":")[4] if ":" in inv.get("EntityArn", "") else "",
                        region=region,
                        resource_type="AWS::Detective::Investigation",
                        state=inv.get("State", ""),
                        properties={
                            "severity": inv.get("Severity", ""),
                            "status": inv.get("Status", ""),
                            "entity_type": inv.get("EntityType", ""),
                            "created_time": str(inv.get("CreatedTime", "")),
                        },
                    ))
                    # Investigation → Graph
                    all_edges.append(AssetEdge(
                        source_id=f"detective-investigation:{inv_id}",
                        target_id=graph_arn,
                        edge_type="IN_GRAPH",
                        provider="aws",
                    ))
                    # Investigation → Resource
                    if entity_arn:
                        all_edges.append(AssetEdge(
                            source_id=f"detective-investigation:{inv_id}",
                            target_id=entity_arn,
                            edge_type="INVESTIGATES",
                            provider="aws",
                        ))
            except ClientError:
                pass

    except ClientError as e:
        logger.debug("detective:list_graphs: %s", e)

    if all_nodes:
        logger.info("Detective: %d nodes, %d edges", len(all_nodes), len(all_edges))
    return all_nodes, all_edges


class BonaProviderDetective:
    @hookimpl
    def bona_reference_providers(self) -> dict[str, Callable]:
        return {"detective": enrich_detective}


plugin = BonaProviderDetective()
