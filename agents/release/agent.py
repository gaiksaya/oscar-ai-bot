# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Release-readiness agent for OSCAR — the advisory "brain" for OpenSearch releases."""

import os

from agents.base_agent import (LambdaConfig, MonitoringConfig,  # noqa: F401
                               OscarAgent, SecretConfig)
from agents.release.action_groups import get_action_groups
from agents.release.iam_policies import get_policies
from agents.release.instructions import (AGENT_INSTRUCTION,
                                         COLLABORATOR_INSTRUCTION)

# The release-state indices (opensearch_release_state, opensearch_release_schedule)
# live on the metrics cluster, but the release agent keeps its OWN secret + its own
# cross-account role env var (rather than sharing the metrics agent's) so the blast
# radius of a compromise stays scoped to this one agent. RELEASE_STATE_SECRET_NAME is
# injected automatically by the CDK from the declared secret in get_secrets().
_ENV_KEYS = [
    "RELEASE_STATE_CROSS_ACCOUNT_ROLE_ARN",
    "OPENSEARCH_REGION",
    "OPENSEARCH_SERVICE",
    "OPENSEARCH_REQUEST_TIMEOUT",
    "RELEASE_STATE_INDEX",
    "RELEASE_SCHEDULE_INDEX",
]


def _passthrough_env(keys):
    """Pass through env vars to Lambda — only if set."""
    return {k: os.environ[k] for k in keys if k in os.environ}


class ReleaseAgent(OscarAgent):

    @property
    def name(self):
        return "release"

    def get_lambda_config(self):
        return LambdaConfig(
            entry="agents/release/lambda",
            timeout_seconds=180,
            memory_size=1024,
            reserved_concurrency=10,
            needs_vpc=True,
            environment_variables=_passthrough_env(_ENV_KEYS),
        )

    def get_iam_policies(self, account_id, region, env):
        return get_policies(account_id, region, env)

    def get_action_groups(self, lambda_arn):
        return get_action_groups(lambda_arn)

    def get_agent_instruction(self):
        return AGENT_INSTRUCTION

    def get_collaborator_instruction(self):
        return COLLABORATOR_INSTRUCTION

    def get_collaborator_name(self):
        return "Release-Specialist"

    def get_access_level(self):
        # Read-only status/window actions are safe for both the limited and privileged supervisors.
        # Decision capture (a privileged, 2PR-guarded write) is deferred to Phase 4.
        return "both"

    def uses_knowledge_base(self):
        return False

    def get_secrets(self):
        # Own secret (oscar-release-env-{env}) holding OPENSEARCH_HOST, so read
        # access and blast radius stay scoped to this agent's Lambda role. The CDK
        # auto-creates it, grants read to only the release role, and injects
        # RELEASE_STATE_SECRET_NAME into the Lambda env.
        return [
            SecretConfig(
                name_suffix="env",
                description="Release agent secrets (OpenSearch metrics-cluster host, etc.)",
                env_var="RELEASE_STATE_SECRET_NAME",
            ),
        ]

    def get_managed_policies(self):
        # AWSLambdaVPCAccessExecutionRole is required because needs_vpc=True — it
        # grants the ec2 ENI permissions Lambda uses to attach to the VPC at cold
        # start (same pairing as the metrics agent, which reaches the same cluster).
        return [
            "service-role/AWSLambdaBasicExecutionRole",
            "service-role/AWSLambdaVPCAccessExecutionRole",
        ]

    def get_monitoring_config(self):
        # TODO: Re-enable after first deployment so the log group exists.
        return []
