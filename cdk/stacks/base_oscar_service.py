#!/usr/bin/env python
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.
"""
Base abstract class for OSCAR service stacks.
Each service (Jenkins, Metrics, etc.) extends this to create a complete stack.
"""

from abc import ABC, abstractmethod
from aws_cdk import Stack, aws_lambda as lambda_, aws_bedrock as bedrock, aws_iam as iam, CfnOutput
from typing import List, Optional, Dict
from dataclasses import dataclass


@dataclass
class ServiceMetadata:
    """Metadata for service identification and documentation."""
    name: str
    version: str
    description: str


class BaseOscarService(Stack, ABC):
    """
    Abstract base stack for OSCAR services.
    Each service implements the creation methods for their specific needs.
    """
    
    def __init__(self, scope, construct_id: str, env_name: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        self.env_name = env_name
        
        # Call abstract methods - each service implements these
        self.lambda_execution_role = self.create_lambda_execution_role()
        self.lambda_function = self.create_lambda_function()
        self.action_groups = self.create_action_groups()
        self.bedrock_agent = self.create_bedrock_agent()
        self.agent_alias = self.create_agent_alias()
        
        # Create outputs
        self.create_outputs()
    
    @abstractmethod
    def get_metadata(self) -> ServiceMetadata:
        """Return service metadata."""
        pass
    
    @abstractmethod
    def create_lambda_execution_role(self) -> iam.Role:
        """Create and return the Lambda execution role with service-specific permissions."""
        pass
    
    @abstractmethod
    def create_lambda_function(self) -> lambda_.Function:
        """Create and return the Lambda function for this service."""
        pass
    
    @abstractmethod
    def create_action_groups(self) -> List[bedrock.CfnAgent.AgentActionGroupProperty]:
        """Create and return action groups for this service."""
        pass
    
    @abstractmethod
    def create_bedrock_agent(self) -> bedrock.CfnAgent:
        """Create and return the Bedrock agent for this service."""
        pass
    
    def create_agent_alias(self) -> bedrock.CfnAgentAlias:
        """Create agent alias - same for all services, so not abstract."""
        return bedrock.CfnAgentAlias(
            self, "ServiceAgentAlias",
            agent_alias_name="LIVE",
            agent_id=self.bedrock_agent.attr_agent_id,
            description=f"Live alias for {self.get_metadata().name} agent"
        )
    
    def create_outputs(self) -> None:
        """Create stack outputs - can be overridden if needed."""
        metadata = self.get_metadata()
        
        # Standard outputs that most services will want
        CfnOutput(
            self, "LambdaFunctionArn",
            value=self.lambda_function.function_arn,
            description=f"{metadata.name} Lambda function ARN",
            export_name=f"Oscar{metadata.name.title()}LambdaArn"
        )
        
        CfnOutput(
            self, "AgentId", 
            value=self.bedrock_agent.attr_agent_id,
            description=f"{metadata.name} Bedrock agent ID",
            export_name=f"Oscar{metadata.name.title()}AgentId"
        )
        
        CfnOutput(
            self, "AgentAliasId",
            value=self.agent_alias.attr_agent_alias_id, 
            description=f"{metadata.name} agent alias ID",
            export_name=f"Oscar{metadata.name.title()}AgentAliasId"
        )
        
        CfnOutput(
            self, "AgentAliasArn",
            value=self.agent_alias.attr_agent_alias_arn,
            description=f"{metadata.name} agent alias ARN", 
            export_name=f"Oscar{metadata.name.title()}AgentAliasArn"
        )
