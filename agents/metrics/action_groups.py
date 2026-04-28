# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Bedrock action group definitions for metrics agent."""

from typing import List

from aws_cdk import aws_bedrock as bedrock


def get_action_groups(lambda_arn: str) -> List[bedrock.CfnAgent.AgentActionGroupProperty]:
    return [
        bedrock.CfnAgent.AgentActionGroupProperty(
            action_group_name="metricsActionGroup",
            description="Unified metrics analysis for builds, tests, release readiness, and general OpenSearch index queries",
            action_group_state="ENABLED",
            action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(lambda_=lambda_arn),
            function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                functions=[
                    bedrock.CfnAgent.FunctionProperty(
                        name="query_metrics",
                        description="Query metrics data using natural language. Automatically routes to the appropriate data source (build results, test results, or release metrics) based on query content.",
                        parameters={
                            "query": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Natural language query about metrics (e.g., 'Failed components for 3.5.0', 'What tests are failing on linux for 3.6.0 version?', 'Release readiness for OpenSearch-Dashboards')",
                                required=True,
                            ),
                            "version": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="OpenSearch version to scope the query (e.g., '3.2.0', '2.18.0')",
                                required=True,
                            ),
                            "memory_id": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Memory ID from a previous query_metrics response. Pass this to maintain conversational context with the search agent across follow-up queries.",
                                required=False,
                            ),
                        },
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="query_opensearch",
                        description=(
                            "Query any OpenSearch index using natural language via an agentic search pipeline. "
                            "Use this for non-release data such as GitHub activity events (github-user-activity-events-*), "
                            "pull requests (github_pulls), or any other index in the metrics cluster. "
                            "The agentic pipeline translates the natural language query to DSL and executes it."
                        ),
                        parameters={
                            "query": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description=(
                                    "Natural language query describing what data to retrieve. Reference specific field names "
                                    "when possible (e.g., 'top senders by event count from github-user-activity-events', "
                                    "'pull requests where merged=true grouped by user_login from github_pulls')"
                                ),
                                required=True,
                            ),
                            "index": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description=(
                                    "Target OpenSearch index name or pattern "
                                    "(e.g., 'github-user-activity-events-04-2026', 'github_pulls', "
                                    "'github-user-activity-events-*')"
                                ),
                                required=True,
                            ),
                            "pipeline": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description=(
                                    "Name of the agentic search pipeline to use. "
                                    "Defaults to the configured pipeline if not specified."
                                ),
                                required=False,
                            ),
                        },
                    ),
                ]
            ),
        )
    ]
