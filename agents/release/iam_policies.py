# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""IAM policies for the release agent."""

import os
from typing import List

from aws_cdk import aws_iam as iam


def get_policies(account_id: str, region: str, env: str) -> List[iam.PolicyStatement]:
    policies = [
        iam.PolicyStatement(
            sid="ReleaseSecretsAccess",
            effect=iam.Effect.ALLOW,
            actions=["secretsmanager:GetSecretValue"],
            resources=[
                f"arn:aws:secretsmanager:{region}:{account_id}:secret:oscar-release-*-{env}*"
            ],
        ),
        iam.PolicyStatement(
            sid="ReleaseLogsAccess",
            effect=iam.Effect.ALLOW,
            actions=[
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
            ],
            resources=[
                f"arn:aws:logs:{region}:{account_id}:log-group:/aws/lambda/oscar-release-*"
            ],
        ),
    ]

    cross_account_role = os.environ.get("RELEASE_STATE_CROSS_ACCOUNT_ROLE_ARN")
    if cross_account_role:
        policies.append(
            iam.PolicyStatement(
                sid="CrossAccountOpenSearchAssumeRole",
                effect=iam.Effect.ALLOW,
                actions=["sts:AssumeRole"],
                resources=[cross_account_role],
            )
        )

    return policies
