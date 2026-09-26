# Multi-Account Setup

Bona supports multi-account discovery via [AWS Config Aggregators](https://docs.aws.amazon.com/config/latest/developerguide/aggregate-data.html).

## How It Works

When you pass `--aggregator`, Bona uses `select_aggregate_resource_config` instead of `select_resource_config`. This queries resources across all accounts and regions enrolled in the aggregator — producing a unified graph from a single command.

```bash
bona export --profile management --region us-east-1 --aggregator MyOrgAggregator
```

Each node retains its original `account_id` and `region`, so you can filter or group by account in downstream analysis.

---

## Setting Up an Aggregator

### Organization Aggregator (Recommended)

If you use AWS Organizations, create an organization-wide aggregator in your management account:

```bash
aws configservice put-configuration-aggregator \
  --configuration-aggregator-name MyOrgAggregator \
  --organization-aggregation-source '{
    "RoleArn": "arn:aws:iam::MANAGEMENT_ACCOUNT:role/OrgConfigRole",
    "AllAwsRegions": true
  }' \
  --region us-east-1
```

The role needs `organizations:ListAccounts` and `organizations:ListAWSServiceAccessForOrganization` permissions.

### Account-Based Aggregator

For non-Organizations setups, specify individual accounts:

```bash
aws configservice put-configuration-aggregator \
  --configuration-aggregator-name MyAggregator \
  --account-aggregation-sources '[
    {
      "AccountIds": ["111111111111", "222222222222", "333333333333"],
      "AllAwsRegions": true
    }
  ]' \
  --region us-east-1
```

Each source account must authorize the aggregator:

```bash
# Run in each source account
aws configservice put-aggregation-authorization \
  --authorized-account-id MANAGEMENT_ACCOUNT_ID \
  --authorized-aws-region us-east-1
```

---

## IAM Permissions

The profile running Bona needs:

```json
{
  "Effect": "Allow",
  "Action": "config:SelectAggregateResourceConfig",
  "Resource": "arn:aws:config:us-east-1:MANAGEMENT_ACCOUNT:config-aggregator/MyOrgAggregator"
}
```

---

## Usage Examples

```bash
# All resources across the organization
bona export --profile management --region us-east-1 \
  --aggregator MyOrgAggregator

# Filter to specific types across all accounts
bona export --profile management --region us-east-1 \
  --aggregator MyOrgAggregator \
  --types "AWS::EC2::*,AWS::S3::*"

# Large org — increase the cap
bona export --profile management --region us-east-1 \
  --aggregator MyOrgAggregator \
  --max-resources 50000
```

---

## Output Differences

With an aggregator, the graph contains resources from multiple accounts. Each node's `account_id` field identifies which account owns it. The `stable_id` includes the account, ensuring uniqueness across accounts:

```json
{
  "id": "arn:aws:ec2:us-east-1:111111111111:instance/i-abc",
  "account_id": "111111111111",
  "stable_id": "aws|111111111111|us-east-1|AWS::EC2::Instance|i-abc"
}
```

Cross-account edges (if resources reference each other) are preserved via ARN-based IDs.

---

## Verify Aggregator Status

```bash
aws configservice describe-configuration-aggregators --region us-east-1

aws configservice describe-aggregation-authorizations --region us-east-1
```

Ensure all source accounts show `SUCCEEDED` in aggregation status before running Bona.
