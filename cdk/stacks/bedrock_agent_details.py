#!/usr/bin/env python
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
"""
Bedrock Agent SSM Parameter Store configuration.

This module provides SSM parameter paths for Bedrock agents to avoid circular imports.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BedrockAgentDetails:
    """SSM Parameter Store paths for Bedrock agent IDs and aliases."""
    jenkins_agent_id: str
    jenkins_agent_alias: str
    metrics_build_agent_id: str
    metrics_build_agent_alias: str
    metrics_test_agent_id: str
    metrics_test_agent_alias: str
    metrics_release_agent_id: str
    metrics_release_agent_alias: str
    supervisor_agent_id: str
    supervisor_agent_alias: str
    limited_supervisor_agent_id: str
    limited_supervisor_agent_alias: str


def get_ssm_param_paths(env: str) -> BedrockAgentDetails:
    """
    Get SSM parameter paths for agent IDs and aliases for a given environment.
    
    Args:
        env: Environment name (e.g., 'dev', 'prod')
        
    Returns:
        BedrockAgentParams with all SSM parameter paths
    """
    return BedrockAgentDetails(
        jenkins_agent_id=f"/oscar/{env}/bedrock/jenkins-agent-id",
        jenkins_agent_alias=f"/oscar/{env}/bedrock/jenkins-agent-alias",
        metrics_build_agent_id=f"/oscar/{env}/bedrock/metrics-build-agent-id",
        metrics_build_agent_alias=f"/oscar/{env}/bedrock/metrics-build-agent-alias",
        metrics_test_agent_id=f"/oscar/{env}/bedrock/metrics-test-agent-id",
        metrics_test_agent_alias=f"/oscar/{env}/bedrock/metrics-test-agent-alias",
        metrics_release_agent_id=f"/oscar/{env}/bedrock/metrics-release-agent-id",
        metrics_release_agent_alias=f"/oscar/{env}/bedrock/metrics-release-agent-alias",
        supervisor_agent_id=f"/oscar/{env}/bedrock/supervisor-agent-id",
        supervisor_agent_alias=f"/oscar/{env}/bedrock/supervisor-agent-alias",
        limited_supervisor_agent_id=f"/oscar/{env}/bedrock/limited-supervisor-agent-id",
        limited_supervisor_agent_alias=f"/oscar/{env}/bedrock/limited-supervisor-agent-alias",
    )
