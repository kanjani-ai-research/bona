"""LLM Enrichment — uses Bedrock to enhance learned relationships and descriptions.

Calls Claude via Bedrock to:
1. Classify ambiguous properties as relationships vs attributes
2. Suggest edge types for candidate relationships
3. Generate descriptions for nodes and TypeDefinitions
4. Validate/correct pattern-learned rules

Uses the converse API (bedrock-runtime) for structured responses.

Configuration (bona.yaml):
    llm_enrichment:
      enabled: true
      model_id: us.anthropic.claude-sonnet-4-5-20250929-v1:0
      max_nodes_per_batch: 20
      max_candidates_per_call: 10
"""

import json
import logging
import time
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from ..schema.model import AssetNode, AssetEdge
from ..relationship_learner import RelationshipCandidate, RelationshipRules
from ..prompts import load_prompt, render_prompt
from ..mcp_client import MCPClient, MCPPool

logger = logging.getLogger(__name__)

DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
MAX_NODES_PER_BATCH = 20
MAX_CANDIDATES_PER_CALL = 10
MAX_TOOL_LOOPS = 5


# ==================================================================
# Core LLM call
# ==================================================================


def _call_bedrock(session: boto3.Session, region: str, model_id: str, prompt: str,
                  temperature: float = 0.0, system_prompt: str = "") -> Optional[str]:
    """Call Bedrock converse API and return the text response."""
    try:
        client = session.client("bedrock-runtime", region_name=region)
        kwargs = {
            "modelId": model_id,
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": 4096, "temperature": temperature},
        }
        if system_prompt:
            kwargs["system"] = [{"text": system_prompt}]

        resp = client.converse(**kwargs)
        output = resp.get("output", {}).get("message", {}).get("content", [])
        for block in output:
            if "text" in block:
                return block["text"]
    except ClientError as e:
        logger.warning("Bedrock call failed: %s", e)
    except Exception as e:
        logger.warning("LLM enrichment error: %s", e)
    return None


def _parse_json_response(text: str) -> Optional[dict]:
    """Extract JSON from LLM response (handles markdown code blocks)."""
    if not text:
        return None
    # Strip markdown code fence if present
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0]
    elif "```" in text:
        text = text.split("```")[1].split("```")[0]
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, IndexError):
        return None


def _resolve_prompt(prompt_name: str, variables: dict, fallback_prompt: str,
                    fallback_system: str = "") -> tuple[str, str, str]:
    """Load prompt from YAML or fall back to inline. Returns (user_prompt, system_prompt, prompt_arn)."""
    data = load_prompt(prompt_name)
    if data:
        template = data.get("user_template", "")
        system = data.get("system", fallback_system)
        prompt_arn = data.get("bedrock_prompt_id", "")
        rendered = render_prompt(template, variables) if template else fallback_prompt
        return rendered, system, prompt_arn
    return fallback_prompt, fallback_system, ""


def _call_bedrock_with_tools(session: boto3.Session, region: str, model_id: str,
                             prompt: str, tools: list[dict], mcp_client,
                             temperature: float = 0.0, system_prompt: str = "") -> Optional[str]:
    """Call Bedrock converse with tool use, running an agentic loop."""
    try:
        client = session.client("bedrock-runtime", region_name=region)
        messages = [{"role": "user", "content": [{"text": prompt}]}]
        kwargs = {
            "modelId": model_id,
            "messages": messages,
            "inferenceConfig": {"maxTokens": 4096, "temperature": temperature},
            "toolConfig": {"tools": tools},
        }
        if system_prompt:
            kwargs["system"] = [{"text": system_prompt}]

        for _ in range(MAX_TOOL_LOOPS):
            resp = client.converse(**kwargs)
            stop = resp.get("stopReason", "")
            output_msg = resp.get("output", {}).get("message", {})
            messages.append(output_msg)

            if stop != "tool_use":
                # Final text response
                for block in output_msg.get("content", []):
                    if "text" in block:
                        return block["text"]
                return None

            # Process tool calls
            tool_results = []
            for block in output_msg.get("content", []):
                if "toolUse" in block:
                    tu = block["toolUse"]
                    result = mcp_client.call_tool(tu["name"], tu.get("input", {}))
                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tu["toolUseId"],
                            "content": [{"text": json.dumps(result, default=str)}],
                        }
                    })

            messages.append({"role": "user", "content": tool_results})
            kwargs["messages"] = messages

    except ClientError as e:
        logger.warning("Bedrock tool-use call failed: %s", e)
    except Exception as e:
        logger.warning("LLM tool-use error: %s", e)
    return None


# ==================================================================
# 1. Classify properties as relationships vs attributes
# ==================================================================


def classify_properties(
    session: boto3.Session,
    region: str,
    nodes: list[AssetNode],
    model_id: str = DEFAULT_MODEL_ID,
    max_batch: int = MAX_NODES_PER_BATCH,
    temperature: float = 0.0,
    system_prompt: str = "",
    prompts_config=None,
    mcp_client: Optional[MCPClient] = None,
    mcp_tools: Optional[list[dict]] = None,
) -> list[dict]:
    """Ask LLM to classify node properties as relationships or attributes."""
    # Sample diverse nodes (one per type, up to batch limit)
    seen_types: dict[str, AssetNode] = {}
    for n in nodes:
        if n.node_type not in seen_types and not n.node_type.startswith("AWS::CloudFormation::"):
            seen_types[n.node_type] = n
        if len(seen_types) >= max_batch:
            break

    if not seen_types:
        return []

    # Build prompt with sample nodes
    node_samples = []
    for node_type, node in seen_types.items():
        props = {k: v for k, v in node.properties.items()
                 if isinstance(v, str) and v and not k.startswith("_") and len(str(v)) < 200}
        if props:
            node_samples.append({"node_type": node_type, "properties": dict(list(props.items())[:10])})

    # Resolve prompt (managed or inline fallback)
    fallback_prompt = f"""Analyze these AWS resource node properties and classify each property as either a RELATIONSHIP (references another resource) or ATTRIBUTE (describes this resource).

For relationships, suggest:
- target_type: the AWS resource type being referenced (e.g. "AWS::IAM::Role")
- edge_type: a short UPPER_SNAKE_CASE label (e.g. "ASSUMES_ROLE", "IN_VPC", "USES_MODEL")
- confidence: 0.0-1.0

Nodes:
{json.dumps(node_samples, indent=2)}

Return JSON array:
```json
[
  {{"source_type": "AWS::...", "property_name": "...", "is_relationship": true, "target_type": "AWS::...", "edge_type": "...", "confidence": 0.9}},
  {{"source_type": "AWS::...", "property_name": "...", "is_relationship": false, "reason": "descriptive attribute"}}
]
```

Only include properties that ARE relationships. Skip obvious attributes (names, descriptions, counts, timestamps)."""

    prompt_name = prompts_config.classify.get("local_name", "") if prompts_config else ""
    if prompt_name:
        prompt, sys_prompt, _ = _resolve_prompt(
            prompt_name, {"nodes_json": json.dumps(node_samples, indent=2)},
            fallback_prompt, system_prompt)
        system_prompt = sys_prompt or system_prompt
    else:
        prompt = fallback_prompt

    # Use MCP tool-use loop if available
    if mcp_client and mcp_tools:
        text = _call_bedrock_with_tools(session, region, model_id, prompt, mcp_tools,
                                        mcp_client, temperature, system_prompt)
    else:
        text = _call_bedrock(session, region, model_id, prompt)
    result = _parse_json_response(text)
    if isinstance(result, list):
        logger.info("LLM classified %d property-relationship candidates", len(result))
        return result
    return []


# ==================================================================
# 2. Validate and correct learned candidates
# ==================================================================


def validate_candidates(
    session: boto3.Session,
    region: str,
    rules: RelationshipRules,
    model_id: str = DEFAULT_MODEL_ID,
    max_batch: int = MAX_CANDIDATES_PER_CALL,
    temperature: float = 0.0,
    system_prompt: str = "",
    mcp_client=None,
    mcp_tools: Optional[list[dict]] = None,
) -> RelationshipRules:
    """Ask LLM to validate/correct unpromoted candidates."""
    candidates = rules.candidates()
    if not candidates:
        return rules

    batch = candidates[:max_batch]

    candidate_data = [
        {
            "source_type": c.source_type,
            "property_name": c.property_name,
            "target_type": c.target_type,
            "edge_type": c.edge_type,
            "sample_values": c.sample_values[:3],
            "observed_count": c.observed_count,
        }
        for c in batch
    ]

    prompt = f"""These are candidate relationship rules learned from scanning AWS resource properties. 
Each candidate suggests that a property on a source resource type references a target resource.

Candidates:
{json.dumps(candidate_data, indent=2)}

For each candidate, determine:
1. Is this a real relationship? (true/false)
2. If yes, is the edge_type correct? Suggest a better one if not.
3. If yes, is the target_type correct? Suggest a better one if not.
4. Should it be promoted (auto-create edges)? Consider if a self-reference is useful.

Return JSON array:
```json
[
  {{"source_type": "...", "property_name": "...", "is_valid": true, "edge_type": "CORRECTED_TYPE", "target_type": "AWS::...", "promote": true, "reason": "..."}},
  {{"source_type": "...", "property_name": "...", "is_valid": false, "reason": "self-reference / not a relationship"}}
]
```"""

    if mcp_client and mcp_tools:
        text = _call_bedrock_with_tools(session, region, model_id, prompt, mcp_tools,
                                        mcp_client, temperature, system_prompt)
    else:
        text = _call_bedrock(session, region, model_id, prompt)
    result = _parse_json_response(text)

    if not isinstance(result, list):
        return rules

    # Apply LLM corrections
    corrections = 0
    for item in result:
        key = rules._key(item.get("source_type", ""), item.get("property_name", ""))
        candidate = rules.rules.get(key)
        if not candidate:
            continue

        if not item.get("is_valid", True):
            # LLM says not a relationship — remove it
            del rules.rules[key]
            corrections += 1
            logger.debug("LLM removed candidate: %s.%s (%s)",
                         candidate.source_type, candidate.property_name, item.get("reason", ""))
        else:
            # Apply corrections
            if item.get("edge_type"):
                candidate.edge_type = item["edge_type"]
            if item.get("target_type"):
                candidate.target_type = item["target_type"]
            if item.get("promote") and not candidate.promoted:
                candidate.promoted = True
                corrections += 1
                logger.info("LLM promoted: %s.%s → %s (%s)",
                            candidate.source_type, candidate.property_name,
                            candidate.target_type, candidate.edge_type)

    if corrections:
        logger.info("LLM validated candidates: %d corrections applied", corrections)

    return rules


# ==================================================================
# 3. Generate node descriptions
# ==================================================================


def generate_descriptions(
    session: boto3.Session,
    region: str,
    nodes: list[AssetNode],
    model_id: str = DEFAULT_MODEL_ID,
    max_batch: int = MAX_NODES_PER_BATCH,
    temperature: float = 0.0,
    system_prompt: str = "",
    prompts_config=None,
    mcp_client=None,
    mcp_tools: Optional[list[dict]] = None,
) -> dict[str, str]:
    """Generate short descriptions for nodes that don't have one."""
    needs_description = [
        n for n in nodes
        if not n.properties.get("description") and n.node_type.startswith("AWS::")
    ]

    if not needs_description:
        return {}

    batch = needs_description[:max_batch]
    node_data = [
        {"id": n.id, "node_type": n.node_type, "name": n.name,
         "properties": {k: v for k, v in list(n.properties.items())[:8]
                        if isinstance(v, (str, int, bool)) and not k.startswith("_")}}
        for n in batch
    ]

    prompt = f"""Generate a one-sentence description for each AWS resource based on its type, name, and properties.

Resources:
{json.dumps(node_data, indent=2)}

Return JSON object mapping id → description:
```json
{{
  "arn:aws:...": "A production VPC hosting the main application workloads",
  "bedrock-model:...": "Anthropic's Claude Sonnet 4 model supporting text and image input"
}}
```

Keep descriptions concise (1 sentence, under 100 chars). Focus on purpose/role, not just restating the type."""

    if mcp_client and mcp_tools:
        text = _call_bedrock_with_tools(session, region, model_id, prompt, mcp_tools,
                                        mcp_client, temperature, system_prompt)
    else:
        text = _call_bedrock(session, region, model_id, prompt)
    result = _parse_json_response(text)

    if isinstance(result, dict):
        logger.info("LLM generated %d node descriptions", len(result))
        return result
    return {}


# ==================================================================
# Main entry point
# ==================================================================


def llm_enrich(
    session: boto3.Session,
    region: str,
    nodes: list[AssetNode],
    rules: RelationshipRules,
    model_id: str = DEFAULT_MODEL_ID,
    config: Optional["LLMConfig"] = None,
    prompts_config=None,
    mcp_config=None,
) -> tuple[RelationshipRules, list[AssetEdge], dict[str, str]]:
    """Run full LLM enrichment pass.

    Returns:
        (updated_rules, new_edges_from_classification, descriptions)
    """
    from ..config import LLMConfig, MCPConfig as MCPConfigClass

    if config is None:
        config = LLMConfig()

    if not config.enabled:
        return rules, [], {}

    # Resolve model per task
    classify_model = config.tasks.classify or config.model_id
    validate_model = config.tasks.validate or config.model_id
    describe_model = config.tasks.describe or config.model_id
    llm_region = config.region or region

    # Set up MCP client if configured
    mcp_client = None
    mcp_tools = None
    if mcp_config and mcp_config.enabled:
        pool = MCPPool.from_config(mcp_config)
        raw_tools = pool.list_tools()
        if raw_tools:
            mcp_client = pool
            mcp_tools = [{"toolSpec": t} for t in raw_tools]
            logger.info("MCP enabled with %d tools from %d servers",
                        len(mcp_tools), len(pool._clients))

    logger.info("LLM enrichment starting (classify=%s, describe=%s, region=%s, nodes=%d, candidates=%d)",
                classify_model, describe_model, llm_region, len(nodes), len(rules.candidates()))

    new_edges: list[AssetEdge] = []
    descriptions: dict[str, str] = {}
    calls_made = 0

    # 1. Classify properties on nodes
    if calls_made < config.budget_max_calls:
        classifications = classify_properties(session, llm_region, nodes, classify_model,
                                              max_batch=config.max_nodes_per_batch,
                                              temperature=config.temperature,
                                              system_prompt=config.system_prompt,
                                              prompts_config=prompts_config,
                                              mcp_client=mcp_client,
                                              mcp_tools=mcp_tools)
        calls_made += 1
        for c in classifications:
            if c.get("is_relationship") and c.get("confidence", 0) >= 0.7:
                key = rules._key(c["source_type"], c["property_name"])
                if key not in rules.rules:
                    rules.rules[key] = RelationshipCandidate(
                        source_type=c["source_type"],
                        property_name=c["property_name"],
                        target_type=c.get("target_type", ""),
                        edge_type=c.get("edge_type", "REFERENCES"),
                        pattern="llm-classified",
                        observed_count=0,
                        promoted=c.get("confidence", 0) >= 0.9,
                        first_seen=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    )

    # 2. Validate candidates
    if calls_made < config.budget_max_calls and rules.candidates():
        rules = validate_candidates(session, llm_region, rules, validate_model,
                                    max_batch=config.max_candidates_per_call,
                                    temperature=config.temperature,
                                    system_prompt=config.system_prompt,
                                    mcp_client=mcp_client,
                                    mcp_tools=mcp_tools)
        calls_made += 1

    # 3. Generate descriptions
    if calls_made < config.budget_max_calls:
        descriptions = generate_descriptions(session, llm_region, nodes, describe_model,
                                             max_batch=config.max_nodes_per_batch,
                                             temperature=config.temperature,
                                             system_prompt=config.system_prompt,
                                             prompts_config=prompts_config,
                                             mcp_client=mcp_client,
                                             mcp_tools=mcp_tools)
        calls_made += 1

    logger.info("LLM enrichment complete: %d rules, %d descriptions, %d API calls",
                len(rules.rules), len(descriptions), calls_made)

    return rules, new_edges, descriptions
