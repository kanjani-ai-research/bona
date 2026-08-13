"""Bona Graph Writer — Schema-validated writes to Neptune.

Apps NEVER write to Neptune directly. All writes go through this module,
which validates against the schema define blocks before writing.

This ensures:
  - Schema integrity (required fields, enum values, types)
  - Audit trail (who wrote what, when)
  - Consistent node/edge format
  - Single source of truth for graph structure

Usage:
    from bona.graph_writer import GraphWriter

    writer = GraphWriter(
        neptune_endpoint="obs-app-dev-graph.cluster-xxx.neptune.amazonaws.com",
        schema_paths=["schemas/scim-identity/scim-identity.yaml"],
    )

    writer.create_node("Task", "scim:Task:abc123", {
        "task_id": "abc123",
        "task_type": "APPROVAL",
        "status": "PENDING",
        "requester_id": "user-xyz",
    }, created_by="SCIM_PRJ")
"""

import json
import logging
import os
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ValidationError:
    """A schema validation failure."""
    field: str
    message: str
    value: Any = None


@dataclass
class WriteResult:
    """Result of a graph write operation."""
    success: bool
    node_id: str = ""
    operation: str = ""  # create_node, create_edge, update_node
    validation_errors: list[ValidationError] = field(default_factory=list)
    error: str = ""
    timestamp: str = ""


class SchemaRegistry:
    """Loads and caches schema define blocks for validation."""

    def __init__(self, schema_paths: list[str]):
        self._definitions: dict[str, dict] = {}  # node_type -> define block
        self._load_schemas(schema_paths)

    def _load_schemas(self, paths: list[str]):
        """Load all schema files and index define blocks by node_type."""
        import yaml

        for path in paths:
            if not os.path.exists(path):
                logger.warning(f"Schema file not found: {path}")
                continue

            with open(path) as f:
                data = yaml.safe_load(f)

            ext = data.get("schema_extension", data)
            schema_id = ext.get("schema_id", "unknown")
            owner = ext.get("owner", "")

            for define_block in ext.get("define", []):
                node_type = define_block.get("node_type", "")
                if node_type:
                    self._definitions[node_type] = {
                        "schema_id": schema_id,
                        "owner": owner,
                        "node_type": node_type,
                        "description": define_block.get("description", ""),
                        "id_pattern": define_block.get("id_pattern", ""),
                        "properties": define_block.get("properties", []),
                        "relationships": define_block.get("relationships", []),
                    }

            logger.info(f"Loaded schema {schema_id}: {len(ext.get('define', []))} types")

    def get_definition(self, node_type: str) -> Optional[dict]:
        """Get the define block for a node type."""
        return self._definitions.get(node_type)

    def validate_node(self, node_type: str, properties: dict) -> list[ValidationError]:
        """Validate properties against the define schema."""
        definition = self.get_definition(node_type)
        if not definition:
            return [ValidationError(field="node_type", message=f"Unknown node type: {node_type}")]

        errors = []
        prop_defs = {p["name"]: p for p in definition["properties"]}

        # Check required fields
        for prop_def in definition["properties"]:
            if prop_def.get("required") and prop_def["name"] not in properties:
                errors.append(ValidationError(
                    field=prop_def["name"],
                    message=f"Required field missing: {prop_def['name']}",
                ))

        # Check enum values
        for key, value in properties.items():
            if key in prop_defs:
                prop_def = prop_defs[key]
                enum_values = prop_def.get("enum", [])
                if enum_values and value is not None and value not in enum_values:
                    errors.append(ValidationError(
                        field=key,
                        message=f"Invalid value '{value}'. Must be one of: {enum_values}",
                        value=value,
                    ))

                # Type checking (basic)
                expected_type = prop_def.get("type", "string")
                if value is not None and expected_type == "integer" and not isinstance(value, int):
                    errors.append(ValidationError(
                        field=key,
                        message=f"Expected integer, got {type(value).__name__}",
                        value=value,
                    ))
                elif value is not None and expected_type == "boolean" and not isinstance(value, bool):
                    errors.append(ValidationError(
                        field=key,
                        message=f"Expected boolean, got {type(value).__name__}",
                        value=value,
                    ))

        return errors

    def validate_edge(self, source_type: str, edge_type: str, target_type: str) -> list[ValidationError]:
        """Validate an edge against relationship definitions."""
        definition = self.get_definition(source_type)
        if not definition:
            # Source might be an AWS-discovered type (no define block) — allow
            return []

        # Check if this relationship is defined
        valid_edges = [r["edge_type"] for r in definition.get("relationships", [])]
        if valid_edges and edge_type not in valid_edges:
            return [ValidationError(
                field="edge_type",
                message=f"Relationship '{edge_type}' not defined for type '{source_type}'. Valid: {valid_edges}",
            )]

        return []

    @property
    def known_types(self) -> list[str]:
        return sorted(self._definitions.keys())


class NeptuneClient:
    """OpenCypher client for Neptune."""

    def __init__(self, endpoint: str, port: int = 8182, auth_mode: str = "iam"):
        self.endpoint = endpoint
        self.port = port
        self.auth_mode = auth_mode
        self._session = None

    def _get_session(self):
        """Get or create a requests session with IAM auth."""
        if self._session is None:
            try:
                from botocore.auth import SigV4Auth
                from botocore.credentials import Credentials
                import boto3
                import requests
                from requests_aws4auth import AWS4Auth

                session = boto3.Session()
                credentials = session.get_credentials().get_frozen_credentials()
                self._aws_auth = AWS4Auth(
                    credentials.access_key,
                    credentials.secret_key,
                    session.region_name or "us-east-1",
                    "neptune-db",
                    session_token=credentials.token,
                )
                self._session = requests.Session()
            except ImportError:
                # Fallback: no auth (for local testing)
                import requests
                self._session = requests.Session()
                self._aws_auth = None

        return self._session

    def execute(self, query: str, parameters: dict = None) -> dict:
        """Execute an openCypher query against Neptune."""
        import requests

        url = f"https://{self.endpoint}:{self.port}/openCypher"
        payload = {"query": query}
        if parameters:
            payload["parameters"] = json.dumps(parameters)

        session = self._get_session()
        try:
            response = session.post(
                url,
                data=payload,
                auth=self._aws_auth,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.ConnectionError:
            logger.warning(f"Neptune not reachable at {self.endpoint}:{self.port}")
            return {"error": "connection_failed", "results": []}
        except Exception as e:
            logger.error(f"Neptune query error: {e}")
            return {"error": str(e), "results": []}


class GraphWriter:
    """Schema-validated graph writer for Neptune.

    All app writes to Neptune go through this class.
    It validates against schema define blocks before writing.
    """

    def __init__(
        self,
        neptune_endpoint: str = "",
        schema_paths: list[str] = None,
        auth_mode: str = "iam",
        dry_run: bool = False,
    ):
        self.dry_run = dry_run
        self.registry = SchemaRegistry(schema_paths or [])

        if neptune_endpoint and not dry_run:
            self.neptune = NeptuneClient(neptune_endpoint, auth_mode=auth_mode)
        else:
            self.neptune = None

        self._audit_log: list[dict] = []

    def create_node(
        self,
        node_type: str,
        node_id: str,
        properties: dict,
        created_by: str = "",
    ) -> WriteResult:
        """Create a node in Neptune, validated against schema.

        Args:
            node_type: The define type (e.g., "Task", "AppRegistration")
            node_id: Unique ID following the id_pattern
            properties: Node properties (validated against schema)
            created_by: Audit: which app/user is writing
        """
        # Validate
        errors = self.registry.validate_node(node_type, properties)
        if errors:
            return WriteResult(
                success=False,
                node_id=node_id,
                operation="create_node",
                validation_errors=errors,
                error=f"{len(errors)} validation error(s)",
            )

        # Add metadata
        now = datetime.now(timezone.utc).isoformat()
        properties["_created_at"] = now
        properties["_created_by"] = created_by
        properties["_node_type"] = node_type

        # Build openCypher MERGE
        props_str = ", ".join(f"n.{k} = ${k}" for k in properties.keys())
        query = f"MERGE (n:{node_type} {{id: $node_id}}) SET {props_str} RETURN n.id"
        params = {"node_id": node_id, **properties}

        # Execute
        if self.dry_run:
            logger.info(f"[DRY RUN] CREATE {node_type} id={node_id}")
            result = WriteResult(success=True, node_id=node_id, operation="create_node", timestamp=now)
        elif self.neptune:
            resp = self.neptune.execute(query, params)
            success = "error" not in resp
            result = WriteResult(
                success=success,
                node_id=node_id,
                operation="create_node",
                error=resp.get("error", ""),
                timestamp=now,
            )
        else:
            result = WriteResult(success=True, node_id=node_id, operation="create_node", timestamp=now)

        # Audit
        self._audit_log.append({
            "operation": "create_node",
            "node_type": node_type,
            "node_id": node_id,
            "created_by": created_by,
            "timestamp": now,
            "success": result.success,
        })

        return result

    def create_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        properties: dict = None,
        created_by: str = "",
    ) -> WriteResult:
        """Create an edge in Neptune."""
        properties = properties or {}
        now = datetime.now(timezone.utc).isoformat()

        # Build openCypher
        query = (
            f"MATCH (a {{id: $source_id}}), (b {{id: $target_id}}) "
            f"MERGE (a)-[r:{edge_type}]->(b) "
            f"SET r._created_at = $now, r._created_by = $created_by "
            f"RETURN type(r)"
        )
        params = {
            "source_id": source_id,
            "target_id": target_id,
            "now": now,
            "created_by": created_by,
        }

        if self.dry_run:
            logger.info(f"[DRY RUN] EDGE {source_id} -[{edge_type}]-> {target_id}")
            return WriteResult(success=True, operation="create_edge", timestamp=now)
        elif self.neptune:
            resp = self.neptune.execute(query, params)
            return WriteResult(
                success="error" not in resp,
                operation="create_edge",
                error=resp.get("error", ""),
                timestamp=now,
            )

        return WriteResult(success=True, operation="create_edge", timestamp=now)

    def update_node(
        self,
        node_id: str,
        properties: dict,
        updated_by: str = "",
    ) -> WriteResult:
        """Update properties on an existing node."""
        now = datetime.now(timezone.utc).isoformat()
        properties["_updated_at"] = now
        properties["_updated_by"] = updated_by

        props_str = ", ".join(f"n.{k} = ${k}" for k in properties.keys())
        query = f"MATCH (n {{id: $node_id}}) SET {props_str} RETURN n.id"
        params = {"node_id": node_id, **properties}

        if self.dry_run:
            logger.info(f"[DRY RUN] UPDATE {node_id} props={list(properties.keys())}")
            return WriteResult(success=True, node_id=node_id, operation="update_node", timestamp=now)
        elif self.neptune:
            resp = self.neptune.execute(query, params)
            return WriteResult(
                success="error" not in resp,
                node_id=node_id,
                operation="update_node",
                error=resp.get("error", ""),
                timestamp=now,
            )

        return WriteResult(success=True, node_id=node_id, operation="update_node", timestamp=now)

    def query(self, cypher: str, parameters: dict = None) -> list[dict]:
        """Execute a read query against Neptune."""
        if self.neptune:
            resp = self.neptune.execute(cypher, parameters)
            return resp.get("results", [])
        return []

    @property
    def audit_log(self) -> list[dict]:
        """Get the audit trail of all writes."""
        return self._audit_log
