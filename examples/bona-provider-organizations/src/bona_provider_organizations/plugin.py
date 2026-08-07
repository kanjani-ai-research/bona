"""Bona plugin for AWS Organizations resources."""

from botocore.exceptions import ClientError

from bona.plugins import hookimpl


def enrich_organizations(session, region, resources, refresh):
    """Enrich the graph with AWS Organizations resources.

    Discovers:
    - Organization root node
    - Account nodes
    - Organizational Unit (OU) nodes with CONTAINS_ACCOUNT edges
    - Service Control Policy (SCP) nodes with APPLIES_TO edges
    """
    client = session.client("organizations", region_name=region)

    try:
        # Describe the organization → Organization root node
        org_response = client.describe_organization()
        org = org_response["Organization"]
        resources.add_node(
            "Organization",
            org["Id"],
            {
                "arn": org["Arn"],
                "master_account_id": org["MasterAccountId"],
                "master_account_email": org.get("MasterAccountEmail", ""),
                "feature_set": org.get("FeatureSet", ""),
            },
        )
    except ClientError:
        # Organizations API not available or not enabled
        return

    # List accounts → Account nodes
    try:
        paginator = client.get_paginator("list_accounts")
        for page in paginator.paginate():
            for account in page["Accounts"]:
                resources.add_node(
                    "Account",
                    account["Id"],
                    {
                        "arn": account["Arn"],
                        "name": account.get("Name", ""),
                        "email": account.get("Email", ""),
                        "status": account.get("Status", ""),
                        "joined_method": account.get("JoinedMethod", ""),
                    },
                )
    except ClientError:
        pass

    # List roots to discover OUs
    try:
        roots_response = client.list_roots()
        root_ids = [r["Id"] for r in roots_response.get("Roots", [])]
    except ClientError:
        root_ids = []

    # List organizational units for each root → OU nodes + CONTAINS_ACCOUNT edges
    for root_id in root_ids:
        try:
            paginator = client.get_paginator("list_organizational_units_for_parent")
            for page in paginator.paginate(ParentId=root_id):
                for ou in page["OrganizationalUnits"]:
                    resources.add_node(
                        "OrganizationalUnit",
                        ou["Id"],
                        {
                            "arn": ou["Arn"],
                            "name": ou.get("Name", ""),
                        },
                    )

                    # List accounts under this OU and create CONTAINS_ACCOUNT edges
                    try:
                        children_paginator = client.get_paginator("list_accounts_for_parent")
                        for children_page in children_paginator.paginate(ParentId=ou["Id"]):
                            for account in children_page["Accounts"]:
                                resources.add_edge(
                                    "CONTAINS_ACCOUNT",
                                    ou["Id"],
                                    account["Id"],
                                )
                    except ClientError:
                        pass
        except ClientError:
            pass

    # List policies (SCP type) → SCP nodes + APPLIES_TO edges
    try:
        paginator = client.get_paginator("list_policies")
        for page in paginator.paginate(Filter="SERVICE_CONTROL_POLICY"):
            for policy in page["Policies"]:
                policy_id = policy["Id"]
                resources.add_node(
                    "ServiceControlPolicy",
                    policy_id,
                    {
                        "arn": policy["Arn"],
                        "name": policy.get("Name", ""),
                        "description": policy.get("Description", ""),
                        "type": policy.get("Type", ""),
                        "aws_managed": policy.get("AwsManaged", False),
                    },
                )

                # Find targets this policy is attached to
                try:
                    targets_paginator = client.get_paginator("list_targets_for_policy")
                    for targets_page in targets_paginator.paginate(PolicyId=policy_id):
                        for target in targets_page["Targets"]:
                            resources.add_edge(
                                "APPLIES_TO",
                                policy_id,
                                target["TargetId"],
                            )
                except ClientError:
                    pass
    except ClientError:
        pass


@hookimpl
def bona_reference_providers():
    """Register the organizations enrichment provider."""
    return [enrich_organizations]
