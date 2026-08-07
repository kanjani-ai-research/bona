"""Prompt Manager — load, render, and sync prompts to Bedrock Prompt Management.

Prompts are stored as YAML in frameworks/prompts/ with:
  - system prompt
  - user template (with {{variables}})
  - metadata (name, version, tags)

Sync to Bedrock: bona prompts sync --profile nw
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "frameworks" / "prompts"
if not PROMPTS_DIR.exists():
    PROMPTS_DIR = Path.cwd() / "frameworks" / "prompts"


def _load_yaml(path: Path) -> dict:
    """Load a YAML file."""
    text = path.read_text()
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:
        from .config import _parse_yaml
        return _parse_yaml(text)


def list_prompts() -> list[dict]:
    """List all available prompt templates."""
    prompts = []
    if not PROMPTS_DIR.exists():
        return prompts
    for f in sorted(PROMPTS_DIR.glob("*.yaml")):
        data = _load_yaml(f)
        prompts.append({
            "name": data.get("name", f.stem),
            "version": data.get("version", ""),
            "description": data.get("description", ""),
            "tags": data.get("tags", []),
            "file": str(f),
            "bedrock_prompt_id": data.get("bedrock_prompt_id", ""),
        })
    return prompts


def load_prompt(name: str) -> Optional[dict]:
    """Load a specific prompt template by name."""
    if not PROMPTS_DIR.exists():
        return None
    for f in PROMPTS_DIR.glob("*.yaml"):
        data = _load_yaml(f)
        if data.get("name") == name or f.stem == name:
            return data
    return None


def render_prompt(template: str, variables: dict[str, Any]) -> str:
    """Render a prompt template with variables.

    Supports {{variable}} and {{#if var}}...{{/if}} and {{#each list}}...{{/each}}
    """
    result = template

    # Handle {{#if var}}...{{/if}}
    def replace_if(match):
        var_name = match.group(1)
        content = match.group(2)
        if variables.get(var_name):
            return render_prompt(content, variables)
        return ""

    result = re.sub(r'\{\{#if (\w+)\}\}(.*?)\{\{/if\}\}', replace_if, result, flags=re.DOTALL)

    # Handle {{#each list}}...{{/each}}
    def replace_each(match):
        var_name = match.group(1)
        content = match.group(2)
        items = variables.get(var_name, [])
        if not isinstance(items, list):
            return ""
        output = ""
        for item in items:
            if isinstance(item, dict):
                rendered = content
                for k, v in item.items():
                    rendered = rendered.replace("{{" + k + "}}", str(v))
                output += rendered
            else:
                output += content.replace("{{this}}", str(item))
        return output

    result = re.sub(r'\{\{#each (\w+)\}\}(.*?)\{\{/each\}\}', replace_each, result, flags=re.DOTALL)

    # Handle simple {{variable}}
    for key, value in variables.items():
        if isinstance(value, (str, int, float, bool)):
            result = result.replace("{{" + key + "}}", str(value))
        elif isinstance(value, (list, dict)):
            result = result.replace("{{" + key + "}}", json.dumps(value, indent=2, default=str))

    return result


def sync_to_bedrock(session: boto3.Session, region: str) -> list[dict]:
    """Sync all prompt templates to Bedrock Prompt Management.

    Creates or updates prompts in Bedrock, returns list of sync results.
    """
    agent_client = session.client("bedrock-agent", region_name=region)
    results = []

    for prompt_info in list_prompts():
        prompt_data = load_prompt(prompt_info["name"])
        if not prompt_data:
            continue

        name = prompt_data.get("name", "")
        description = prompt_data.get("description", "")
        system_text = prompt_data.get("system", "")
        user_template = prompt_data.get("user_template", "")

        # Check if already synced
        existing_id = prompt_data.get("bedrock_prompt_id", "")

        try:
            if existing_id:
                # Update existing
                agent_client.update_prompt(
                    promptIdentifier=existing_id,
                    name=name,
                    description=description,
                    variants=[{
                        "name": "default",
                        "templateType": "TEXT",
                        "modelId": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                        "templateConfiguration": {
                            "text": {
                                "text": f"{system_text}\n\n{user_template}",
                            }
                        },
                    }],
                )
                results.append({"name": name, "status": "updated", "id": existing_id})
            else:
                # Create new
                resp = agent_client.create_prompt(
                    name=name,
                    description=description,
                    variants=[{
                        "name": "default",
                        "templateType": "TEXT",
                        "modelId": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                        "templateConfiguration": {
                            "text": {
                                "text": f"{system_text}\n\n{user_template}",
                            }
                        },
                    }],
                )
                new_id = resp.get("id", "")
                results.append({"name": name, "status": "created", "id": new_id})

                # Write back the ID to the YAML file
                if new_id:
                    _update_prompt_id(prompt_info["file"], new_id)

        except ClientError as e:
            results.append({"name": name, "status": "error", "error": str(e)[:100]})
            logger.warning("Failed to sync prompt %s: %s", name, e)

    logger.info("Synced %d prompts to Bedrock", len(results))
    return results


def _update_prompt_id(file_path: str, prompt_id: str):
    """Write the Bedrock prompt ID back to the YAML file."""
    path = Path(file_path)
    content = path.read_text()
    content = re.sub(
        r'bedrock_prompt_id:\s*"[^"]*"',
        f'bedrock_prompt_id: "{prompt_id}"',
        content
    )
    path.write_text(content)
