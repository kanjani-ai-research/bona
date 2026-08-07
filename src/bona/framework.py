"""Bona Framework — deploy compliance conformance packs.

Reads available frameworks from frameworks/frameworks.yaml catalog.
No hardcoded framework list — add new ones by editing the YAML.
"""

import logging
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

FRAMEWORKS_DIR = Path(__file__).parent.parent / "frameworks"
# If running from installed package, check relative to CWD or home
if not FRAMEWORKS_DIR.exists():
    FRAMEWORKS_DIR = Path.cwd() / "frameworks"
if not FRAMEWORKS_DIR.exists():
    FRAMEWORKS_DIR = Path(__file__).parent.parent.parent / "frameworks"
CATALOG_FILE = FRAMEWORKS_DIR / "frameworks.yaml"


def _load_catalog() -> dict:
    """Load the frameworks catalog from YAML."""
    if not CATALOG_FILE.exists():
        logger.warning("Frameworks catalog not found: %s", CATALOG_FILE)
        return {}

    text = CATALOG_FILE.read_text()

    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError:
        # Basic YAML parsing fallback
        from .config import _parse_yaml
        return _parse_yaml(text)


def get_catalog() -> dict:
    """Get the full catalog metadata."""
    return _load_catalog()


def list_frameworks() -> list[dict]:
    """List available frameworks from the catalog."""
    catalog = _load_catalog()
    frameworks = catalog.get("frameworks", {})
    return [
        {
            "name": name,
            "version": info.get("version", ""),
            "description": info.get("description", ""),
            "rules_count": info.get("rules_count", 0),
            "tags": info.get("tags", []),
            "has_template": bool(info.get("local_file")),
            "docs": info.get("docs", []),
        }
        for name, info in frameworks.items()
    ]


def deploy_framework(session: boto3.Session, region: str, framework_name: str) -> dict:
    """Deploy a conformance pack for the given framework."""
    catalog = _load_catalog()
    frameworks = catalog.get("frameworks", {})

    if framework_name not in frameworks:
        available = list(frameworks.keys())
        return {"error": f"Unknown framework: {framework_name}. Available: {available}"}

    info = frameworks[framework_name]
    local_file = info.get("local_file", "")

    if not local_file:
        template_name = info.get("template", "")
        return {"error": f"Template not downloaded yet. Run: bona framework download --name {framework_name}"}

    template_path = FRAMEWORKS_DIR / "conformance-packs" / local_file

    if not template_path.exists():
        return {"error": f"Template file missing: {template_path}. Run: bona framework download --name {framework_name}"}

    template_body = template_path.read_text()
    pack_name = f"bona-{framework_name}"

    config_client = session.client("config", region_name=region)

    try:
        config_client.put_conformance_pack(
            ConformancePackName=pack_name,
            TemplateBody=template_body,
        )
        logger.info("Deployed conformance pack: %s (%s)", pack_name, info.get("description", ""))
        return {"status": "deployed", "pack_name": pack_name, "framework": framework_name, "version": info.get("version", "")}
    except ClientError as e:
        return {"error": str(e)}


def download_framework(framework_name: str) -> dict:
    """Download a conformance pack template from GitHub."""
    catalog = _load_catalog()
    frameworks = catalog.get("frameworks", {})
    source = catalog.get("source", {})

    if framework_name not in frameworks:
        return {"error": f"Unknown framework: {framework_name}"}

    info = frameworks[framework_name]
    template_name = info.get("template", "")
    local_file = info.get("local_file", "")

    if not template_name:
        return {"error": f"No template defined for {framework_name}"}

    repo = source.get("repo", "https://github.com/awslabs/aws-config-rules")
    path = source.get("path", "aws-config-conformance-packs")
    branch = source.get("branch", "master")

    url = f"https://raw.githubusercontent.com/{repo.replace('https://github.com/', '')}/{branch}/{path}/{template_name}"

    import urllib.request
    out_dir = FRAMEWORKS_DIR / "conformance-packs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / (local_file or template_name)

    try:
        urllib.request.urlretrieve(url, str(out_path))
        size = out_path.stat().st_size
        if size < 100:
            out_path.unlink()
            return {"error": f"Download failed (file too small). URL: {url}"}
        logger.info("Downloaded: %s (%d bytes)", out_path.name, size)
        return {"status": "downloaded", "file": str(out_path), "size": size}
    except Exception as e:
        return {"error": f"Download failed: {e}"}


def get_framework_status(session: boto3.Session, region: str) -> list[dict]:
    """Get status and compliance scores for deployed conformance packs."""
    config_client = session.client("config", region_name=region)
    results = []

    try:
        resp = config_client.describe_conformance_packs()
        packs = resp.get("ConformancePackDetails", [])

        scores = {}
        try:
            score_resp = config_client.list_conformance_pack_compliance_scores()
            for s in score_resp.get("ConformancePackComplianceScores", []):
                scores[s["ConformancePackName"]] = s.get("Score", "N/A")
        except ClientError:
            pass

        for pack in packs:
            name = pack.get("ConformancePackName", "")
            results.append({
                "name": name,
                "status": "ACTIVE",
                "score": scores.get(name, "evaluating..."),
                "id": pack.get("ConformancePackId", ""),
            })
    except ClientError as e:
        logger.warning("Failed to get conformance pack status: %s", e)

    return results
