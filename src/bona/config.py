"""Bona Configuration — controls enrichment, output, logging, and behavior.

Looks for config in order:
  1. --config CLI flag (explicit path)
  2. ./bona.yaml or ./bona.json (project-local)
  3. ~/.bona/config.yaml or ~/.bona/config.json (user-level)

Example bona.yaml:

    logging:
      level: INFO              # DEBUG, INFO, WARNING, ERROR
      path: ./bona.log         # local file (or s3://bucket/logs/)

    enrichment:
      enabled: true
      mode: soft               # soft | hard
      include:
        - "AWS::EC2::*"
        - "AWS::S3::*"
      exclude:
        - "AWS::CloudFormation::Stack"
      supplemental:
        ec2_instance_specs: true

    output:
      dir: ./bona-output
      s3_uri: s3://my-bucket/apg/

    schema_cache:
      s3_uri: s3://my-bucket/schemas/

    graph_schema:
      s3_uri: s3://my-bucket/graph-schema.json
      # path: ./graph-schema.json

Example bona.json:

    {
      "logging": {
        "level": "INFO",
        "path": "./bona.log"
      },
      "enrichment": {
        "enabled": true,
        "mode": "soft",
        "include": ["AWS::EC2::*", "AWS::S3::*"],
        "exclude": ["AWS::CloudFormation::Stack"],
        "supplemental": {
          "ec2_instance_specs": true
        }
      },
      "output": {
        "dir": "./bona-output",
        "s3_uri": "s3://my-bucket/apg/"
      },
      "schema_cache": {
        "s3_uri": "s3://my-bucket/schemas/"
      },
      "graph_schema": {
        "s3_uri": "s3://my-bucket/graph-schema.json"
      }
    }
"""

import fnmatch
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentConfig:
    """Controls which types get enriched."""

    enabled: bool = False
    mode: str = "soft"  # "soft" or "hard"
    include: list[str] = field(default_factory=list)  # glob patterns, empty = all
    exclude: list[str] = field(default_factory=list)  # glob patterns
    supplemental: dict[str, bool] = field(default_factory=lambda: {
        "ec2_instance_specs": True,
    })

    def should_enrich(self, type_name: str) -> bool:
        """Check if a resource type should be enriched (include/exclude logic)."""
        if not self.enabled:
            return False

        # If include list is set, type must match at least one pattern
        if self.include:
            matched = any(fnmatch.fnmatch(type_name, pat) for pat in self.include)
            if not matched:
                return False

        # If exclude list has a match, skip it
        if self.exclude:
            excluded = any(fnmatch.fnmatch(type_name, pat) for pat in self.exclude)
            if excluded:
                return False

        return True


@dataclass
class OutputConfig:
    """Controls output location."""

    dir: str = "./bona-output"
    s3_uri: str = ""


@dataclass
class LoggingConfig:
    """Controls logging level and output."""

    level: str = "INFO"   # DEBUG, INFO, WARNING, ERROR
    path: str = ""        # local file path or s3:// URI (empty = stderr only)


@dataclass
class SchemaCacheConfig:
    """Controls schema cache upload."""

    s3_uri: str = ""


@dataclass
class GraphSchemaConfig:
    """Controls graph schema (learn mode) storage."""

    s3_uri: str = ""  # S3 URI for graph-schema.json upload
    path: str = ""    # local path override (default: ~/.bona/graph-schema.json)


@dataclass
class LearningConfig:
    """Controls storage for all learning artifacts."""

    s3_uri: str = ""  # S3 prefix for all learning outputs (graph-schema, rules, descriptions)
    # Individual path overrides
    graph_schema_path: str = ""
    relationship_rules_path: str = ""
    descriptions_path: str = ""


@dataclass
class LLMTaskConfig:
    """Model override per task."""

    classify: str = ""
    validate: str = ""
    describe: str = ""


@dataclass
class LLMConfig:
    """Controls LLM enrichment behavior."""

    enabled: bool = True
    model_id: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    region: str = ""  # empty = use export region
    max_nodes_per_batch: int = 20
    max_candidates_per_call: int = 10
    temperature: float = 0.0
    budget_max_calls: int = 10
    system_prompt: str = "You are an AWS infrastructure analyst specializing in resource relationships and graph topology."
    tasks: LLMTaskConfig = field(default_factory=LLMTaskConfig)


@dataclass
class BonaConfig:
    """Top-level configuration."""

    enrichment: EnrichmentConfig = field(default_factory=EnrichmentConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    schema_cache: SchemaCacheConfig = field(default_factory=SchemaCacheConfig)
    graph_schema: GraphSchemaConfig = field(default_factory=GraphSchemaConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)

    @classmethod
    def from_dict(cls, data: dict) -> "BonaConfig":
        """Parse config from a dict (loaded from JSON/YAML)."""
        enrichment_data = data.get("enrichment", {})
        output_data = data.get("output", {})
        logging_data = data.get("logging", {})
        schema_cache_data = data.get("schema_cache", {})
        graph_schema_data = data.get("graph_schema", {})

        enrichment = EnrichmentConfig(
            enabled=enrichment_data.get("enabled", False),
            mode=enrichment_data.get("mode", "soft"),
            include=enrichment_data.get("include", []),
            exclude=enrichment_data.get("exclude", []),
            supplemental=enrichment_data.get("supplemental", {"ec2_instance_specs": True}),
        )

        output = OutputConfig(
            dir=output_data.get("dir", "./bona-output"),
            s3_uri=output_data.get("s3_uri", ""),
        )

        log_cfg = LoggingConfig(
            level=logging_data.get("level", "INFO"),
            path=logging_data.get("path", ""),
        )

        schema_cache = SchemaCacheConfig(
            s3_uri=schema_cache_data.get("s3_uri", ""),
        )

        graph_schema = GraphSchemaConfig(
            s3_uri=graph_schema_data.get("s3_uri", ""),
            path=graph_schema_data.get("path", ""),
        )

        llm_data = data.get("llm", {})
        tasks_data = llm_data.get("tasks", {})
        llm_cfg = LLMConfig(
            enabled=llm_data.get("enabled", True),
            model_id=llm_data.get("model_id", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
            region=llm_data.get("region", ""),
            max_nodes_per_batch=llm_data.get("max_nodes_per_batch", 20),
            max_candidates_per_call=llm_data.get("max_candidates_per_call", 10),
            temperature=llm_data.get("temperature", 0.0),
            budget_max_calls=llm_data.get("budget_max_calls", 10),
            system_prompt=llm_data.get("system_prompt", LLMConfig.system_prompt),
            tasks=LLMTaskConfig(
                classify=tasks_data.get("classify", ""),
                validate=tasks_data.get("validate", ""),
                describe=tasks_data.get("describe", ""),
            ),
        )

        learning_data = data.get("learning", {})
        learning_cfg = LearningConfig(
            s3_uri=learning_data.get("s3_uri", ""),
            graph_schema_path=learning_data.get("graph_schema_path", ""),
            relationship_rules_path=learning_data.get("relationship_rules_path", ""),
            descriptions_path=learning_data.get("descriptions_path", ""),
        )

        return cls(enrichment=enrichment, output=output, logging=log_cfg, schema_cache=schema_cache, graph_schema=graph_schema, llm=llm_cfg, learning=learning_cfg)

    @classmethod
    def default(cls) -> "BonaConfig":
        """Return default config (no enrichment, local output only)."""
        return cls()


def load_config(config_path: Optional[str] = None) -> BonaConfig:
    """Load configuration from file.

    Search order:
      1. Explicit path (--config flag)
      2. ./bona.yaml or ./bona.json
      3. ~/.bona/config.yaml or ~/.bona/config.json

    Returns default config if no file found.
    """
    if config_path:
        path = Path(config_path)
        if path.exists():
            return _parse_config_file(path)
        logger.warning("Config file not found: %s, using defaults", path)
        return BonaConfig.default()

    # Search local directory
    for name in ("bona.yaml", "bona.yml", "bona.json"):
        path = Path.cwd() / name
        if path.exists():
            logger.info("Using config: %s", path)
            return _parse_config_file(path)

    # Search user home
    for name in ("config.yaml", "config.yml", "config.json"):
        path = Path.home() / ".bona" / name
        if path.exists():
            logger.info("Using config: %s", path)
            return _parse_config_file(path)

    # No config file found — defaults
    return BonaConfig.default()


def _parse_config_file(path: Path) -> BonaConfig:
    """Parse a config file (JSON or YAML)."""
    text = path.read_text()

    if path.suffix in (".yaml", ".yml"):
        data = _parse_yaml(text)
    elif path.suffix == ".json":
        data = json.loads(text)
    else:
        # Try JSON first, then YAML
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = _parse_yaml(text)

    if not isinstance(data, dict):
        logger.warning("Config file is not a dict: %s", path)
        return BonaConfig.default()

    logger.info("Loaded config from %s", path)
    return BonaConfig.from_dict(data)


def _parse_yaml(text: str) -> dict:
    """Parse YAML text. Falls back to basic parsing if pyyaml not installed."""
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:
        # Basic YAML subset parser for simple configs
        # Supports flat key: value and simple lists
        logger.debug("pyyaml not installed, attempting basic YAML parse")
        return _basic_yaml_parse(text)


def _basic_yaml_parse(text: str) -> dict:
    """Minimal YAML parser for simple bona configs (no pyyaml dependency).

    Supports:
      - key: value (string, bool, int)
      - key:        (starts a nested dict)
      -   - item    (list items)
    """
    result: dict = {}
    stack: list[tuple[dict, int]] = [(result, -1)]
    current_key = None

    for line in text.split("\n"):
        stripped = line.rstrip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())

        # Pop stack to find parent at correct indent level
        while len(stack) > 1 and indent <= stack[-1][1]:
            stack.pop()

        parent = stack[-1][0]

        if stripped.lstrip().startswith("- "):
            # List item
            value = stripped.lstrip()[2:].strip().strip('"').strip("'")
            if current_key and current_key in parent:
                if not isinstance(parent[current_key], list):
                    parent[current_key] = []
                parent[current_key].append(_coerce_value(value))
            continue

        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()

            if val:
                # key: value
                parent[key] = _coerce_value(val.strip('"').strip("'"))
            else:
                # key: (nested dict or list)
                parent[key] = {}
                stack.append((parent[key], indent))
                current_key = key

    return result


def _coerce_value(val: str):
    """Coerce string value to appropriate Python type."""
    if val.lower() in ("true", "yes"):
        return True
    if val.lower() in ("false", "no"):
        return False
    if val.lower() in ("null", "none", "~"):
        return None
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val
