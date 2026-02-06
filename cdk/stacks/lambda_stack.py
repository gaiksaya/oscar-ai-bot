#!/usr/bin/env python
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
"""
Lambda stack for OSCAR infrastructure.

This module defines all Lambda functions used by OSCAR including:
- Main OSCAR agent with Slack event processing
- Communication handler for Bedrock action groups
- Jenkins agent for CI/CD integration
- Multiple metrics agents for different data sources
"""

import logging
from typing import Dict, Any, Optional
from aws_cdk import (
    Stack,
    Duration,
    aws_lambda,
    aws_iam as iam,
    CfnOutput
)
from aws_cdk.aws_lambda_python_alpha import PythonFunction
from constructs import Construct
import os

from .bedrock_agent_details import get_ssm_param_paths

# Configure logging
logger = logging.getLogger(__name__)

class OscarLambdaStack(Stack):
    """
    Comprehensive Lambda resources for OSCAR infrastructure.
    
    This construct creates and configures all Lambda functions used by OSCAR:
    - Main OSCAR agent with Slack event processing capabilities
    - Communication handler for Bedrock action group integration
    - Jenkins agent for CI/CD operations
    - Multiple metrics agents for different data sources (test, build, release, deployment)
    """

    SUPERVISOR_AGENT_LAMBDA_FUNCTION_NAME = 'oscar-supervisor-agent'
    COMMUNICATION_HANDLER_LAMBDA_FUNCTION_NAME = 'oscar-communication-handler'
    JENKINS_AGENT_LAMBDA_FUNCTION_NAME = 'oscar-jenkins-operations'
    METRICS_AGENT_LAMBDA_FUNCTION_NAME = 'oscar-metrics-agent'


    @classmethod
    def get_supervisor_agent_function_name(cls, env: str) -> str:
        """Get the main agent Lambda function name"""
        return f"{cls.SUPERVISOR_AGENT_LAMBDA_FUNCTION_NAME}-{env}"

    @classmethod
    def get_communication_handler_lambda_function_name(cls, env: str) -> str:
        """Get the communication handler Lambda function name"""
        return f"{cls.COMMUNICATION_HANDLER_LAMBDA_FUNCTION_NAME}-{env}"

    @classmethod
    def get_jenkins_agent_lambda_function_name(cls, env: str) -> str:
        """Get the jenkins Lambda function name"""
        return f"{cls.JENKINS_AGENT_LAMBDA_FUNCTION_NAME}-{env}"

    @classmethod
    def get_metrics_agent_lambda_function_name(cls, env: str) -> str:
        """Get the metrics Lambda function name"""
        return f"{cls.METRICS_AGENT_LAMBDA_FUNCTION_NAME}-{env}"

    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        permissions_stack: Any,
        secrets_stack: Any,
        storage_stack: Any,
        environment: str,
        vpc_stack: Optional[Any] = None,
        **kwargs
    ) -> None:
        """
        Initialize Lambda resources.
        
        Args:
            scope: The CDK construct scope
            construct_id: The ID of the construct
            permissions_stack: The permissions stack with IAM roles
            secrets_stack: The secrets stack with central environment secret
            vpc_stack: Optional VPC stack for VPC-enabled functions
            **kwargs: Additional keyword arguments
        """
        super().__init__(scope, construct_id, **kwargs)
        
        # Store references to other stacks
        self.storage_stack = storage_stack
        self.permissions_stack = permissions_stack
        self.secrets_stack = secrets_stack
        self.vpc_stack = vpc_stack
        self.env_name = environment
        
        # Dictionary to store all Lambda functions
        self.lambda_functions: Dict[str, PythonFunction] = {}
        
        # Create all Lambda functions
        self._create_supervisor_agent_lambda()
        self._create_communication_handler_lambda()
        self._create_jenkins_agent_lambda()
        self._create_metrics_agent_lambda()
        
        # Add outputs for important resources
        self._add_outputs()
    
    def _create_supervisor_agent_lambda(self) -> None:
        """
        Create the main OSCAR agent Lambda function with Slack event processing capabilities.
        """
        # Get the base Lambda execution role from permissions stack
        execution_role = self.permissions_stack.lambda_execution_roles["base"]
        
        # Grant access to central environment secret
        self.secrets_stack.grant_read_access(execution_role)
        
        function = PythonFunction(
            self, "MainOscarAgentLambda",
            function_name=self.get_supervisor_agent_function_name(self.env_name),
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="lambda_handler",
            entry="lambda/oscar-agent",
            index="app.py",
            timeout=Duration.seconds(300),  # 5 minutes for complex agent interactions
            memory_size=1024,  # Higher memory for better performance
            environment=self._get_main_agent_environment_variables(),
            role=execution_role,
            description="Main OSCAR agent with Slack event processing capabilities",
            reserved_concurrent_executions=10  # Limit concurrent executions
        )
        # Grant Bedrock permission to invoke this Lambda
        function.add_permission(
            "AllowBedrockInvoke",
            principal=iam.ServicePrincipal("bedrock.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_account=self.account
        )

        function.add_permission(
            "SelfInvoke",
            principal=iam.ServicePrincipal("lambda.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_arn=function.function_arn
        )
        self.lambda_functions[self.get_supervisor_agent_function_name(self.env_name)] = function

    def _create_communication_handler_lambda(self) -> None:
        """
        Create the communication handler Lambda function for Bedrock action groups.
        """
        # Get the communication handler execution role from permissions stack
        execution_role = self.permissions_stack.lambda_execution_roles["communication"]
        
        # Grant access to central environment secret
        self.secrets_stack.grant_read_access(execution_role)
        
        function = PythonFunction(
            self, "CommunicationHandlerLambda",
            function_name=self.get_communication_handler_lambda_function_name(self.env_name),
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="lambda_handler",
            entry="lambda/oscar-communication-handler",
            index="lambda_function.py",
            timeout=Duration.seconds(60),
            memory_size=512,
            environment=self._get_communication_handler_environment_variables(),
            role=execution_role,
            description="Communication handler for OSCAR Bedrock action groups",
            reserved_concurrent_executions=20  # Higher concurrency for action groups
        )
        
        # Grant Bedrock permission to invoke this Lambda
        function.add_permission(
            "AllowBedrockInvoke",
            principal=iam.ServicePrincipal("bedrock.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_account=self.account
        )
        
        self.lambda_functions[self.get_communication_handler_lambda_function_name(self.env_name)] = function

    def _create_jenkins_agent_lambda(self) -> None:
        """
        Create the Jenkins agent Lambda function for CI/CD integration.
        """
        logger.info("Creating Jenkins agent Lambda function")

        # Get the Jenkins execution role from permissions stack
        execution_role = self.permissions_stack.lambda_execution_roles["jenkins"]

        # Grant access to central environment secret
        self.secrets_stack.grant_read_access(execution_role)

        function = PythonFunction(
            self, "JenkinsAgentLambda",
            function_name=self.get_jenkins_agent_lambda_function_name(self.env_name),
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="lambda_handler",
            entry="lambda/jenkins",
            index="lambda_function.py",
            timeout=Duration.seconds(120),  # 2 minutes for Jenkins API calls
            memory_size=512,
            environment=self._get_jenkins_agent_environment_variables(),
            role=execution_role,
            description="Jenkins agent for OSCAR CI/CD operations",
            reserved_concurrent_executions=5  # Limited concurrency for Jenkins operations
        )
        
        # Grant Bedrock permission to invoke this Lambda
        function.add_permission(
            "AllowBedrockInvoke",
            principal=iam.ServicePrincipal("bedrock.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_account=self.account
        )

        self.lambda_functions[self.get_jenkins_agent_lambda_function_name(self.env_name)] = function

    def _create_metrics_agent_lambda(self) -> None:
        """
        Create all metrics agent Lambda functions with VPC configuration.
        """

        # Get the VPC execution role from permissions stack (references existing authorized role)
        execution_role = self.permissions_stack.lambda_execution_roles["metrics"]

        # Grant access to central environment secret
        self.secrets_stack.grant_read_access(execution_role)

        # Get VPC configuration for metrics agents (they need VPC access for OpenSearch)
        vpc = self.vpc_stack.vpc
        security_group = self.vpc_stack.lambda_security_group

        function = PythonFunction(
            self, "MetricsAgentLambda",
            function_name=self.get_metrics_agent_lambda_function_name(self.env_name),
            runtime=aws_lambda.Runtime.PYTHON_3_12,
            handler="lambda_handler",
            entry="lambda/metrics",
            index="lambda_function.py",
            timeout=Duration.seconds(180),  # 3 minutes for metrics queries
            memory_size=1024,  # Higher memory for data processing
            environment=self._get_metrics_agent_environment_variables(),
            role=execution_role,
            description=f"OSCAR Metrics Agent (VPC-enabled)",
            reserved_concurrent_executions=100,  # Limited concurrency for metrics
            vpc=vpc,
            security_groups=[security_group],
            allow_public_subnet=True  # Allow placement in public subnets when no private subnets available
            )
        
        # Grant Bedrock permission to invoke this Lambda
        function.add_permission(
            "AllowBedrockInvoke",
            principal=iam.ServicePrincipal("bedrock.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_account=self.account
        )

        self.lambda_functions[self.get_metrics_agent_lambda_function_name(self.env_name)] = function

    def _get_main_agent_environment_variables(self) -> Dict[str, str]:
        """
        Get environment variables for the main OSCAR agent Lambda function.
        
        Returns:
            Dictionary of environment variables for the main agent
        """

        params = get_ssm_param_paths(self.env_name)
        return {
            # Central secret reference - Lambda will load from Secrets Manager at runtime
            "CENTRAL_SECRET_NAME": self.secrets_stack.central_env_secret.secret_name,

            # DynamoDB table name
            "CONTEXT_TABLE_NAME": self.storage_stack.context_table_name,

            # Parameter store paths with agent configs
            "OSCAR_PRIVILEGED_BEDROCK_AGENT_ID_PARAM_PATH": params.supervisor_agent_id,
            "OSCAR_PRIVILEGED_BEDROCK_AGENT_ALIAS_PARAM_PATH": params.supervisor_agent_alias,
            "OSCAR_LIMITED_BEDROCK_AGENT_ID_PARAM_PATH": params.limited_supervisor_agent_id,
            "OSCAR_LIMITED_BEDROCK_AGENT_ALIAS_PARAM_PATH": params.limited_supervisor_agent_alias,


            # TTL configurations from .env
            "DEDUP_TTL": os.environ.get("DEDUP_TTL", "300"),
            "SESSION_TTL": os.environ.get("SESSION_TTL", "3600"),
            "CONTEXT_TTL": os.environ.get("CONTEXT_TTL", "604800"),  # 7 days
            
            # Context management from .env
            "MAX_CONTEXT_LENGTH": os.environ.get("MAX_CONTEXT_LENGTH", "3000"),
            "CONTEXT_SUMMARY_LENGTH": os.environ.get("CONTEXT_SUMMARY_LENGTH", "500"),
            
            # Feature flags from .env
            "ENABLE_DM": os.environ.get("ENABLE_DM", "false"),
            
            # AWS configuration from .env (AWS_REGION is automatically set by Lambda runtime)
            "AWS_ACCOUNT_ID": os.environ.get("AWS_ACCOUNT_ID") or os.environ.get("CDK_DEFAULT_ACCOUNT", ""),
            
            # Logging from .env
            "LOG_LEVEL": os.environ.get("LOG_LEVEL", "INFO")
        }
    
    def _get_communication_handler_environment_variables(self) -> Dict[str, str]:
        """
        Get environment variables for the communication handler Lambda function.
        
        Returns:
            Dictionary of environment variables for the communication handler
        """
        return {
            # Central secret reference
            "CENTRAL_SECRET_NAME": self.secrets_stack.central_env_secret.secret_name,

            "CONTEXT_TABLE_NAME": self.storage_stack.context_table_name,
            
            # Communication settings
            "MESSAGE_TIMEOUT": os.environ.get("MESSAGE_TIMEOUT", "30"),
            "MAX_RETRIES": os.environ.get("MAX_RETRIES", "3"),
            
            # Logging
            "LOG_LEVEL": os.environ.get("LOG_LEVEL", "INFO")
        }
    
    def _get_jenkins_agent_environment_variables(self) -> Dict[str, str]:
        """
        Get environment variables for the Jenkins agent Lambda function.
        
        Returns:
            Dictionary of environment variables for the Jenkins agent
        """
        return {
            # Central secret reference
            "CENTRAL_SECRET_NAME": self.secrets_stack.central_env_secret.secret_name,
            
            # Jenkins configuration
            "JENKINS_TIMEOUT": os.environ.get("JENKINS_TIMEOUT", "60"),
            "MAX_BUILD_WAIT_TIME": os.environ.get("MAX_BUILD_WAIT_TIME", "1800"),  # 30 minutes
            
            # Logging
            "LOG_LEVEL": os.environ.get("LOG_LEVEL", "INFO")
        }
    
    def _get_metrics_agent_environment_variables(self) -> Dict[str, str]:
        """
        Get environment variables for metrics agent Lambda function.
        
        Args:
            metrics_type: Type of metrics agent (build, test, release, or unified)
            
        Returns:
            Dictionary of environment variables for the metrics agent
        """
        return {
            # Central secret reference
            "CENTRAL_SECRET_NAME": self.secrets_stack.central_env_secret.secret_name,
            
            # Metrics configuration from .env - specify the type
            # "METRICS_TYPE": metrics_type,
            "REQUEST_TIMEOUT": os.environ.get("REQUEST_TIMEOUT", "30"),
            "MAX_RESULTS": os.environ.get("MAX_RESULTS", "500"),
            
            # # OpenSearch configuration from .env
            # "OPENSEARCH_HOST": os.environ.get("OPENSEARCH_HOST"),
            # "OPENSEARCH_DOMAIN_ACCOUNT": os.environ.get("OPENSEARCH_DOMAIN_ACCOUNT"),
            # "OPENSEARCH_REGION": os.environ.get("OPENSEARCH_REGION", "us-east-1"),
            #
            # # Cross-account access from .env
            # "METRICS_CROSS_ACCOUNT_ROLE_ARN": os.environ.get("METRICS_CROSS_ACCOUNT_ROLE_ARN"),
            # "EXTERNAL_ID": "oscar-metrics-access",
            #
            # # VPC configuration from .env
            # "VPC_ID": os.environ.get("VPC_ID"),
            # "SUBNET_IDS": os.environ.get("SUBNET_IDS"),
            # "SECURITY_GROUP_ID": os.environ.get("SECURITY_GROUP_ID"),
            #
            # # Logging from .env
            # "LOG_LEVEL": os.environ.get("LOG_LEVEL", "INFO")
        }

    
    def _add_outputs(self) -> None:
        """
        Add CloudFormation outputs for all Lambda functions.
        """
        # Output for each Lambda function
        for function_key, function in self.lambda_functions.items():
            # Function name output
            CfnOutput(
                self, f"{function_key.title().replace('_', '')}FunctionName",
                value=function.function_name,
                description=f"Name of the {function_key.replace('_', ' ')} Lambda function",
                export_name=f"Oscar{function_key.title().replace('_', '')}FunctionName"
            )

        # Summary output
        function_names = [func.function_name for func in self.lambda_functions.values()]
        CfnOutput(
            self, "AllLambdaFunctions",
            value=", ".join(function_names),
            description="Comma-separated list of all OSCAR Lambda function names"
        )