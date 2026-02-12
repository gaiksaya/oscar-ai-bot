# OSCAR Knowledge Base Stack

This document describes the OSCAR Knowledge Base stack implementation, which provides automated document ingestion and vector search capabilities for the OSCAR AI assistant.

## Overview

The Knowledge Base stack creates and manages:

- **S3 Bucket**: Document storage with versioning and lifecycle policies
- **OpenSearch Serverless Collection**: Vector search engine with 1024-dimensional embeddings
- **Bedrock Knowledge Base**: Document ingestion pipeline with Amazon Titan 2.0 embeddings
- **GitHub Docs Uploader Lambda**: Daily sync of GitHub repositories to S3 (Docker-based)
- **Document Sync Lambda**: Automatic Knowledge Base ingestion trigger on S3 changes
- **IAM Roles & Policies**: Least-privilege access for all components

## Architecture

```
┌──────────────────────┐    Daily Schedule
│  GitHub Repositories │    (EventBridge)
│ opensearch-build     │         │
│ opensearch-build-... │         ▼
└──────────────────────┘   ┌─────────────────┐
                           │  Docs Uploader  │
                           │  Lambda (Docker)│
                           └────────┬─────────┘
                                    │ Upload
                                    ▼
                           ┌─────────────────┐
                           │   S3 Bucket     │
                           │  (Documents)    │
                           └────────┬─────────┘
                                    │ S3 Event
                                    ▼
                           ┌─────────────────┐
                           │  Document Sync  │
                           │     Lambda      │
                           └────────┬─────────┘
                                    │ Trigger Ingestion
                                    ▼
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────────┐
│  OpenSearch     │◄───│  Bedrock         │◄───│  Ingestion Job      │
│  Serverless     │    │  Knowledge Base  │    │  (S3 Data Source)   │
│  Collection     │    │  (Titan 2.0)     │    │                     │
└─────────────────┘    └──────────────────┘    └─────────────────────┘
         │                       │
         │ Vector Search         │ Queries
         ▼                       ▼
┌─────────────────────────────────────────────────┐
│          Bedrock Agents (Supervisor)            │
│  - Privileged Agent (full access)               │
│  - Limited Agent (read-only)                    │
└─────────────────────────────────────────────────┘
```

## Components

### S3 Document Storage

**Bucket Name**: `oscar-knowledge-docs-{env}-{account-id}-{region}`

**Features**:
- Versioning enabled for document history
- Server-side encryption (S3 managed keys)
- SSL enforcement for all requests
- Public access blocked for security
- Lifecycle policies:
  - Non-current versions deleted after 90 days
  - Objects transition to Infrequent Access after 30 days
  - Objects transition to Glacier after 90 days
  - Incomplete multipart uploads cleaned after 7 days

### OpenSearch Serverless Collection

**Collection Name**: `oscar-kb-cdk-{env}`

**Configuration**:
- **Type**: VECTORSEARCH
- **Vector Dimensions**: 1024 (Titan 2.0 embeddings)
- **Index Name**: `bedrock-knowledge-base-default-index`
- **Algorithm**: HNSW (Hierarchical Navigable Small World) with L2 distance
- **Standby Replicas**: DISABLED (cost optimization for non-prod)

**Security**:
- Encryption at rest with AWS owned keys
- Data access policy for Knowledge Base service role
- IAM-based access control

**Index Schema**:
```json
{
  "settings": {
    "index.knn": true
  },
  "mappings": {
    "properties": {
      "bedrock-knowledge-base-default-vector": {
        "type": "knn_vector",
        "dimension": 1024,
        "method": {
          "engine": "faiss",
          "space_type": "l2",
          "name": "hnsw"
        }
      },
      "AMAZON_BEDROCK_TEXT_CHUNK": {
        "type": "text"
      },
      "AMAZON_BEDROCK_METADATA": {
        "type": "text"
      }
    }
  }
}
```

### Bedrock Knowledge Base

**Name**: `oscar-kb-{env}`

**Configuration**:
- **Embedding Model**: Amazon Titan 2.0 (`amazon.titan-embed-text-v2:0`)
- **Vector Dimensions**: 1024
- **Storage**: OpenSearch Serverless
- **Chunking Strategy**: Fixed size
  - Max tokens per chunk: 300
  - Overlap percentage: 20%

**Field Mapping**:
- `vector_field`: `bedrock-knowledge-base-default-vector`
- `text_field`: `AMAZON_BEDROCK_TEXT_CHUNK`
- `metadata_field`: `AMAZON_BEDROCK_METADATA`

### GitHub Docs Uploader Lambda

**Function Name**: `oscar-kb-docs-uploader-{env}`

**Configuration**:
- **Runtime**: Docker container (for git operations)
- **Memory**: 512 MB
- **Timeout**: 300 seconds (5 minutes)
- **Schedule**: Daily (EventBridge cron)
- **Environment Variables**:
  - `BUCKET_NAME`: S3 bucket for documents
  - `GITHUB_REPOSITORIES`: Comma-separated list of repos

**GitHub Repositories** (configured in `app.py`):
- `opensearch-project/opensearch-build`
- `opensearch-project/opensearch-build-libraries`
- More can be added

**Process**:
1. Clone each GitHub repository
2. Extract documentation files (README, markdown files)
3. Upload to S3 bucket
4. Trigger Knowledge Base ingestion via sync Lambda

### Document Sync Lambda

**Function Name**: `oscar-kb-document-sync-{env}`

**Configuration**:
- **Runtime**: Python 3.12
- **Memory**: 256 MB
- **Timeout**: 300 seconds (5 minutes)
- **Trigger**: S3 bucket events (ObjectCreated, ObjectRemoved)

**Supported Events**:
- Document added to S3
- Document updated in S3
- Document deleted from S3

**Process**:
1. Receive S3 event notification
2. Extract bucket and object information
3. Trigger Bedrock Knowledge Base ingestion job
4. Log job status and details to CloudWatch

### IAM Roles & Policies

**Knowledge Base Service Role**:
- S3 GetObject and ListBucket on documents bucket
- Bedrock InvokeModel for embeddings
- OpenSearch Serverless APIAccessAll for index operations

**Lambda Execution Roles**:
- CloudWatch Logs write access
- S3 GetObject and PutObject (docs uploader)
- Bedrock StartIngestionJob (document sync)
- Knowledge Base read access

**OpenSearch Data Access Policy**:
- Knowledge Base service role granted full AOSS API access
- Scoped to specific collection

## Usage

### Initial Deployment

Deploy the Knowledge Base stack as part of the main OSCAR deployment:

```bash
cd /path/to/oscar-ai-bot
pipenv run cdk deploy OscarKnowledgeBaseStack-{env}
```

The stack will:
1. Create S3 bucket and OpenSearch collection
2. Create and configure Bedrock Knowledge Base
3. Deploy Lambda functions
4. Configure EventBridge schedule for daily sync

### Document Management

#### Automatic GitHub Sync

GitHub repositories are automatically synced daily via EventBridge schedule. The docs uploader Lambda:
- Runs once per day
- Clones configured repositories
- Uploads documentation to S3
- Triggers Knowledge Base ingestion

To manually trigger sync:
```bash
aws lambda invoke \
  --function-name oscar-kb-docs-uploader-dev \
  --invocation-type Event \
  response.json
```

#### Manual Document Upload

Upload documents directly to S3 (triggers automatic ingestion):

```bash
# Upload single document
aws s3 cp document.md s3://oscar-knowledge-docs-dev-{account}-{region}/

# Upload directory
aws s3 sync ./docs/ s3://oscar-knowledge-docs-dev-{account}-{region}/docs/
```

#### Check Ingestion Status

```bash
# Get Knowledge Base ID
KB_ID=$(aws bedrock-agent list-knowledge-bases \
  --query "knowledgeBaseSummaries[?name=='oscar-kb-dev'].knowledgeBaseId" \
  --output text)

# Get Data Source ID
DS_ID=$(aws bedrock-agent list-data-sources \
  --knowledge-base-id $KB_ID \
  --query "dataSourceSummaries[0].dataSourceId" \
  --output text)

# List recent ingestion jobs
aws bedrock-agent list-ingestion-jobs \
  --knowledge-base-id $KB_ID \
  --data-source-id $DS_ID \
  --max-results 5
```

### Querying the Knowledge Base

The Knowledge Base is automatically integrated with Bedrock agents. Query via Slack:

```
@oscar how do I build OpenSearch from source?
@oscar what are the release manager responsibilities?
@oscar explain the Jenkins job workflow
```

## Configuration

### Environment Variables

Set in `app.py` during stack initialization:

- `environment`: Deployment environment (dev/staging/prod)
- `github_repositories`: List of GitHub repos to sync

### Document Processing

**Chunking Strategy**:
- Fixed size chunks of 300 tokens
- 20% overlap between chunks
- Metadata preserved in each chunk

**Embedding Generation**:
- Amazon Titan 2.0 model
- 1024-dimensional vectors
- Stored in OpenSearch Serverless with HNSW index

## Monitoring

### CloudWatch Logs

**Log Groups**:
- `/aws/lambda/oscar-kb-docs-uploader-{env}` - GitHub sync logs
- `/aws/lambda/oscar-kb-document-sync-{env}` - Ingestion trigger logs

**Key Metrics**:
- Lambda invocations and errors
- Ingestion job success/failure rates
- S3 upload counts
- GitHub sync duration

### CloudWatch Alarms

Configure alarms for:
- Lambda function errors
- Ingestion job failures
- High S3 storage costs
- OpenSearch collection errors

### Useful Queries

```bash
# View recent sync logs
aws logs tail /aws/lambda/oscar-kb-docs-uploader-dev --follow

# View ingestion trigger logs
aws logs tail /aws/lambda/oscar-kb-document-sync-dev --follow

# List S3 bucket contents
aws s3 ls s3://oscar-knowledge-docs-dev-{account}-{region}/ --recursive

# Get Knowledge Base details
aws bedrock-agent get-knowledge-base --knowledge-base-id $KB_ID
```

## Troubleshooting

### Common Issues

**1. GitHub Sync Failures**

Check Lambda logs for errors:
```bash
aws logs tail /aws/lambda/oscar-kb-docs-uploader-dev --follow
```

Common causes:
- GitHub rate limiting (wait and retry)
- Invalid repository name (verify in `app.py`)
- IAM permissions missing (check Lambda execution role)

**2. Ingestion Job Stuck or Failed**

Check ingestion job status:
```bash
aws bedrock-agent list-ingestion-jobs \
  --knowledge-base-id $KB_ID \
  --data-source-id $DS_ID
```

Common causes:
- S3 bucket permissions incorrect
- OpenSearch collection not accessible
- Document format not supported
- Chunking configuration issues

**3. Documents Not Found in Search**

Verify document ingestion:
1. Check S3 bucket for document
2. Verify ingestion job completed successfully
3. Wait 2-5 minutes for indexing
4. Test search with specific terms from document

**4. OpenSearch Collection Access Denied**

Check data access policy:
```bash
aws opensearchserverless get-access-policy \
  --name oscar-kb-data-access-cdk-{env} \
  --type data
```

Ensure Knowledge Base service role is included in policy.

**5. Lambda Timeout During Sync**

Increase Lambda timeout in stack definition:
```python
timeout=Duration.seconds(600)  # Increase to 10 minutes
```

### Debug Commands

```bash
# Test Knowledge Base connectivity
aws bedrock-agent get-knowledge-base --knowledge-base-id $KB_ID

# Test OpenSearch collection
aws opensearchserverless batch-get-collection \
  --ids $(aws opensearchserverless list-collections \
    --query "collectionSummaries[?name=='oscar-kb-cdk-dev'].id" \
    --output text)

# Manually trigger ingestion
aws bedrock-agent start-ingestion-job \
  --knowledge-base-id $KB_ID \
  --data-source-id $DS_ID

# Check Knowledge Base vector count
aws opensearchserverless query \
  --collection-id $COLLECTION_ID \
  --query '{"query":{"match_all":{}},"size":0}'
```

## Integration

### With Bedrock Agents

Knowledge Base is automatically integrated with:
- **Privileged Supervisor Agent**: Full access to all knowledge
- **Limited Supervisor Agent**: Read-only access to all knowledge

Agents use the Knowledge Base for:
- Answering documentation questions
- Providing build/release guidance
- Explaining OpenSearch workflows
- Troubleshooting assistance

### Retrieval Configuration

Agents use these settings:
- **Search Type**: Vector similarity search
- **Top K Results**: 5 most relevant chunks
- **Score Threshold**: 0.7 minimum similarity
- **Metadata Filtering**: Optional (by document type, date, etc.)

### With OSCAR Components

- **Slack Bot**: Queries routed through supervisor agent
- **Metrics Functions**: Context-aware responses using knowledge
- **Jenkins Agent**: Access to build/release documentation

## Security

### Data Protection

- **Encryption at Rest**: All data encrypted (S3, OpenSearch)
- **Encryption in Transit**: SSL/TLS for all API calls
- **IAM Policies**: Least-privilege access
- **Network Isolation**: VPC endpoints for sensitive environments

### Access Control

- **S3 Bucket**: Block all public access, IAM-only
- **OpenSearch Collection**: Data access policy with service role
- **Knowledge Base**: Bedrock service role with scoped permissions
- **Lambda Functions**: Separate execution roles per function

### Compliance

- **Logging**: All access logged to CloudWatch
- **Versioning**: S3 versioning for audit trail
- **Retention**: 90-day retention for non-current versions
- **Deletion**: Automated cleanup of old data

## References

- [AWS Bedrock Knowledge Bases](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base.html)
- [OpenSearch Serverless](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless.html)
- [Amazon Titan Embeddings](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html)
- [S3 Lifecycle Policies](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lifecycle-mgmt.html)
