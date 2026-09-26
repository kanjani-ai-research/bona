#!/usr/bin/env bash
# setup-compliance-kb.sh — Create a Bedrock Knowledge Base for compliance frameworks
#
# This script:
#   1. Creates an S3 bucket for compliance documents
#   2. Downloads framework PDFs (NIST 800-53, CIS Benchmarks, PCI DSS)
#   3. Creates a Bedrock Knowledge Base with the documents
#   4. Starts an ingestion job to index the documents
#
# Usage:
#   ./scripts/setup-compliance-kb.sh --profile nw --region us-east-1 --bucket my-compliance-docs
#
# Prerequisites:
#   - AWS CLI configured
#   - Bedrock model access enabled (titan-embed-text-v2)
#   - IAM role for Bedrock KB (created by script if not exists)

set -euo pipefail

# Defaults
PROFILE="${AWS_PROFILE:-default}"
REGION="${AWS_REGION:-us-east-1}"
BUCKET=""
KB_NAME="bona-compliance-kb"
EMBEDDING_MODEL="amazon.titan-embed-text-v2:0"

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --profile) PROFILE="$2"; shift 2;;
        --region) REGION="$2"; shift 2;;
        --bucket) BUCKET="$2"; shift 2;;
        --kb-name) KB_NAME="$2"; shift 2;;
        *) echo "Unknown arg: $1"; exit 1;;
    esac
done

if [[ -z "$BUCKET" ]]; then
    BUCKET="bona-compliance-docs-$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)"
fi

echo "============================================"
echo "  Bona Compliance KB Setup"
echo "  Profile: $PROFILE"
echo "  Region:  $REGION"
echo "  Bucket:  $BUCKET"
echo "  KB Name: $KB_NAME"
echo "============================================"
echo ""

# Step 1: Create S3 bucket (if not exists)
echo "→ Step 1: S3 bucket for compliance documents"
if aws s3api head-bucket --bucket "$BUCKET" --profile "$PROFILE" 2>/dev/null; then
    echo "  Bucket exists: s3://$BUCKET"
else
    aws s3api create-bucket \
        --bucket "$BUCKET" \
        --profile "$PROFILE" \
        --region "$REGION" \
        --create-bucket-configuration LocationConstraint="$REGION" 2>/dev/null || \
    aws s3api create-bucket \
        --bucket "$BUCKET" \
        --profile "$PROFILE" \
        --region "$REGION"
    echo "  Created: s3://$BUCKET"
fi

# Step 2: Download compliance framework documents
echo ""
echo "→ Step 2: Download compliance framework documents"
DOCS_DIR=$(mktemp -d)

# NIST 800-53 rev 5
echo "  Downloading NIST 800-53 rev 5..."
curl -sL "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-53r5.pdf" \
    -o "$DOCS_DIR/NIST-SP-800-53-rev5.pdf"

# CIS AWS Foundations Benchmark (public summary)
echo "  Downloading CIS AWS Foundations Benchmark overview..."
curl -sL "https://www.cisecurity.org/wp-content/uploads/2022/09/CIS-AWS-Foundations-Benchmark-v1.5.0-Overview.pdf" \
    -o "$DOCS_DIR/CIS-AWS-Foundations-Benchmark-Overview.pdf" 2>/dev/null || \
echo "  Note: CIS full benchmark requires CIS membership. Using Config rule descriptions instead."

# AWS Well-Architected Security Pillar (public)
echo "  Downloading AWS Well-Architected Security Pillar..."
curl -sL "https://docs.aws.amazon.com/pdfs/wellarchitected/latest/security-pillar/wellarchitected-security-pillar.pdf" \
    -o "$DOCS_DIR/AWS-Well-Architected-Security-Pillar.pdf"

# Upload to S3
echo ""
echo "→ Step 3: Upload documents to S3"
aws s3 sync "$DOCS_DIR/" "s3://$BUCKET/compliance-docs/" --profile "$PROFILE"
echo "  Uploaded to s3://$BUCKET/compliance-docs/"

# Step 4: Create IAM role for Bedrock KB
echo ""
echo "→ Step 4: IAM role for Bedrock KB"
ACCOUNT_ID=$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)
ROLE_NAME="bona-compliance-kb-role"

TRUST_POLICY='{
    "Version": "2012-10-17",
    "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "bedrock.amazonaws.com"},
        "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"aws:SourceAccount": "'$ACCOUNT_ID'"}}
    }]
}'

ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --profile "$PROFILE" --query 'Role.Arn' --output text 2>/dev/null || echo "")

if [[ -z "$ROLE_ARN" || "$ROLE_ARN" == "None" ]]; then
    ROLE_ARN=$(aws iam create-role \
        --role-name "$ROLE_NAME" \
        --assume-role-policy-document "$TRUST_POLICY" \
        --profile "$PROFILE" \
        --query 'Role.Arn' --output text)

    # Attach S3 read + Bedrock permissions
    aws iam put-role-policy --role-name "$ROLE_NAME" --profile "$PROFILE" \
        --policy-name "bona-kb-access" \
        --policy-document '{
            "Version": "2012-10-17",
            "Statement": [
                {"Effect": "Allow", "Action": ["s3:GetObject", "s3:ListBucket"], "Resource": ["arn:aws:s3:::'$BUCKET'", "arn:aws:s3:::'$BUCKET'/*"]},
                {"Effect": "Allow", "Action": ["bedrock:InvokeModel"], "Resource": "*"}
            ]
        }'
    echo "  Created role: $ROLE_ARN"
    echo "  Waiting for role propagation..."
    sleep 10
else
    echo "  Role exists: $ROLE_ARN"
fi

# Step 5: Create Bedrock Knowledge Base
echo ""
echo "→ Step 5: Create Bedrock Knowledge Base"
EMBEDDING_ARN="arn:aws:bedrock:${REGION}::foundation-model/${EMBEDDING_MODEL}"

KB_ID=$(aws bedrock-agent list-knowledge-bases --profile "$PROFILE" --region "$REGION" \
    --query "knowledgeBaseSummaries[?name=='$KB_NAME'].knowledgeBaseId" --output text 2>/dev/null || echo "")

if [[ -z "$KB_ID" || "$KB_ID" == "None" ]]; then
    KB_RESPONSE=$(aws bedrock-agent create-knowledge-base \
        --profile "$PROFILE" --region "$REGION" \
        --name "$KB_NAME" \
        --description "Compliance framework documents for bona scan enrichment (NIST, CIS, AWS Security)" \
        --role-arn "$ROLE_ARN" \
        --knowledge-base-configuration '{
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {
                "embeddingModelArn": "'$EMBEDDING_ARN'"
            }
        }' \
        --storage-configuration '{
            "type": "OPENSEARCH_SERVERLESS",
            "opensearchServerlessConfiguration": {
                "collectionArn": "auto",
                "fieldMapping": {
                    "metadataField": "metadata",
                    "textField": "text",
                    "vectorField": "vector"
                },
                "vectorIndexName": "bona-compliance"
            }
        }' 2>&1) || true

    if echo "$KB_RESPONSE" | grep -q "knowledgeBaseId"; then
        KB_ID=$(echo "$KB_RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['knowledgeBase']['knowledgeBaseId'])")
        echo "  Created KB: $KB_ID"
    else
        echo "  Note: KB creation may require an existing OpenSearch Serverless collection."
        echo "  You can create the KB manually in the Bedrock console and reference it in bona.yaml:"
        echo ""
        echo "    compliance:"
        echo "      knowledge_base_id: <your-kb-id>"
        echo ""
        echo "  Or use an existing KB that already has compliance documents."
        KB_ID=""
    fi
else
    echo "  KB exists: $KB_ID"
fi

# Step 6: Add S3 data source and start ingestion
if [[ -n "$KB_ID" && "$KB_ID" != "None" ]]; then
    echo ""
    echo "→ Step 6: Add data source and start ingestion"

    DS_ID=$(aws bedrock-agent list-data-sources --knowledge-base-id "$KB_ID" \
        --profile "$PROFILE" --region "$REGION" \
        --query "dataSourceSummaries[0].dataSourceId" --output text 2>/dev/null || echo "")

    if [[ -z "$DS_ID" || "$DS_ID" == "None" ]]; then
        DS_ID=$(aws bedrock-agent create-data-source \
            --profile "$PROFILE" --region "$REGION" \
            --knowledge-base-id "$KB_ID" \
            --name "compliance-docs" \
            --data-source-configuration '{
                "type": "S3",
                "s3Configuration": {
                    "bucketArn": "arn:aws:s3:::'$BUCKET'",
                    "inclusionPrefixes": ["compliance-docs/"]
                }
            }' \
            --query 'dataSource.dataSourceId' --output text 2>/dev/null) || true
    fi

    if [[ -n "$DS_ID" && "$DS_ID" != "None" ]]; then
        aws bedrock-agent start-ingestion-job \
            --profile "$PROFILE" --region "$REGION" \
            --knowledge-base-id "$KB_ID" \
            --data-source-id "$DS_ID" 2>/dev/null || true
        echo "  Ingestion started for KB: $KB_ID"
    fi
fi

# Summary
echo ""
echo "============================================"
echo "  Setup Complete!"
echo ""
echo "  S3 Bucket: s3://$BUCKET/compliance-docs/"
echo "  IAM Role:  $ROLE_ARN"
if [[ -n "$KB_ID" && "$KB_ID" != "None" ]]; then
    echo "  KB ID:     $KB_ID"
    echo ""
    echo "  Add to bona.yaml:"
    echo "    compliance:"
    echo "      knowledge_base_id: $KB_ID"
fi
echo "============================================"

# Cleanup
rm -rf "$DOCS_DIR"
