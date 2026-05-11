# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Bedrock action group definitions for metrics agent."""

from typing import List

from aws_cdk import aws_bedrock as bedrock


def get_action_groups(lambda_arn: str) -> List[bedrock.CfnAgent.AgentActionGroupProperty]:
    return [
        bedrock.CfnAgent.AgentActionGroupProperty(
            action_group_name="metricsActionGroup",
            description="Unified metrics analysis for builds, tests, and release readiness",
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
                ]
            ),
        ),
        bedrock.CfnAgent.AgentActionGroupProperty(
            action_group_name="agenticSearchActionGroup",
            description="Direct agentic search against any OpenSearch index using a flow agent pipeline for NL-to-DSL translation",
            action_group_state="ENABLED",
            action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(lambda_=lambda_arn),
            function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                functions=[
                    bedrock.CfnAgent.FunctionProperty(
                        name="agentic_query",
                        description="Execute a natural language query against a specific OpenSearch index. The query is translated to DSL by a flow agent pipeline. Use this for GitHub data (issues, PRs, activity events) or any index where you know the exact index name.",
                        parameters={
                            "query": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Natural language query (e.g., 'Show all closed issues with label github-request and maintainer in title for April 2026')",
                                required=True,
                            ),
                            "index": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="OpenSearch index name to query (e.g., 'github_issues', 'github_pulls', 'github-user-activity-events-04-2026')",
                                required=True,
                            ),
                            "pipeline": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Search pipeline name for NL-to-DSL translation. Defaults to 'oscar-flow-agentic-pipeline' if not specified.",
                                required=False,
                            ),
                        },
                    ),
                ]
            ),
        ),
        bedrock.CfnAgent.AgentActionGroupProperty(
            action_group_name="directSearchActionGroup",
            description="Execute explicit OpenSearch DSL queries directly against a specified index and return raw JSON. No NL-to-DSL translation. Use for deterministic or high-performance queries.",
            action_group_state="ENABLED",
            action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(lambda_=lambda_arn),
            function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                functions=[
                    bedrock.CfnAgent.FunctionProperty(
                        name="direct_query",
                        description="Execute a raw OpenSearch DSL query against a specific index and return the full JSON response. No LLM translation, no summarization — the caller provides DSL, the Lambda returns JSON.",
                        parameters={
                            "index": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="OpenSearch index name to query (e.g., 'github_pulls', 'github_issues', 'github-user-activity-events-04-2026'). Also accepts comma-separated lists and wildcards.",
                                required=True,
                            ),
                            "query_body": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="The OpenSearch query DSL as a JSON string. Example: '{\"size\":0,\"query\":{\"bool\":{\"filter\":[{\"range\":{\"created_at\":{\"gte\":\"2026-04-01\",\"lt\":\"2026-05-01\"}}}]}},\"aggs\":{\"by_user\":{\"terms\":{\"field\":\"user_login.keyword\",\"size\":10000}}}}'",
                                required=True,
                            ),
                        },
                    ),
                ]
            ),
        ),
    ]
