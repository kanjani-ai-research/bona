"""Bona Service — configuration from environment variables."""

import os
import logging

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Bona service configuration."""

    # Service identity
    service_name: str = "bona"
    port: int = 8080

    # Auth
    vouch_signing_key: str = ""
    auth_secret: str = ""
    auth_type: str = "env"  # "env" or "vlt"

    # AWS
    aws_region: str = "us-east-1"
    aws_profile: str = ""  # for local dev; EKS uses IRSA
    s3_bucket: str = ""
    export_prefix: str = "apg-exports"

    # Bona export settings
    max_resources: int = 10000
    resource_types: str = ""  # comma-separated filter (empty = all)
    enrich_mode: str = "soft"  # "soft", "hard", or "" (disabled)
    learn_mode: bool = True
    include_compliance: bool = False

    # Tenant
    tenant_id: str = "default"

    # Service catalog (client stack integration)
    service_catalog_table: str = ""
    service_catalog_domain: str = ""
    service_name_id: str = "bona"
    service_callbacks_to: str = ""

    class Config:
        env_prefix = ""
        case_sensitive = False


settings = Settings()
