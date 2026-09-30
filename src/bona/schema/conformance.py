"""SPEC-004 conformance — the Bona Discovery Contract, enforced (and checkable).

This module makes SPEC-004 (`specs/bona-discovery/SPEC-004.md`) an executable contract over a
:class:`~bona.schema.model.DiscoveryResult`:

* :func:`ensure_edge_completeness` — SPEC-004 §3: after discovery, every edge endpoint MUST resolve
  to a node in ``resources``. When a relationship names a target (or source) that was not explicitly
  discovered, a **stub node** is created (§3.2) with the id from the relationship, a ``node_type``
  inferred from the id pattern (§3.3), and ``state="inferred"``. Returns a result with no dangling
  edges — the invariant SPEC-004 §3.1 requires.
* :func:`check_conformance` — SPEC-004 §2/§3: assert the provider output carries the MUST fields
  (AssetNode id/node_type/provider; AssetEdge source_id/target_id/edge_type; DiscoveryResult
  resources/relationships/provider) and that edge completeness holds. Returns a list of violation
  strings (empty = conformant) — the shape a ``bona doctor`` check or a CI/conformance test uses.

Nothing here re-discovers or re-classifies; it operates purely on a returned ``DiscoveryResult`` so
any provider (aws/azure/gcp/mcp) gets the same guarantee from one place.
"""
from __future__ import annotations

import re
from typing import Optional

from .model import AssetEdge, AssetNode, DiscoveryResult

# SPEC-004 §3.3 — id-pattern → inferred node_type (the spec's table, verbatim).
_ID_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^vpc-[0-9a-f]+$"), "AWS::EC2::VPC"),
    (re.compile(r"^subnet-[0-9a-f]+$"), "AWS::EC2::Subnet"),
    (re.compile(r"^sg-[0-9a-f]+$"), "AWS::EC2::SecurityGroup"),
    (re.compile(r"^igw-[0-9a-f]+$"), "AWS::EC2::InternetGateway"),
    (re.compile(r"^nat-[0-9a-f]+$"), "AWS::EC2::NatGateway"),
    (re.compile(r"^rtb-[0-9a-f]+$"), "AWS::EC2::RouteTable"),
    (re.compile(r"^eni-[0-9a-f]+$"), "AWS::EC2::NetworkInterface"),
    (re.compile(r"^vol-[0-9a-f]+$"), "AWS::EC2::Volume"),
    (re.compile(r"^i-[0-9a-f]+$"), "AWS::EC2::Instance"),
]
# §3.3: "Unrecognized patterns SHOULD use node_type `Unknown` with the raw ID preserved."
UNKNOWN_TYPE = "Unknown"
INFERRED_STATE = "inferred"

# SPEC-004 §2.2 / §2.3 MUST fields.
NODE_MUST = ("id", "node_type", "provider")
EDGE_MUST = ("source_id", "target_id", "edge_type")


def infer_node_type(node_id: str) -> str:
    """The node_type inferred from an id (SPEC-004 §3.3); ``Unknown`` when no pattern matches."""
    for pattern, node_type in _ID_PATTERNS:
        if pattern.match(node_id or ""):
            return node_type
    return UNKNOWN_TYPE


def _stub_node(node_id: str, *, provider: str, account_id: str, region: str) -> AssetNode:
    """A SPEC-004 §3.2 stub node for an edge endpoint that was not explicitly discovered."""
    return AssetNode(
        id=node_id,
        node_type=infer_node_type(node_id),
        provider=provider or "aws",
        account_id=account_id,
        region=region,
        state=INFERRED_STATE,
    )


def ensure_edge_completeness(result: DiscoveryResult) -> DiscoveryResult:
    """SPEC-004 §3: guarantee every edge endpoint resolves to a node in ``resources``.

    For each edge whose ``source_id``/``target_id`` is not among the discovered resources, append a
    stub node (id from the edge, type inferred per §3.3, ``state="inferred"``). Mutates and returns
    ``result`` (a provider calls this before returning; idempotent — a second call adds nothing)."""
    known = {n.id for n in result.resources}
    added: dict[str, AssetNode] = {}
    for edge in result.relationships:
        for endpoint in (edge.source_id, edge.target_id):
            if endpoint and endpoint not in known and endpoint not in added:
                added[endpoint] = _stub_node(
                    endpoint, provider=result.provider,
                    account_id=getattr(edge, "account_id", "") or result.account_id,
                    region=result.region)
    if added:
        result.resources.extend(added.values())
    return result


def _missing_fields(obj: object, fields: tuple) -> list[str]:
    return [f for f in fields if not getattr(obj, f, None)]


def check_conformance(result: DiscoveryResult) -> list[str]:
    """SPEC-004 §2/§3 conformance check. Returns violation strings ([] = conformant).

    Checks: DiscoveryResult carries a ``provider`` + list fields; every AssetNode has the MUST
    fields (id/node_type/provider); every AssetEdge has the MUST fields (source_id/target_id/
    edge_type); and edge completeness holds (every endpoint resolves to a node)."""
    problems: list[str] = []
    if not getattr(result, "provider", ""):
        problems.append("DiscoveryResult.provider is required (SPEC-004 §2.1)")
    if not isinstance(result.resources, list):
        problems.append("DiscoveryResult.resources must be a list (SPEC-004 §2.1)")
    if not isinstance(result.relationships, list):
        problems.append("DiscoveryResult.relationships must be a list (SPEC-004 §2.1)")

    for i, n in enumerate(result.resources):
        miss = _missing_fields(n, NODE_MUST)
        if miss:
            problems.append(f"resources[{i}] (id={getattr(n, 'id', '?')!r}) missing MUST field(s) "
                            f"{miss} (SPEC-004 §2.2)")

    known = {n.id for n in result.resources}
    for i, e in enumerate(result.relationships):
        miss = _missing_fields(e, EDGE_MUST)
        if miss:
            problems.append(f"relationships[{i}] missing MUST field(s) {miss} (SPEC-004 §2.3)")
            continue
        if e.source_id not in known:
            problems.append(f"relationships[{i}].source_id {e.source_id!r} does not resolve to a node "
                            f"(SPEC-004 §3.1 edge completeness)")
        if e.target_id not in known:
            problems.append(f"relationships[{i}].target_id {e.target_id!r} does not resolve to a node "
                            f"(SPEC-004 §3.1 edge completeness)")
    return problems


def is_conformant(result: DiscoveryResult) -> bool:
    """True iff ``result`` satisfies the SPEC-004 contract (no violations)."""
    return not check_conformance(result)
