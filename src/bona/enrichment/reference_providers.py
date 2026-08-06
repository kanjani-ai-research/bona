"""Reference Providers — discover available capabilities (not owned assets).

Reference providers emit nodes for things that *exist in the service catalog*
rather than things you've created. Your assets link to these via edges.

Example:
  [KnowledgeBase "my-kb"] --USES_MODEL--> [BedrockModel "anthropic.claude-3-sonnet"]
  [EC2 Instance "web"]    --RUNS_ON--->   [InstanceSpec "t3.medium"]

Providers:
  - bedrock_models: Foundation models available in the region
  - bedrock_profiles: Inference profiles (cross-region routing)
"""

import logging
from typing import Callable

import boto3
from botocore.exceptions import ClientError

from ..cache import read_cache, write_cache
from ..schema.model import AssetNode, AssetEdge

logger = logging.getLogger(__name__)


# ==================================================================
# Bedrock Foundation Models
# ==================================================================


def _fetch_bedrock_models(session: boto3.Session, region: str) -> dict:
    """Hard inquiry: list all foundation models available in the region."""
    bedrock = session.client("bedrock", region_name=region)

    logger.info("Fetching Bedrock foundation models (hard inquiry)...")
    try:
        resp = bedrock.list_foundation_models()
    except ClientError as e:
        logger.warning("Failed to list Bedrock models: %s", e)
        return {}

    models = {}
    for m in resp.get("modelSummaries", []):
        model_id = m.get("modelId", "")
        if not model_id:
            continue

        # Detect Mantle availability: models with ON_DEMAND inference are accessible
        # via the Bedrock Mantle OpenAI-compatible surface
        inference_types = m.get("inferenceTypesSupported", [])
        is_mantle = "ON_DEMAND" in inference_types
        mantle_qualified_id = f"openai.{model_id}" if is_mantle and not model_id.startswith("openai.") else model_id

        models[model_id] = {
            "model_id": model_id,
            "model_arn": m.get("modelArn", ""),
            "model_name": m.get("modelName", ""),
            "provider_name": m.get("providerName", ""),
            "input_modalities": m.get("inputModalities", []),
            "output_modalities": m.get("outputModalities", []),
            "streaming_supported": m.get("responseStreamingSupported", False),
            "customizations_supported": m.get("customizationsSupported", []),
            "inference_types": inference_types,
            "lifecycle_status": m.get("modelLifecycle", {}).get("status", ""),
            "mantle_available": is_mantle,
            "mantle_qualified_id": mantle_qualified_id,
            "mantle_regions": [region] if is_mantle else [],
        }

    # Check Mantle availability across key regions
    mantle_regions = _discover_mantle_regions(session, models)
    for model_id, regions in mantle_regions.items():
        if model_id in models:
            models[model_id]["mantle_regions"] = regions

    logger.info("Fetched %d Bedrock foundation models (%d Mantle-compatible)",
                len(models), sum(1 for m in models.values() if m.get("mantle_available")))
    return models


def _discover_mantle_regions(session: boto3.Session, models: dict) -> dict[str, list[str]]:
    """Check which regions have Mantle-compatible models available.

    Queries key Bedrock regions to build a region availability map
    for openai.-prefixed (Mantle) models.
    """
    # Key regions where Mantle models are deployed
    MANTLE_REGIONS = ["us-east-1", "us-east-2", "us-west-2", "eu-west-1"]

    mantle_model_ids = {mid for mid, m in models.items() if m.get("mantle_available")}
    if not mantle_model_ids:
        return {}

    region_map: dict[str, list[str]] = {mid: [] for mid in mantle_model_ids}

    for check_region in MANTLE_REGIONS:
        try:
            client = session.client("bedrock", region_name=check_region)
            resp = client.list_foundation_models()
            for m in resp.get("modelSummaries", []):
                mid = m.get("modelId", "")
                if mid in region_map and check_region not in region_map[mid]:
                    region_map[mid].append(check_region)
        except (ClientError, Exception) as e:
            logger.debug("Mantle region check failed for %s: %s", check_region, e)

    return region_map


def _get_bedrock_models(session: boto3.Session, region: str, refresh: bool = False) -> dict:
    """Get Bedrock models — soft (cache) or hard."""
    cache_name = "bedrock-models"

    if not refresh:
        cached = read_cache(cache_name, region)
        if cached:
            return {k: v for k, v in cached.items() if k != "_metadata"}

    models = _fetch_bedrock_models(session, region)
    if models:
        write_cache(cache_name, region, models)
    return models


# ==================================================================
# Bedrock Agents, Prompts, KBs (bedrock-agent service)
# ==================================================================


def enrich_bedrock_agents(
    session: boto3.Session,
    region: str,
    resources: list[AssetNode],
    refresh: bool = False,
) -> tuple[list[AssetNode], list[AssetEdge]]:
    """Discover Bedrock Agents, Prompts and their relationships.

    Nodes:
      - BedrockAgent: each agent
      - BedrockPrompt: each managed prompt

    Edges:
      - USES_MODEL: Agent/Prompt → Model
      - HAS_KB: Agent → KnowledgeBase
      - USES_PROMPT: Agent → Prompt (future, if action groups reference prompts)
    """
    all_nodes: list[AssetNode] = []
    all_edges: list[AssetEdge] = []

    try:
        agent_client = session.client("bedrock-agent", region_name=region)
    except ClientError as e:
        logger.debug("Failed to create bedrock-agent client: %s", e)
        return [], []

    # --- Agents ---
    try:
        agents_resp = agent_client.list_agents()
        for agent_summary in agents_resp.get("agentSummaries", []):
            agent_id = agent_summary.get("agentId", "")
            if not agent_id:
                continue

            try:
                detail = agent_client.get_agent(agentId=agent_id)
                agent_detail = detail.get("agent", {})
                agent_arn = agent_detail.get("agentArn", "")
                account_id = agent_arn.split(":")[4] if ":" in agent_arn else ""

                agent_node = AssetNode(
                    id=agent_arn or f"bedrock-agent:{agent_id}",
                    node_type="AWS::Bedrock::Agent",
                    name=agent_detail.get("agentName", agent_id),
                    provider="aws",
                    account_id=account_id,
                    region=region,
                    resource_type="AWS::Bedrock::Agent",
                    state=agent_detail.get("agentStatus", "ACTIVE"),
                    properties={
                        "agent_id": agent_id,
                        "description": agent_detail.get("description", ""),
                        "foundation_model": agent_detail.get("foundationModel", ""),
                        "instruction": agent_detail.get("instruction", "")[:200],
                        "idle_session_ttl": agent_detail.get("idleSessionTTLInSeconds", 0),
                    },
                )
                all_nodes.append(agent_node)

                # Agent → Model edge
                foundation_model = agent_detail.get("foundationModel", "")
                if foundation_model:
                    all_edges.append(AssetEdge(
                        source_id=agent_node.id,
                        target_id=f"bedrock-model:{foundation_model}",
                        edge_type="USES_MODEL",
                        provider="aws",
                        account_id=account_id,
                        properties={"relationship": "foundation_model"},
                    ))

                # Agent → KnowledgeBase edges
                try:
                    kb_resp = agent_client.list_agent_knowledge_bases(
                        agentId=agent_id, agentVersion="DRAFT"
                    )
                    for kb_assoc in kb_resp.get("agentKnowledgeBaseSummaries", []):
                        kb_id = kb_assoc.get("knowledgeBaseId", "")
                        if kb_id:
                            # Find KB in discovered resources
                            kb_target = _find_resource_by_id(resources, kb_id, "AWS::Bedrock::KnowledgeBase")
                            if not kb_target:
                                kb_target = f"bedrock-kb:{kb_id}"
                            all_edges.append(AssetEdge(
                                source_id=agent_node.id,
                                target_id=kb_target,
                                edge_type="HAS_KB",
                                provider="aws",
                                account_id=account_id,
                                properties={"kb_state": kb_assoc.get("knowledgeBaseState", "")},
                            ))
                except ClientError:
                    pass

            except ClientError:
                pass
    except ClientError as e:
        logger.debug("Failed to list agents: %s", e)

    # --- Prompts ---
    try:
        prompts_resp = agent_client.list_prompts()
        for prompt_summary in prompts_resp.get("promptSummaries", []):
            prompt_id = prompt_summary.get("id", "")
            prompt_name = prompt_summary.get("name", "")
            prompt_arn = prompt_summary.get("arn", "")
            if not prompt_id:
                continue

            try:
                detail = agent_client.get_prompt(promptIdentifier=prompt_id)
                variants = detail.get("variants", [])
                account_id = prompt_arn.split(":")[4] if ":" in prompt_arn else ""

                # Collect models used across variants
                variant_models = []
                for v in variants:
                    model_id = v.get("modelId", "")
                    if model_id:
                        variant_models.append(model_id)

                prompt_node = AssetNode(
                    id=prompt_arn or f"bedrock-prompt:{prompt_id}",
                    node_type="AWS::Bedrock::Prompt",
                    name=prompt_name,
                    provider="aws",
                    account_id=account_id,
                    region=region,
                    resource_type="AWS::Bedrock::Prompt",
                    state="ACTIVE",
                    properties={
                        "prompt_id": prompt_id,
                        "description": detail.get("description", ""),
                        "variant_count": len(variants),
                        "variant_models": variant_models,
                        "version": prompt_summary.get("version", ""),
                    },
                )
                all_nodes.append(prompt_node)

                # Prompt → Model edges (one per variant model)
                seen_models = set()
                for model_id in variant_models:
                    if model_id not in seen_models:
                        seen_models.add(model_id)
                        all_edges.append(AssetEdge(
                            source_id=prompt_node.id,
                            target_id=f"bedrock-model:{model_id}",
                            edge_type="USES_MODEL",
                            provider="aws",
                            account_id=account_id,
                            properties={"relationship": "prompt_model"},
                        ))

            except ClientError:
                pass
    except ClientError as e:
        logger.debug("Failed to list prompts: %s", e)

    logger.info("Bedrock agents: %d agent/prompt nodes, %d edges (USES_MODEL + HAS_KB)",
                len(all_nodes), len(all_edges))

    return all_nodes, all_edges


# ==================================================================
# Bedrock Inference Profiles
# ==================================================================


def _fetch_bedrock_profiles(session: boto3.Session, region: str) -> dict:
    """Hard inquiry: list inference profiles (cross-region model routing)."""
    bedrock = session.client("bedrock", region_name=region)

    logger.info("Fetching Bedrock inference profiles (hard inquiry)...")
    try:
        resp = bedrock.list_inference_profiles()
    except ClientError as e:
        logger.warning("Failed to list Bedrock inference profiles: %s", e)
        return {}

    profiles = {}
    for p in resp.get("inferenceProfileSummaries", []):
        profile_id = p.get("inferenceProfileId", "")
        if not profile_id:
            continue

        profiles[profile_id] = {
            "profile_id": profile_id,
            "profile_arn": p.get("inferenceProfileArn", ""),
            "profile_name": p.get("inferenceProfileName", ""),
            "type": p.get("type", ""),
            "status": p.get("status", ""),
            "description": p.get("description", ""),
            "model_source": p.get("modelSource", {}).get("copyFrom", ""),
        }

    logger.info("Fetched %d Bedrock inference profiles", len(profiles))
    return profiles


def _get_bedrock_profiles(session: boto3.Session, region: str, refresh: bool = False) -> dict:
    """Get Bedrock inference profiles — soft (cache) or hard."""
    cache_name = "bedrock-profiles"

    if not refresh:
        cached = read_cache(cache_name, region)
        if cached:
            return {k: v for k, v in cached.items() if k != "_metadata"}

    profiles = _fetch_bedrock_profiles(session, region)
    if profiles:
        write_cache(cache_name, region, profiles)
    return profiles


# ==================================================================
# Reference provider entry points
# ==================================================================


def enrich_bedrock_models(
    session: boto3.Session,
    region: str,
    resources: list[AssetNode],
    refresh: bool = False,
) -> tuple[list[AssetNode], list[AssetEdge]]:
    """Emit Bedrock model graph: models, providers, modalities, families + edges.

    Decomposes models into separate reference nodes:
      - BedrockModel: one per foundation model
      - BedrockProvider: one per model provider (Anthropic, Amazon, etc.)
      - BedrockModality: one per input/output modality (TEXT, IMAGE, etc.)
      - BedrockModelFamily: one per model family (anthropic.claude, amazon.nova, etc.)

    Edges:
      - PROVIDED_BY: Model → Provider
      - SUPPORTS_INPUT: Model → Modality
      - PRODUCES_OUTPUT: Model → Modality
      - MEMBER_OF: Model → ModelFamily
      - USES_MODEL: KnowledgeBase/Agent → Model (from bedrock-agent API)
    """
    # Get all foundation models
    models = _get_bedrock_models(session, region, refresh=refresh)
    if not models:
        return [], []

    all_nodes: list[AssetNode] = []
    all_edges: list[AssetEdge] = []

    # Track unique dimension values for reference nodes
    providers_seen: dict[str, AssetNode] = {}
    modalities_seen: dict[str, AssetNode] = {}
    families_seen: dict[str, AssetNode] = {}

    # Create model nodes + dimension edges
    for model_id, specs in models.items():
        if not isinstance(specs, dict):
            continue

        # Model node
        model_node = AssetNode(
            id=f"bedrock-model:{model_id}",
            node_type="AWS::Bedrock::FoundationModel",
            name=specs.get("model_name", "") or model_id,
            provider="aws",
            account_id="global",
            region=region,
            resource_type="AWS::Bedrock::FoundationModel",
            state=specs.get("lifecycle_status", "ACTIVE"),
            properties=specs,
        )
        all_nodes.append(model_node)

        # --- Provider node + PROVIDED_BY edge ---
        provider_name = specs.get("provider_name", "")
        if provider_name:
            if provider_name not in providers_seen:
                providers_seen[provider_name] = AssetNode(
                    id=f"bedrock-provider:{provider_name}",
                    node_type="AWS::Bedrock::Provider",
                    name=provider_name,
                    provider="aws",
                    account_id="global",
                    region="global",
                    resource_type="AWS::Bedrock::Provider",
                    state="ACTIVE",
                    properties={"provider_name": provider_name},
                )
            all_edges.append(AssetEdge(
                source_id=model_node.id,
                target_id=f"bedrock-provider:{provider_name}",
                edge_type="PROVIDED_BY",
                provider="aws",
            ))

        # --- Modality nodes + SUPPORTS_INPUT / PRODUCES_OUTPUT edges ---
        for modality in specs.get("input_modalities", []):
            if modality not in modalities_seen:
                modalities_seen[modality] = AssetNode(
                    id=f"bedrock-modality:{modality}",
                    node_type="AWS::Bedrock::Modality",
                    name=modality,
                    provider="aws",
                    account_id="global",
                    region="global",
                    resource_type="AWS::Bedrock::Modality",
                    state="ACTIVE",
                    properties={"modality": modality, "direction": "input"},
                )
            all_edges.append(AssetEdge(
                source_id=model_node.id,
                target_id=f"bedrock-modality:{modality}",
                edge_type="SUPPORTS_INPUT",
                provider="aws",
            ))

        for modality in specs.get("output_modalities", []):
            if modality not in modalities_seen:
                modalities_seen[modality] = AssetNode(
                    id=f"bedrock-modality:{modality}",
                    node_type="AWS::Bedrock::Modality",
                    name=modality,
                    provider="aws",
                    account_id="global",
                    region="global",
                    resource_type="AWS::Bedrock::Modality",
                    state="ACTIVE",
                    properties={"modality": modality},
                )
            all_edges.append(AssetEdge(
                source_id=model_node.id,
                target_id=f"bedrock-modality:{modality}",
                edge_type="PRODUCES_OUTPUT",
                provider="aws",
            ))

        # --- Model family node + MEMBER_OF edge ---
        parts = model_id.split(".")
        if len(parts) >= 2:
            family = f"{parts[0]}.{parts[1].split('-')[0]}"
            if family not in families_seen:
                families_seen[family] = AssetNode(
                    id=f"bedrock-family:{family}",
                    node_type="AWS::Bedrock::ModelFamily",
                    name=family,
                    provider="aws",
                    account_id="global",
                    region="global",
                    resource_type="AWS::Bedrock::ModelFamily",
                    state="ACTIVE",
                    properties={"family": family, "provider_name": parts[0]},
                )
            all_edges.append(AssetEdge(
                source_id=model_node.id,
                target_id=f"bedrock-family:{family}",
                edge_type="MEMBER_OF",
                provider="aws",
            ))

    # Add dimension reference nodes
    all_nodes.extend(providers_seen.values())
    all_nodes.extend(modalities_seen.values())
    all_nodes.extend(families_seen.values())

    # --- USES_MODEL edges from KnowledgeBases and Agents ---
    uses_model_edges = _discover_model_usage(session, region, resources, models)
    all_edges.extend(uses_model_edges)

    logger.info("Bedrock models: %d model nodes, %d provider/modality/family nodes, %d edges (%d USES_MODEL)",
                len(models), len(providers_seen) + len(modalities_seen) + len(families_seen),
                len(all_edges), len(uses_model_edges))

    return all_nodes, all_edges


def _discover_model_usage(
    session: boto3.Session,
    region: str,
    resources: list[AssetNode],
    models: dict,
) -> list[AssetEdge]:
    """Query bedrock-agent API for USES_MODEL edges (KB→model, Agent→model)."""
    edges = []

    try:
        agent_client = session.client("bedrock-agent", region_name=region)

        # KnowledgeBases → embedding model
        try:
            kb_resp = agent_client.list_knowledge_bases()
            for kb_summary in kb_resp.get("knowledgeBaseSummaries", []):
                kb_id = kb_summary.get("knowledgeBaseId", "")
                if not kb_id:
                    continue
                try:
                    detail = agent_client.get_knowledge_base(knowledgeBaseId=kb_id)
                    kb_detail = detail.get("knowledgeBase", {})
                    kb_config = kb_detail.get("knowledgeBaseConfiguration", {})
                    vector_config = kb_config.get("vectorKnowledgeBaseConfiguration", {})
                    embedding_arn = vector_config.get("embeddingModelArn", "")
                    kb_arn = kb_detail.get("knowledgeBaseArn", "")

                    if embedding_arn:
                        source_id = _find_resource_by_id(resources, kb_id, "AWS::Bedrock::KnowledgeBase") or kb_arn
                        resolved_model = _resolve_model_id(embedding_arn, models)
                        if source_id and resolved_model:
                            edges.append(AssetEdge(
                                source_id=source_id,
                                target_id=f"bedrock-model:{resolved_model}",
                                edge_type="USES_MODEL",
                                provider="aws",
                                account_id=kb_arn.split(":")[4] if ":" in kb_arn else "",
                                properties={"relationship": "embedding_model"},
                            ))
                except ClientError:
                    pass
        except ClientError as e:
            logger.debug("Failed to list knowledge bases: %s", e)

        # Agents → foundation model
        try:
            agents_resp = agent_client.list_agents()
            for agent_summary in agents_resp.get("agentSummaries", []):
                agent_id = agent_summary.get("agentId", "")
                if not agent_id:
                    continue
                try:
                    detail = agent_client.get_agent(agentId=agent_id)
                    agent_detail = detail.get("agent", {})
                    foundation_model = agent_detail.get("foundationModel", "")
                    agent_arn = agent_detail.get("agentArn", "")

                    if foundation_model:
                        resolved_model = _resolve_model_id(foundation_model, models)
                        if resolved_model:
                            edges.append(AssetEdge(
                                source_id=agent_arn or f"bedrock-agent:{agent_id}",
                                target_id=f"bedrock-model:{resolved_model}",
                                edge_type="USES_MODEL",
                                provider="aws",
                                account_id=agent_arn.split(":")[4] if ":" in agent_arn else "",
                                properties={"relationship": "foundation_model"},
                            ))
                except ClientError:
                    pass
        except ClientError as e:
            logger.debug("Failed to list agents: %s", e)

    except Exception as e:
        logger.debug("bedrock-agent model usage discovery error: %s", e)

    return edges


def enrich_bedrock_profiles(
    session: boto3.Session,
    region: str,
    resources: list[AssetNode],
    refresh: bool = False,
) -> tuple[list[AssetNode], list[AssetEdge]]:
    """Emit BedrockInferenceProfile reference nodes and ROUTED_VIA edges."""
    profiles = _get_bedrock_profiles(session, region, refresh=refresh)
    if not profiles:
        return [], []

    # Create reference nodes
    profile_nodes = []
    for profile_id, specs in profiles.items():
        profile_nodes.append(AssetNode(
            id=f"bedrock-profile:{profile_id}",
            node_type="AWS::Bedrock::InferenceProfile",
            name=specs.get("profile_name", profile_id),
            provider="aws",
            account_id="global",
            region=region,
            resource_type="AWS::Bedrock::InferenceProfile",
            state=specs.get("status", "ACTIVE"),
            properties=specs,
        ))

    logger.info("Bedrock inference profiles: %d reference nodes", len(profile_nodes))
    return profile_nodes, []


# ==================================================================
# Helpers
# ==================================================================


def _resolve_model_id(model_ref: str, known_models: dict) -> str:
    """Resolve a model reference (ARN or ID) to a known model_id."""
    # Direct match
    if model_ref in known_models:
        return model_ref

    # ARN → extract model ID from the end
    if model_ref.startswith("arn:"):
        # arn:aws:bedrock:region::foundation-model/anthropic.claude-3-sonnet...
        parts = model_ref.split("/")
        if len(parts) >= 2:
            candidate = parts[-1]
            if candidate in known_models:
                return candidate

    # Partial match (model_ref might be a prefix or inference profile ID)
    for model_id in known_models:
        if model_id.startswith(model_ref) or model_ref in model_id:
            return model_id

    return ""


def _find_resource_by_id(resources: list[AssetNode], resource_id: str, resource_type: str = "") -> str:
    """Find a resource's ARN/id by its short resource ID."""
    for r in resources:
        if resource_type and r.resource_type != resource_type:
            continue
        # Check if the resource_id appears in the node's id or name
        if resource_id in r.id or r.name == resource_id:
            return r.id
    return ""


# ==================================================================
# Bedrock Platform (guardrails, prompt routers, batch jobs, reasoning)
# ==================================================================


def enrich_bedrock_platform(
    session: boto3.Session,
    region: str,
    resources: list[AssetNode],
    refresh: bool = False,
) -> tuple[list[AssetNode], list[AssetEdge]]:
    """Discover Bedrock platform resources: guardrails, prompt routers, batch jobs, reasoning."""
    all_nodes: list[AssetNode] = []
    all_edges: list[AssetEdge] = []

    try:
        bedrock = session.client("bedrock", region_name=region)
    except Exception:
        return [], []

    # --- Guardrails (detailed) ---
    try:
        resp = bedrock.list_guardrails()
        for g in resp.get("guardrails", []):
            guardrail_id = g.get("id", "")
            guardrail_arn = g.get("arn", "")
            account_id = guardrail_arn.split(":")[4] if ":" in guardrail_arn else ""
            all_nodes.append(AssetNode(
                id=guardrail_arn or f"bedrock-guardrail:{guardrail_id}",
                node_type="AWS::Bedrock::Guardrail",
                name=g.get("name", guardrail_id),
                provider="aws",
                account_id=account_id,
                region=region,
                resource_type="AWS::Bedrock::Guardrail",
                state=g.get("status", "ACTIVE"),
                properties={
                    "guardrail_id": guardrail_id,
                    "description": g.get("description", ""),
                    "version": g.get("version", ""),
                },
            ))
    except ClientError as e:
        logger.debug("list_guardrails: %s", e)

    # --- Prompt Routers ---
    try:
        resp = bedrock.list_prompt_routers()
        for r in resp.get("promptRouterSummaries", []):
            router_name = r.get("promptRouterName", "")
            router_arn = r.get("promptRouterArn", "")
            account_id = router_arn.split(":")[4] if ":" in router_arn else ""
            all_nodes.append(AssetNode(
                id=router_arn or f"bedrock-router:{router_name}",
                node_type="AWS::Bedrock::PromptRouter",
                name=router_name,
                provider="aws",
                account_id=account_id,
                region=region,
                resource_type="AWS::Bedrock::PromptRouter",
                state="ACTIVE",
                properties={
                    "description": r.get("description", ""),
                    "routing_criteria": r.get("routingCriteria", {}),
                    "model_count": len(r.get("models", [])),
                },
            ))
            # Router → Model edges
            for model in r.get("models", []):
                model_arn = model.get("modelArn", "")
                if model_arn:
                    model_id = model_arn.split("/")[-1] if "/" in model_arn else model_arn
                    all_edges.append(AssetEdge(
                        source_id=router_arn or f"bedrock-router:{router_name}",
                        target_id=f"bedrock-model:{model_id}",
                        edge_type="ROUTES_TO",
                        provider="aws",
                    ))
    except ClientError as e:
        logger.debug("list_prompt_routers: %s", e)

    # --- Batch Invocation Jobs ---
    try:
        resp = bedrock.list_model_invocation_jobs()
        for j in resp.get("invocationJobSummaries", []):
            job_arn = j.get("jobArn", "")
            job_name = j.get("jobName", "")
            account_id = job_arn.split(":")[4] if ":" in job_arn else ""
            model_id = j.get("modelId", "")
            all_nodes.append(AssetNode(
                id=job_arn or f"bedrock-batch:{job_name}",
                node_type="AWS::Bedrock::BatchInferenceJob",
                name=job_name,
                provider="aws",
                account_id=account_id,
                region=region,
                resource_type="AWS::Bedrock::BatchInferenceJob",
                state=j.get("status", ""),
                properties={
                    "job_name": job_name,
                    "model_id": model_id,
                    "status": j.get("status", ""),
                    "submitted_at": str(j.get("submitTime", "")),
                    "end_time": str(j.get("endTime", "")),
                },
            ))
            # Job → Model edge
            if model_id:
                resolved = model_id.split("/")[-1] if "/" in model_id else model_id
                all_edges.append(AssetEdge(
                    source_id=job_arn or f"bedrock-batch:{job_name}",
                    target_id=f"bedrock-model:{resolved}",
                    edge_type="USES_MODEL",
                    provider="aws",
                    account_id=account_id,
                    properties={"relationship": "batch_inference"},
                ))
    except ClientError as e:
        logger.debug("list_model_invocation_jobs: %s", e)

    # --- Automated Reasoning Policies ---
    try:
        resp = bedrock.list_automated_reasoning_policies()
        for p in resp.get("automatedReasoningPolicySummaries", []):
            policy_arn = p.get("policyArn", "")
            policy_name = p.get("name", "")
            account_id = policy_arn.split(":")[4] if ":" in policy_arn else ""
            all_nodes.append(AssetNode(
                id=policy_arn or f"bedrock-reasoning:{policy_name}",
                node_type="AWS::Bedrock::AutomatedReasoningPolicy",
                name=policy_name,
                provider="aws",
                account_id=account_id,
                region=region,
                resource_type="AWS::Bedrock::AutomatedReasoningPolicy",
                state="ACTIVE",
                properties={
                    "description": p.get("description", ""),
                    "version": p.get("version", ""),
                },
            ))
    except ClientError as e:
        logger.debug("list_automated_reasoning_policies: %s", e)

    logger.info("Bedrock platform: %d nodes, %d edges (guardrails, routers, batch, reasoning)",
                len(all_nodes), len(all_edges))
    return all_nodes, all_edges


# ==================================================================
# Bedrock AgentCore (runtimes, gateways, registries, memories, workload IDs)
# ==================================================================


def enrich_bedrock_agentcore(
    session: boto3.Session,
    region: str,
    resources: list[AssetNode],
    refresh: bool = False,
) -> tuple[list[AssetNode], list[AssetEdge]]:
    """Discover Bedrock AgentCore: runtimes, gateways, registries, memories, workload identities."""
    all_nodes: list[AssetNode] = []
    all_edges: list[AssetEdge] = []

    try:
        control = session.client("bedrock-agentcore-control", region_name=region)
    except Exception:
        return [], []

    # --- Agent Runtimes ---
    try:
        resp = control.list_agent_runtimes()
        for r in resp.get("agentRuntimes", []):
            runtime_id = r.get("agentRuntimeId", "")
            runtime_name = r.get("agentRuntimeName", "")
            runtime_arn = r.get("agentRuntimeArn", "")
            account_id = runtime_arn.split(":")[4] if ":" in runtime_arn else ""
            all_nodes.append(AssetNode(
                id=runtime_arn or f"agentcore-runtime:{runtime_id}",
                node_type="AWS::Bedrock::AgentRuntime",
                name=runtime_name,
                provider="aws",
                account_id=account_id,
                region=region,
                resource_type="AWS::Bedrock::AgentRuntime",
                state=r.get("status", "ACTIVE"),
                properties={
                    "runtime_id": runtime_id,
                    "description": r.get("description", ""),
                },
            ))
    except ClientError as e:
        logger.debug("list_agent_runtimes: %s", e)

    # --- Gateways ---
    try:
        resp = control.list_gateways()
        for g in resp.get("gateways", []):
            gateway_id = g.get("gatewayId", "")
            gateway_name = g.get("name", "")
            gateway_arn = g.get("gatewayArn", "")
            account_id = gateway_arn.split(":")[4] if ":" in gateway_arn else ""
            all_nodes.append(AssetNode(
                id=gateway_arn or f"agentcore-gateway:{gateway_id}",
                node_type="AWS::Bedrock::Gateway",
                name=gateway_name,
                provider="aws",
                account_id=account_id,
                region=region,
                resource_type="AWS::Bedrock::Gateway",
                state=g.get("status", "ACTIVE"),
                properties={
                    "gateway_id": gateway_id,
                    "description": g.get("description", ""),
                },
            ))
            # Gateway → Runtime edges (runtimes route via gateway)
            for runtime_node in [n for n in all_nodes if n.node_type == "AWS::Bedrock::AgentRuntime"]:
                all_edges.append(AssetEdge(
                    source_id=runtime_node.id,
                    target_id=gateway_arn or f"agentcore-gateway:{gateway_id}",
                    edge_type="ROUTES_VIA",
                    provider="aws",
                ))
    except ClientError as e:
        logger.debug("list_gateways: %s", e)

    # --- Registries ---
    try:
        resp = control.list_registries()
        for r in resp.get("registries", []):
            registry_id = r.get("registryId", "")
            registry_name = r.get("name", "")
            registry_arn = r.get("registryArn", "")
            account_id = registry_arn.split(":")[4] if ":" in registry_arn else ""
            all_nodes.append(AssetNode(
                id=registry_arn or f"agentcore-registry:{registry_id}",
                node_type="AWS::Bedrock::Registry",
                name=registry_name,
                provider="aws",
                account_id=account_id,
                region=region,
                resource_type="AWS::Bedrock::Registry",
                state="ACTIVE",
                properties={
                    "registry_id": registry_id,
                    "description": r.get("description", ""),
                },
            ))
            # Runtimes → Registry (REGISTERED_IN)
            for runtime_node in [n for n in all_nodes if n.node_type == "AWS::Bedrock::AgentRuntime"]:
                all_edges.append(AssetEdge(
                    source_id=runtime_node.id,
                    target_id=registry_arn or f"agentcore-registry:{registry_id}",
                    edge_type="REGISTERED_IN",
                    provider="aws",
                ))
    except ClientError as e:
        logger.debug("list_registries: %s", e)

    # --- Memories ---
    try:
        resp = control.list_memories()
        for m in resp.get("memories", []):
            memory_id = m.get("id", "")
            memory_arn = m.get("arn", "")
            account_id = memory_arn.split(":")[4] if ":" in memory_arn else ""
            all_nodes.append(AssetNode(
                id=memory_arn or f"agentcore-memory:{memory_id}",
                node_type="AWS::Bedrock::Memory",
                name=memory_id,
                provider="aws",
                account_id=account_id,
                region=region,
                resource_type="AWS::Bedrock::Memory",
                state=m.get("status", "ACTIVE"),
                properties={
                    "memory_id": memory_id,
                },
            ))
    except ClientError as e:
        logger.debug("list_memories: %s", e)

    # --- Workload Identities ---
    try:
        resp = control.list_workload_identities()
        for w in resp.get("workloadIdentities", []):
            wid_name = w.get("name", "")
            wid_id = w.get("workloadIdentityId", "")
            wid_arn = w.get("workloadIdentityArn", "")
            account_id = wid_arn.split(":")[4] if ":" in wid_arn else ""
            all_nodes.append(AssetNode(
                id=wid_arn or f"agentcore-identity:{wid_name}",
                node_type="AWS::Bedrock::WorkloadIdentity",
                name=wid_name,
                provider="aws",
                account_id=account_id,
                region=region,
                resource_type="AWS::Bedrock::WorkloadIdentity",
                state="ACTIVE",
                properties={
                    "workload_identity_id": wid_id,
                },
            ))
            # Match workload identity to its runtime (name pattern: runtimeName-xxxxx)
            for runtime_node in [n for n in all_nodes if n.node_type == "AWS::Bedrock::AgentRuntime"]:
                if runtime_node.properties.get("runtime_id", "") in wid_name:
                    all_edges.append(AssetEdge(
                        source_id=runtime_node.id,
                        target_id=wid_arn or f"agentcore-identity:{wid_name}",
                        edge_type="RUNS_AS",
                        provider="aws",
                    ))
    except ClientError as e:
        logger.debug("list_workload_identities: %s", e)

    logger.info("Bedrock AgentCore: %d nodes, %d edges (runtimes, gateways, registries, memories, identities)",
                len(all_nodes), len(all_edges))
    return all_nodes, all_edges


# ==================================================================
# Registry — all reference providers
# ==================================================================

REFERENCE_PROVIDERS: dict[str, Callable] = {
    "bedrock_models": enrich_bedrock_models,
    "bedrock_agents": enrich_bedrock_agents,
    "bedrock_profiles": enrich_bedrock_profiles,
    "bedrock_platform": enrich_bedrock_platform,
    "bedrock_agentcore": enrich_bedrock_agentcore,
}
