"""OSCAL Catalog — machine-readable NIST 800-53 control lookups.

Loads the OSCAL JSON catalog for direct control-to-description mapping
without needing LLM calls for basic lookups.

Usage:
    from bona.oscal import load_catalog, get_control
    catalog = load_catalog()
    control = get_control(catalog, "AC-2")
    print(control["title"])  # "Account Management"
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

OSCAL_DIR = Path(__file__).parent.parent / "frameworks" / "oscal"
if not OSCAL_DIR.exists():
    OSCAL_DIR = Path.cwd() / "frameworks" / "oscal"

NIST_CATALOG = OSCAL_DIR / "NIST_SP-800-53_rev5_catalog.json"


def load_catalog(path: Optional[str] = None) -> dict:
    """Load the OSCAL NIST 800-53 catalog. Returns {control_id: {title, description, group}}."""
    catalog_path = Path(path) if path else NIST_CATALOG
    if not catalog_path.exists():
        logger.warning("OSCAL catalog not found: %s", catalog_path)
        return {}

    data = json.loads(catalog_path.read_text())
    catalog = data.get("catalog", {})
    groups = catalog.get("groups", [])

    controls = {}
    for group in groups:
        group_id = group.get("id", "")
        group_title = group.get("title", "")
        for control in group.get("controls", []):
            _extract_control(control, group_id, group_title, controls)

    logger.info("Loaded OSCAL catalog: %d controls from %d groups", len(controls), len(groups))
    return controls


def _extract_control(control: dict, group_id: str, group_title: str, controls: dict):
    """Recursively extract a control and its enhancements."""
    control_id = control.get("id", "").upper()
    title = control.get("title", "")

    # Extract prose description from parts
    description = ""
    for part in control.get("parts", []):
        if part.get("name") == "statement":
            description = _extract_prose(part)
            break

    controls[control_id] = {
        "id": control_id,
        "title": title,
        "description": description[:500],
        "group_id": group_id,
        "group_title": group_title,
        "class": control.get("class", ""),
    }

    # Recurse into control enhancements
    for enhancement in control.get("controls", []):
        _extract_control(enhancement, group_id, group_title, controls)


def _extract_prose(part: dict) -> str:
    """Extract prose text from an OSCAL part (recursively)."""
    prose = part.get("prose", "")
    for sub in part.get("parts", []):
        sub_prose = _extract_prose(sub)
        if sub_prose:
            prose += " " + sub_prose
    return prose.strip()


def get_control(catalog: dict, control_id: str) -> Optional[dict]:
    """Look up a control by ID (case-insensitive, handles AC-2(1) and AC-2.1 formats)."""
    cid = control_id.upper()
    if cid in catalog:
        return catalog[cid]
    # Try dot format: AC-2(1) → AC-2.1
    cid_dot = cid.replace("(", ".").replace(")", "")
    if cid_dot in catalog:
        return catalog[cid_dot]
    return None


def search_controls(catalog: dict, query: str, limit: int = 5) -> list[dict]:
    """Search controls by title or description text."""
    query_lower = query.lower()
    results = []
    for control in catalog.values():
        score = 0
        if query_lower in control["title"].lower():
            score += 2
        if query_lower in control["description"].lower():
            score += 1
        if score > 0:
            results.append({**control, "_score": score})
    results.sort(key=lambda x: x["_score"], reverse=True)
    return results[:limit]


def map_rule_to_controls(catalog: dict, rule_name: str, rule_description: str = "") -> list[dict]:
    """Heuristic mapping of Config rule name to NIST controls.

    Uses keyword matching on rule name → control descriptions.
    Returns top matches. For precise mapping, use the LLM.
    """
    # Common keyword → control family mapping
    KEYWORD_MAP = {
        "encrypt": ["SC-28", "SC-8", "SC-13"],
        "ssl": ["SC-8", "SC-23"],
        "tls": ["SC-8", "SC-23"],
        "password": ["IA-5", "AC-7"],
        "access-key": ["AC-2", "IA-5"],
        "access_key": ["AC-2", "IA-5"],
        "mfa": ["IA-2"],
        "public": ["AC-3", "AC-4", "SC-7"],
        "logging": ["AU-2", "AU-3", "AU-6"],
        "cloudtrail": ["AU-2", "AU-3", "AU-12"],
        "cloud-trail": ["AU-2", "AU-3", "AU-12"],
        "cloud_trail": ["AU-2", "AU-3", "AU-12"],
        "trail": ["AU-2", "AU-3"],
        "backup": ["CP-9", "CP-10"],
        "vpc": ["SC-7", "AC-4"],
        "security-group": ["AC-4", "SC-7"],
        "security_group": ["AC-4", "SC-7"],
        "iam": ["AC-2", "AC-3", "AC-6"],
        "root": ["AC-6", "AC-2"],
        "rotation": ["IA-5", "SC-12"],
        "rotated": ["IA-5", "SC-12"],
        "versioning": ["CP-9", "SI-7"],
        "kms": ["SC-12", "SC-13", "SC-28"],
        "waf": ["SC-7", "SI-3"],
        "guardduty": ["SI-4", "IR-4"],
        "config": ["CM-2", "CM-6", "CM-8"],
        "readonly": ["AC-6", "AC-3", "CM-7"],
        "read-only": ["AC-6", "AC-3", "CM-7"],
        "read_only": ["AC-6", "AC-3", "CM-7"],
        "container": ["CM-7", "AC-6", "SC-7"],
        "ecs": ["CM-7", "AC-6", "SI-7"],
        "lambda": ["AC-3", "AC-6", "SC-7"],
        "s3": ["AC-3", "AC-4", "SC-28"],
        "bucket": ["AC-3", "AC-4", "SC-28"],
        "rds": ["SC-28", "AC-3", "SC-8"],
        "db": ["SC-28", "AC-3"],
        "secret": ["SC-28", "IA-5"],
        "monitor": ["SI-4", "AU-6"],
        "alarm": ["SI-4", "IR-4"],
        "flow-log": ["AU-2", "AU-3", "SI-4"],
        "flow_log": ["AU-2", "AU-3", "SI-4"],
    }

    matches = []
    rule_lower = rule_name.lower().replace("_", "-").replace(" ", "-")

    for keyword, control_ids in KEYWORD_MAP.items():
        if keyword in rule_lower or keyword in rule_description.lower():
            for cid in control_ids:
                control = get_control(catalog, cid)
                if control and control not in matches:
                    matches.append(control)

    return matches[:5]
