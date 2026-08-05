# Configuration

## Prerequisites

Bona reads from AWS Config. You need:

1. **AWS Config recorder** enabled in your target region(s)
2. **IAM permissions** for the Bona user/role
3. **Python 3.11+** with `boto3`

---

## AWS Config Setup

### Enable the Config Recorder

If AWS Config isn't already enabled, set it up via the console or CLI:

```bash
# Create a Config recorder (records all resource types)
aws configservice put-configuration-recorder \
  --configuration-recorder name=default,roleARN=arn:aws:iam::ACCOUNT:role/ConfigRole \
  --recording-group allSupported=true,includeGlobalResourceTypes=true

# Create a delivery channel (required for recorder to work)
aws configservice put-delivery-channel \
  --delivery-channel name=default,s3BucketName=my-config-bucket

# Start recording
aws configservice start-configuration-recorder --configuration-recorder-name default
```

### Verify Config is Recording

```bash
aws configservice describe-configuration-recorder-status
```

Expected output shows `recording: true` and `lastStatus: SUCCESS`.

### Resource Types

By default, AWS Config records all supported resource types. To limit recording:

```bash
aws configservice put-configuration-recorder \
  --configuration-recorder name=default,roleARN=arn:aws:iam::ACCOUNT:role/ConfigRole \
  --recording-group '{
    "allSupported": false,
    "resourceTypes": [
      "AWS::EC2::Instance",
      "AWS::EC2::SecurityGroup",
      "AWS::S3::Bucket",
      "AWS::IAM::Role"
    ]
  }'
```

> **Note**: Bona can only discover resource types that Config is recording. If you filter Config's recording, Bona's output will be limited to those types.

---

## IAM Permissions

### Minimum Policy for `bona export`

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "config:SelectResourceConfig",
        "config:ListDiscoveredResources",
        "config:BatchGetResourceConfig",
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    }
  ]
}
```

### Additional: Multi-account (aggregator)

```json
{
  "Effect": "Allow",
  "Action": "config:SelectAggregateResourceConfig",
  "Resource": "arn:aws:config:*:*:config-aggregator/*"
}
```

### Additional: History command

```json
{
  "Effect": "Allow",
  "Action": "config:GetResourceConfigHistory",
  "Resource": "*"
}
```

### Additional: Compliance findings

```json
{
  "Effect": "Allow",
  "Action": [
    "config:DescribeComplianceByResource",
    "config:GetComplianceDetailsByResource",
    "config:DescribeConfigRules"
  ],
  "Resource": "*"
}
```

### Additional: S3 upload

```json
{
  "Effect": "Allow",
  "Action": "s3:PutObject",
  "Resource": "arn:aws:s3:::my-bucket/bona/*"
}
```

---

## AWS Credentials

Bona uses the standard boto3 credential chain:

1. Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`)
2. `--profile` flag (maps to `~/.aws/credentials`)
3. AWS config file (`~/.aws/config`)
4. EC2 instance metadata / ECS task role / Lambda execution role

### Using Named Profiles

```ini
# ~/.aws/credentials
[prod]
aws_access_key_id = AKIA...
aws_secret_access_key = ...

[management]
aws_access_key_id = AKIA...
aws_secret_access_key = ...
```

```bash
bona export --profile prod --region us-east-1
```

### Using SSO

```ini
# ~/.aws/config
[profile prod]
sso_start_url = https://myorg.awsapps.com/start
sso_region = us-east-1
sso_account_id = 123456789012
sso_role_name = ReadOnlyAccess
region = us-east-1
```

```bash
aws sso login --profile prod
bona export --profile prod --region us-east-1
```

---

## Config Advanced Query

Bona primarily uses AWS Config's **Advanced Query** feature (`select_resource_config`). This requires:

- AWS Config recorder enabled
- At least one successful delivery (resources must be recorded)
- The account must support Config SQL (all commercial regions do)

If Advanced Query is unavailable (permissions, region), Bona falls back to `list_discovered_resources` + `batch_get_resource_config`, which is slower but always works.

---

## Troubleshooting

### "No resources found"

1. Verify Config is recording: `aws configservice describe-configuration-recorder-status`
2. Check that resources exist: `aws configservice list-discovered-resources --resource-type AWS::EC2::Instance`
3. Ensure your profile/region are correct

### "AccessDenied"

Add the required IAM permissions above.

### "InvalidExpression"

Advanced Query might not support a specific SQL syntax in your region. Bona will automatically fall back to the list+batch method. Use `--verbose` to see the fallback in action.
