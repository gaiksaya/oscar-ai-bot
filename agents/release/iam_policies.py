# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""IAM policies for the release agent.

The release agent is READ-ONLY. It reads the release-state indices on the
metrics cluster via its OWN cross-account assumed role (RELEASE_STATE_CROSS_ACCOUNT_ROLE_ARN),
kept distinct from the metrics agent's role so blast radius stays scoped to this
agent. No cluster-write and no Jenkins-trigger permissions are granted here;
decision capture is deferred to Phase 4.
"""

import os
from typing import List

from aws_cdk import aws_iam as iam


def get_policies(account_id: str, region: str, env: str) -> List[iam.PolicyStatement]:
    policies: List[iam.PolicyStatement] = []

    # Assume the release agent's own cross-account role to sign OpenSearch reads.
    # CloudWatch Logs access comes from the AWSLambdaBasicExecutionRole managed
    # policy; read access to this agent's secret is granted by the CDK wiring
    # (from the declared get_secrets()).
    release_account_role = os.environ.get("RELEASE_STATE_CROSS_ACCOUNT_ROLE_ARN")
    if release_account_role:
        policies.append(
            iam.PolicyStatement(
                sid="CrossAccountOpenSearchAssumeRole",
                effect=iam.Effect.ALLOW,
                actions=["sts:AssumeRole"],
                resources=[release_account_role],
            )
        )

    return policies
