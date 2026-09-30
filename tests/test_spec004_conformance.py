"""SPEC-004 (Bona Discovery Contract) conformance — the contract, enforced.

Pins SPEC-004 §2 (provider interface + node/edge MUST fields), §3 (edge completeness: every edge
endpoint resolves to a node; stub nodes with id-pattern-inferred types for unresolved targets). Pure
dataclass + logic; no cloud, no network.
"""
from bona.schema.conformance import (
    UNKNOWN_TYPE, check_conformance, ensure_edge_completeness, infer_node_type, is_conformant,
)
from bona.schema.model import AssetEdge, AssetNode, DiscoveryResult


def _node(nid, nt="AWS::S3::Bucket"):
    return AssetNode(id=nid, node_type=nt, provider="aws")


# ── §2 MUST fields ──────────────────────────────────────────────────────────────────
def test_conformant_result_has_no_violations():
    r = DiscoveryResult(provider="aws",
                        resources=[_node("arn:a"), _node("arn:b")],
                        relationships=[AssetEdge(source_id="arn:a", target_id="arn:b",
                                                 edge_type="IS_ATTACHED_TO")])
    assert check_conformance(r) == [] and is_conformant(r)


def test_node_missing_must_field_is_a_violation():
    r = DiscoveryResult(provider="aws", resources=[AssetNode(id="", node_type="X", provider="aws")])
    v = check_conformance(r)
    assert any("missing MUST field" in x and "resources[0]" in x for x in v)


def test_edge_missing_must_field_is_a_violation():
    r = DiscoveryResult(provider="aws", resources=[_node("arn:a")],
                        relationships=[AssetEdge(source_id="arn:a", target_id="", edge_type="X")])
    assert any("missing MUST field" in x for x in check_conformance(r))


def test_result_without_provider_is_a_violation():
    r = DiscoveryResult(provider="", resources=[], relationships=[])
    assert any("provider is required" in x for x in check_conformance(r))


# ── §3.1 edge completeness (detection) ───────────────────────────────────────────────
def test_dangling_edge_is_flagged():
    r = DiscoveryResult(provider="aws", resources=[_node("arn:a")],
                        relationships=[AssetEdge(source_id="arn:a", target_id="vpc-0abc123",
                                                 edge_type="IS_CONTAINED_IN")])
    v = check_conformance(r)
    assert any("edge completeness" in x and "vpc-0abc123" in x for x in v)
    assert not is_conformant(r)


# ── §3.2/§3.3 stub-node creation + id-pattern inference (enforcement) ─────────────────
def test_ensure_edge_completeness_creates_typed_stub():
    r = DiscoveryResult(provider="aws", resources=[_node("arn:a")],
                        relationships=[AssetEdge(source_id="arn:a", target_id="vpc-0abc123",
                                                 edge_type="IS_CONTAINED_IN")])
    ensure_edge_completeness(r)
    stub = next(n for n in r.resources if n.id == "vpc-0abc123")
    assert stub.node_type == "AWS::EC2::VPC" and stub.state == "inferred"
    # after enforcement the result is conformant (no dangling edges)
    assert is_conformant(r)


def test_ensure_edge_completeness_is_idempotent():
    r = DiscoveryResult(provider="aws", resources=[_node("arn:a")],
                        relationships=[AssetEdge(source_id="arn:a", target_id="sg-1", edge_type="E")])
    ensure_edge_completeness(r)
    n1 = len(r.resources)
    ensure_edge_completeness(r)
    assert len(r.resources) == n1        # a second pass adds nothing


def test_unknown_id_pattern_gets_unknown_type():
    r = DiscoveryResult(provider="aws", resources=[_node("arn:a")],
                        relationships=[AssetEdge(source_id="arn:a", target_id="weird-thing",
                                                 edge_type="E")])
    ensure_edge_completeness(r)
    stub = next(n for n in r.resources if n.id == "weird-thing")
    assert stub.node_type == UNKNOWN_TYPE and stub.id == "weird-thing"   # raw id preserved


def test_id_pattern_table_matches_spec():
    # SPEC-004 §3.3 table
    cases = {"vpc-0a": "AWS::EC2::VPC", "subnet-0a": "AWS::EC2::Subnet", "sg-0a": "AWS::EC2::SecurityGroup",
             "igw-0a": "AWS::EC2::InternetGateway", "nat-0a": "AWS::EC2::NatGateway",
             "rtb-0a": "AWS::EC2::RouteTable", "eni-0a": "AWS::EC2::NetworkInterface",
             "vol-0a": "AWS::EC2::Volume", "i-0a": "AWS::EC2::Instance", "nope": UNKNOWN_TYPE}
    for nid, want in cases.items():
        assert infer_node_type(nid) == want


def test_both_endpoints_stubbed_when_neither_discovered():
    r = DiscoveryResult(provider="aws", resources=[],
                        relationships=[AssetEdge(source_id="i-1", target_id="subnet-2", edge_type="E")])
    ensure_edge_completeness(r)
    ids = {n.id for n in r.resources}
    assert ids == {"i-1", "subnet-2"} and is_conformant(r)
