# Developer Guide

This project contains the AWS CDK infrastructure code for OSCAR (OpenSearch Conversational AI Release Assistant).

## 🏗️ Infrastructure Components

The CDK deploys:
- **Lambda Functions**: All OSCAR agent implementations
- **DynamoDB Table**: Session and context management
- **IAM Roles & Policies**: Security and permissions
- **API Gateway**: Slack integration endpoint
- **Secrets Manager**: Centralized configuration
- **Bedrock Agents**: Main as well as collaborator bedrock agents. 

## 📋 CDK Stacks

| Stack | Purpose | Resources |
|-------|---------|-----------|
| `OscarPermissionsStack` | IAM roles and policies | Bedrock agent role, Lambda execution roles (base, Jenkins, metrics), API Gateway role |
| `OscarSecretsStack` | Configuration management | Central secret (`oscar-central-env-{env}`) with all environment variables |
| `OscarStorageStack` | Data persistence | DynamoDB table for session/context/deduplication with TTL and monitoring |
| `OscarVpcStack` | Networking | VPC, security groups, VPC endpoints (S3, DynamoDB, Secrets Manager) |
| `OscarKnowledgeBaseStack` | Bedrock Knowledge Base | S3 bucket, OpenSearch Serverless collection, document sync Lambda |
| `OscarLambdaStack` | Compute functions | Supervisor agent, Jenkins agent, metrics agent (VPC-enabled) |
| `OscarApiGatewayStack` | Slack integration | REST API (`POST /slack/events`) with Lambda proxy integration |
| `OscarAgentsStack` | Bedrock Agents | Supervisor agents (privileged & limited), collaborator agents (Jenkins, Build, Test, Release) |

_Please note: Stacks have dependencies on each other and needs to be deployed in a specific order. Check app.py for more details_
## 🔨 Build Tools

### Python 3.12

Python projects in this repository use Python 3.12. See the [Python Beginners Guide](https://wiki.python.org/moin/BeginnersGuide) if you have never worked with the language.
```bash
$ python --version
Python 3.12.12
```
### Pipenv
This project uses [pipenv](https://pipenv.pypa.io/en/latest/), which is typically installed with `pip install --user pipenv` or use what fits your local OS. Pipenv automatically creates and manages a virtualenv for your projects, as well as adds/removes packages from your `Pipfile` as you install/uninstall packages. It also generates the ever-important `Pipfile.lock`, which is used to produce deterministic builds.

```bash
$ pipenv --version
pipenv, version 2026.0.3
```
### Install Dependencies

```bash
$ pipenv install
To activate this project's virtualenv, run pipenv shell.
Alternatively, run a command inside the virtualenv with pipenv run.
Installing dependencies from Pipfile.lock (6657ff)...
```

## ⚙️ Configuration

### Environment Setup

The project uses AWS Secrets Manager to store configuration. Create a secret named `oscar-central-env-{env}` (e.g., `oscar-central-env-dev`) with all environment variables.

### Slack App Configuration
- Go to https://api.slack.com/apps
- Go to OAuth and Permissions. Get Bot OAuth Token
- Next, select 'Event Subscriptions' on left panel
- Set Request URL to your API Gateway endpoint (Example: https://api-gateway-url/prod/slack/events)
- Subscribe to bot events (message.channels, app_mention)
- Install app to workspace
- 
### Required Variables

**Critical Configuration** (must be in Secrets Manager):

```bash
# Slack Integration
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_SIGNING_SECRET=your-signing-secret

# Authorization (comma-separated Slack user IDs)
FULLY_AUTHORIZED_USERS=U12345678,U87654321
DM_AUTHORIZED_USERS=U12345678
CHANNEL_ALLOW_LIST=C12345678,C87654321
```

**See `.env.example`** for the complete list of 135+ optional configuration variables.


## 🔧 Key Files

| File | Purpose                         |
|------|---------------------------------|
| `app.py` | CDK application entry point     |
| `.env` | Configuration and env variables | 
| `stacks/` | CDK stack definitions           |
| `lambda/` | Lambda function source code     |

## 🚀 Deployment

**In order to deploy all the stacks**:
```bash
# From project root
pipenv run cdk deploy "*"

```
**Deploy each stack individually**:
```bash
pipenv run cdk deploy <stack_name>
```

## 🧹 Cleanup

**Remove all resources**:
```bash
pipenv run cdk destroy --all
```

**Note**: Some resources like S3 buckets may need manual cleanup if they contain data.

**Useful Commands**:
```bash
cdk ls                    # List all stacks
cdk diff                  # Show changes
cdk synth                 # Generate CloudFormation
cdk doctor                # Check CDK setup
```