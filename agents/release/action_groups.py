# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Bedrock action group definitions for the release agent."""

from typing import List

from aws_cdk import aws_bedrock as bedrock


def get_action_groups(lambda_arn: str) -> List[bedrock.CfnAgent.AgentActionGroupProperty]:
    return [
        _release_action_group(lambda_arn),
    ]


def _release_action_group(
    lambda_arn: str,
) -> bedrock.CfnAgent.AgentActionGroupProperty:
    """Read-only actions for querying release readiness state and the release window."""
    return bedrock.CfnAgent.AgentActionGroupProperty(
        action_group_name="releaseActions",
        description=(
            "Query per-criterion release readiness state and the release window for "
            "OpenSearch versions."
        ),
        action_group_state="ENABLED",
        action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(lambda_=lambda_arn),
        function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
            functions=[
                bedrock.CfnAgent.FunctionProperty(
                    name="get_release_status",
                    description=(
                        "Get the current release readiness state for a version: the "
                        "per-criterion statuses (met / not_met / in_progress / unknown) and "
                        "a Red/Yellow/Green verdict with reasoning. Use for any question about "
                        "whether a release is ready, what is blocking it, or its criteria status."
                    ),
                    parameters={
                        "version": bedrock.CfnAgent.ParameterDetailProperty(
                            type="string",
                            description=(
                                "Three-part release version to report on (e.g., '3.8.0'). "
                                "Required."
                            ),
                            required=True,
                        ),
                    },
                ),
                bedrock.CfnAgent.FunctionProperty(
                    name="get_release_window",
                    description=(
                        "Get the release schedule for a version: RC date, release date, days "
                        "remaining, and the current cadence phase. Use for questions about when "
                        "a release is due or how much time remains."
                    ),
                    parameters={
                        "version": bedrock.CfnAgent.ParameterDetailProperty(
                            type="string",
                            description="Three-part release version (e.g., '3.8.0'). Required.",
                            required=True,
                        ),
                    },
                ),
            ]
        ),
    )
