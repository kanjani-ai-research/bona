"""Pydantic models for bona's compliance and prompt pipeline.

Structured types for:
  - Prompt templates (loaded from YAML, synced to Bedrock)
  - OSCAL controls (from catalog JSON)
  - Compliance findings (enriched with NIST mapping)
  - Scan results
"""

from typing import Any, Optional
from pydantic import BaseModel, Field


# ==================================================================
# Prompt Models
# ==================================================================


class PromptTemplate(BaseModel):
    """A prompt template stored in frameworks/prompts/*.yaml."""

    name: str
    version: str = "1.0"
    description: str = ""
    system: str = ""
    user_template: str = ""
    input_variables: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    bedrock_prompt_id: str = ""  # Set after sync


class PromptSyncResult(BaseModel):
    """Result of syncing a prompt to Bedrock."""

    name: str
    status: str  # created | updated | error
    id: str = ""
    error: str = ""


# ==================================================================
# OSCAL Models
# ==================================================================


class OSCALControl(BaseModel):
    """A single NIST 800-53 control from the OSCAL catalog."""

    id: str  # e.g. "AC-2", "SC-28"
    title: str  # e.g. "Account Management"
    description: str = ""
    group_id: str = ""  # e.g. "ac"
    group_title: str = ""  # e.g. "Access Control"
    control_class: str = Field(default="", alias="class")

    class Config:
        populate_by_name = True


class ControlMapping(BaseModel):
    """Mapping from a Config rule to NIST controls."""

    rule_name: str
    rule_description: str = ""
    nist_controls: list[OSCALControl] = Field(default_factory=list)
    mapping_source: str = "heuristic"  # heuristic | llm | manual


# ==================================================================
# Compliance Finding Models
# ==================================================================


class ComplianceFinding(BaseModel):
    """An enriched compliance finding with NIST mapping and remediation."""

    resource_id: str
    resource_type: str
    resource_name: str = ""
    region: str = ""
    account_id: str = ""

    # Config rule that failed
    rule_name: str
    rule_description: str = ""
    rule_source: str = ""
    annotation: str = ""

    # NIST mapping (from OSCAL)
    nist_control_id: str = ""
    nist_control_title: str = ""
    nist_group: str = ""

    # LLM-generated remediation
    severity: str = ""  # Critical | High | Medium | Low
    impact: str = ""
    remediation_steps: list[str] = Field(default_factory=list)
    cli_command: str = ""
    verification: str = ""

    # Source
    kb_context: str = ""
    prompt_used: str = ""  # local template name or Bedrock prompt ID


class CompliancePosture(BaseModel):
    """Overall compliance posture summary."""

    total_resources: int = 0
    compliant_count: int = 0
    non_compliant_count: int = 0
    compliance_rate: float = 0.0

    conformance_packs: list[dict] = Field(default_factory=list)
    top_failing_rules: list[dict] = Field(default_factory=list)
    failing_resource_types: list[dict] = Field(default_factory=list)

    overall_posture: str = ""  # Critical | At-Risk | Moderate | Good | Excellent
    executive_summary: str = ""
    key_risks: list[dict] = Field(default_factory=list)
    recommendations: list[dict] = Field(default_factory=list)


# ==================================================================
# Prompt Execution Config
# ==================================================================


class PromptSource(BaseModel):
    """Where to load a prompt from: local file or Bedrock managed."""

    source: str = "local"  # local | bedrock
    local_name: str = ""  # prompt template name in frameworks/prompts/
    bedrock_prompt_id: str = ""  # Bedrock Prompt Management ID
    bedrock_prompt_version: str = ""  # optional version


class PromptConfig(BaseModel):
    """Configuration for which prompts to use per task.

    Allows specifying local templates OR Bedrock managed prompts per task.

    bona.yaml:
      prompts:
        compliance_mapping:
          source: bedrock
          bedrock_prompt_id: EABD03UBZY
        remediation:
          source: local
          local_name: remediation-guidance
    """

    compliance_mapping: PromptSource = Field(default_factory=lambda: PromptSource(source="local", local_name="compliance-mapping"))
    remediation: PromptSource = Field(default_factory=lambda: PromptSource(source="local", local_name="remediation-guidance"))
    audit_summary: PromptSource = Field(default_factory=lambda: PromptSource(source="local", local_name="audit-summary"))
    classify_relationships: PromptSource = Field(default_factory=lambda: PromptSource(source="local", local_name="classify-relationships"))
    system: PromptSource = Field(default_factory=lambda: PromptSource(source="local", local_name="bona-system"))
