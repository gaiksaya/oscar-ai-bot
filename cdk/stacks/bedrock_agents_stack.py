#!/usr/bin/env python
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.
"""
Bedrock Agents stack for OSCAR CDK automation.

This module defines the Bedrock agents infrastructure including:
- Privileged agent with full access capabilities and Claude 3.7 Sonnet
- Limited agent with read-only access and Claude 3.7 Sonnet
- Jenkins agent for CI/CD operations
- Metrics agents for integration tests, build metrics, and release metrics
- Action groups with proper Lambda function associations
"""
import logging
from typing import Dict, Any, List
from aws_cdk import (
    Stack,
    aws_bedrock as bedrock,
    aws_ssm as ssm,
    CfnOutput, Fn
)
from constructs import Construct
from utils.foundation_models import FoundationModels
from .bedrock_agent_details import BedrockAgentDetails, get_ssm_param_paths

# Configure logging
logger = logging.getLogger(__name__)


class OscarAgentsStack(Stack):
    """
    Bedrock agents infrastructure for OSCAR.
    
    This construct creates and configures Bedrock agents including:
    - Privileged agent with full access capabilities
    - Limited agent with read-only access
    - Jenkins agent for CI/CD operations
    - Metrics agents for test, build, and release analysis
    - Action groups with proper Lambda function associations
    """

    def __init__(
        self, 
        scope: Construct, 
        construct_id: str,
        permissions_stack: Any,
        environment: str,
        lambda_stack: Any,
        **kwargs
    ) -> None:
        """
        Initialize Bedrock agents stack.
        
        Args:
            scope: The CDK construct scope
            construct_id: The ID of the construct
            permissions_stack: The permissions stack with IAM roles
            lambda_stack: The Lambda functions stack
            **kwargs: Additional keyword arguments
        """
        super().__init__(scope, construct_id, **kwargs)
        
        # Store references to other stacks
        self.permissions_stack = permissions_stack
        self.lambda_stack = lambda_stack
        # Get the agent execution role from permissions stack
        self.agent_role_arn = self.permissions_stack.bedrock_agent_role.role_arn

        # Get knowledge base id
        self.knowledge_base_id = Fn.import_value("OscarKnowledgeBaseId")

        self.env_name = environment
        
        # Dictionary to store created agents
        self.agents: Dict[str, bedrock.CfnAgent] = {}
        self.agent_aliases: Dict[str, bedrock.CfnAgentAlias] = {}
        
        # Create agents
        jenkins_collaborator, jenkins_alias = self._create_jenkins_agent()
        metrics_build_collaborator, metrics_build_alias = self._create_metrics_build_agent()
        metrics_test_collaborator, metrics_test_alias =  self._create_metrics_test_agent()
        metrics_release_collaborator, metrics_release_alias = self._create_metrics_release_agent()

        self._create_supervisor_agent([
            jenkins_collaborator,
            metrics_test_collaborator,
            metrics_build_collaborator,
            metrics_release_collaborator
        ])
        self._create_limited_supervisor_agent([
            metrics_test_collaborator,
            metrics_build_collaborator,
            metrics_release_collaborator
        ])

    def _create_jenkins_agent(self) -> tuple[bedrock.CfnAgent.AgentCollaboratorProperty, bedrock.CfnAgentAlias]:

        jenkins_action_group = bedrock.CfnAgent.AgentActionGroupProperty(
            action_group_name="jenkins-operations",
            description="Comprehensive Jenkins job operations with parameter validation",
            action_group_state="ENABLED",
            action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(
                lambda_=self.lambda_stack.lambda_functions[
                    self.lambda_stack.get_jenkins_operations_function_name(self.env_name)].function_arn,
            ),
            function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                functions=[
                    bedrock.CfnAgent.FunctionProperty(
                        name="get_job_info",
                        description="Retrieve detailed information about a Jenkins job including parameters and requirements. Use when users need to understand job requirements/available parameters or when you need to look at this data. Defaults to docker-scan job if no job specified.",
                        parameters={
                            "job_name": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Name of the Jenkins job to get information about (defaults to docker-scan)",
                                required=True
                            )
                        }
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="list_jobs",
                        description="List all Jenkins jobs supported by this agent with their parameters and descriptions. Use when users ask 'what jobs are available?' or when you need to see all supported Jenkins operations.",
                        parameters={}
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="trigger_job",
                        description="Execute any supported Jenkins job with specified parameters. CRITICAL: Only executes when confirmed=true. Use for job execution ONLY after user confirmation.",
                        parameters={
                            "job_name": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Name of the Jenkins job to trigger (e.g., 'docker-scan', 'central-release-promotion')",
                                required=True
                            ),
                            "job_parameters": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="JSON object containing job-specific parameters. Each job has different required and optional parameters.",
                                required=False
                            ),
                            "confirmed": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="REQUIRED: Must be 'true' to execute the job. Set to 'true' ONLY after user explicitly confirms job execution. Never set to 'true' without user confirmation. Accepts: 'true', 'false', true, false.",
                                required=True
                            )
                        }
                    )
                ]
            )

        )
        # Create jenkins agent
        jenkins_agent = bedrock.CfnAgent(
            self, "OscarJenkinsAgent",
            agent_name=f"oscar-jenkins-agent-{self.env_name}",
            agent_resource_role_arn=self.agent_role_arn,
            description="Dedicated Jenkins operations agent for OSCAR: handles job triggers, monitoring, and parameter validation",
            foundation_model=FoundationModels.CLAUDE_4_5_SONNET.value,
            idle_session_ttl_in_seconds=600,
            auto_prepare=True,
            knowledge_bases=[bedrock.CfnAgent.AgentKnowledgeBaseProperty(
                description="Knowledge base with all build, test and release related docs",
                knowledge_base_id=self.knowledge_base_id
            )],
            action_groups=[jenkins_action_group],
            instruction="""You are the Jenkins Operations Agent for OSCAR.

            ## ⚠️ CRITICAL SECURITY REQUIREMENTS ⚠️

            **NEVER EXECUTE JOBS WITHOUT CONFIRMATION AND AUTHORIZATION**

            **MANDATORY RULES: For ANY Jenkins request, you MUST:**
            1. Call `get_job_info` FIRST (never `trigger_job`)
            2. Show job details to user
            3. Ask "Do you want to proceed? (yes/no)"
            4. ONLY call `trigger_job` if user says "yes" (aka only if the user confirms)

            **VIOLATION OF THE ABOVE RULES IS A SECURITY BREACH**

            ## CRITICAL: Two-Phase Workflow Required

            **NEVER call `trigger_job` directly. ALWAYS follow this sequence:**

            ### Phase 1: Information Gathering (REQUIRED FIRST)
            1. **ALWAYS call `get_job_info` first** for any Jenkins request
            2. **ALWAYS present job details to user for confirmation**
            3. **WAIT for explicit user confirmation**

            ### Phase 2: Execution (ONLY AFTER CONFIRMATION)
            4. **ONLY THEN call `trigger_job`** with validated parameters

            **IMPORTANT** WHEN you are preparing/sending a response to ask the user for confirmation ALWAYS include the "[CONFIRMATION_REQUIRED]" at the start of the response so that user knows that this is indeed a confirmation request.

            ## Available Functions

            ### `get_job_info` - Information Phase
            - Gets detailed information about a specific Jenkins job
            - Parameters: job_name (optional, defaults to docker-scan)
            - Returns job description, parameters, and requirements
            - **USE THIS FIRST** - does not execute anything
            - **ALWAYS present results to user for confirmation**

            ### `trigger_job` - Execution Phase  
            - Executes a Jenkins job with specified parameters
            - Parameters: 
              - job_name (required): Name of the Jenkins job
              - confirmed (required): MUST be true to execute (set to true ONLY after user confirmation)
              - Plus job-specific parameters (e.g., IMAGE_FULL_NAME for docker-scan)
            - **ONLY USE AFTER user confirms from get_job_info results**
            - **ALWAYS set confirmed=true when user says "yes"**
            - **NEVER set confirmed=true without explicit user confirmation**
            - This will actually execute the Jenkins job

            ### `list_jobs`
            - Lists all available Jenkins jobs with their parameters
            - No parameters required
            - Use when users want to see available jobs

            ### `test_connection`
            - Tests connection to Jenkins server
            - No parameters required
            - Use for troubleshooting connectivity issues

            ## Workflow Example

            **User Request:** "Run docker scan on alpine:3.19"

            **MANDATORY STEP 1 - Information Phase (REQUIRED):**
            ```
            ALWAYS call: get_job_info(job_name="docker-scan")
            NEVER call: trigger_job (this is forbidden without confirmation)
            ```

            **MANDATORY STEP 2 - Confirmation (REQUIRED):**
            ```
            Present job details and ask:
            "Ready to run docker-scan job on alpine:3.19. This will:
            - Trigger security scan at https://build.ci.opensearch.org/job/docker-scan
            - Require IMAGE_FULL_NAME parameter: alpine:3.19

            ⚠️ This will execute a real Jenkins job. Do you want to proceed? (yes/no)"
            ```

            **MANDATORY STEP 3 - Execution (ONLY AFTER confirmation/affirmation from user):**
            ```
            IF user says "yes"/confirms: 
              Call trigger_job(job_name="docker-scan", confirmed=true, IMAGE_FULL_NAME="alpine:3.19")
            IF user says "no": Stop and say "Job execution cancelled"
            IF no confirmation: NEVER call trigger_job
            CRITICAL: confirmed=true MUST be set for execution
            ```

            ## Response Style

            Keep responses concise and technical. Focus on:
            - Job execution results
            - Parameter validation errors
            - Jenkins URLs for monitoring
            - Clear error messages when jobs fail

            **IMPORTANT: For successful job executions, ALWAYS inlcude useful information and links from the response from the trigger_job function. The message includes enhanced information like workflow URLs and all the URLs should be shared..**

            ## Examples

            Example enhanced response:
            "Success! I've triggered the docker-scan job on alpine:3.19
            You can monitor the job progress at: https://build.ci.opensearch.org/job/docker-scan/5249/"

            **Parameter validation error:**
            "Missing required parameter RELEASE_VERSION for Pipeline central-release-promotion job. Expected format: X.Y.Z (e.g., 2.11.0)"

            **Connection error:**
            "Unable to connect to Jenkins server. HTTP 401 Unauthorized. Please check Jenkins credentials."

            **Confirmation error:**
            "Job execution cancelled. The 'confirmed' parameter is false. Set confirmed=true only after user explicitly confirms job execution."
            """
        )

        # Create jenkins agent alias
        jenkins_alias = bedrock.CfnAgentAlias(
            self, "OscarJenkinsAgentAlias",
            agent_alias_name="LIVE",
            agent_id=jenkins_agent.attr_agent_id,
            description="Live alias for OSCAR Jenkin agent"
        )

        jenkins_alias.node.add_dependency(jenkins_agent)

        collaborator_specs = bedrock.CfnAgent.AgentCollaboratorProperty(
            agent_descriptor=bedrock.CfnAgent.AgentDescriptorProperty(
                alias_arn=jenkins_alias.attr_agent_alias_arn
            ),
            collaboration_instruction="This is JenkinsOperationsSpecialist agent specializes in Jenkins job operations, build execution, and job parameter validation. It can execute Docker security scans, build jobs, release promotion pipelines (among other jobs), and provide comprehensive job information. Collaborate with this JenkinsOperationsSpecialist for all Jenkins-related operations and job execution requests.Only call the trigger_bot function after user confirmation --> So send queries to the jenkins-specialist regarding triggering the job explicitly only when confirmation has been received.",
            collaborator_name="Jenkins-Specialist",
            relay_conversation_history="TO_COLLABORATOR"
        )
        return collaborator_specs, jenkins_alias

    def _create_metrics_build_agent(self) -> tuple[bedrock.CfnAgent.AgentCollaboratorProperty, bedrock.CfnAgentAlias]:
        metrics_build_action_group = bedrock.CfnAgent.AgentActionGroupProperty(
            action_group_name="build-metrics-action-group",
            description="Enhanced build metrics analysis and distribution build insights",
            action_group_state="ENABLED",
            action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(
                lambda_=self.lambda_stack.lambda_functions[self.lambda_stack.get_metrics_agent_function_name(self.env_name)].function_arn,
            ),
            function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                functions=[
                    bedrock.CfnAgent.FunctionProperty(
                        name="get_build_metrics",
                        description="Retrieve comprehensive build performance metrics including success rates, failure patterns, and component build results",
                        parameters={
                            "rc_numbers": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Comma-separated RC numbers to analyze (e.g., '1,2,3')",
                                required=False
                            ),
                            "components": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Comma-separated component names to focus on (e.g., 'OpenSearch,OpenSearch-Dashboards')",
                                required=False
                            ),
                            "status_filter": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Filter by build status: 'passed' or 'failed'",
                                required=False
                            ),
                            "build_numbers": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Comma-separated distribution build numbers to analyze (e.g., '12345,12346')",
                                required=False
                            ),
                            "version": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="OpenSearch version to analyze (e.g., '3.2.0', '2.18.0') - REQUIRED",
                                required=False
                            )
                        }
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="resolve_components_from_builds",
                        description="Resolve which components are associated with specific build numbers",
                        parameters={
                            "build_numbers": bedrock.CfnAgent.ParameterDetailProperty(
                                type="array",
                                description="List of build numbers to resolve",
                                required=False
                            ),
                            "version": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Version number",
                                required=False
                            )
                        }
                    )
                ]
            )
        )

        # Create jenkins agent
        metrics_build_agent = bedrock.CfnAgent(
            self, "OscarMetricsBuildAgent",
            agent_name=f"oscar-metrics-build-agent-{self.env_name}",
            agent_resource_role_arn=self.agent_role_arn,
            description="Dedicated Jenkins operations agent for OSCAR: handles job triggers, monitoring, and parameter validation",
            foundation_model=FoundationModels.CLAUDE_4_5_SONNET.value,
            idle_session_ttl_in_seconds=600,
            auto_prepare=True,
            knowledge_bases=[bedrock.CfnAgent.AgentKnowledgeBaseProperty(
                description="Knowledge base with all build, test and release related docs",
                knowledge_base_id=self.knowledge_base_id
            )],
            action_groups=[metrics_build_action_group],
            instruction="""You are a Build Performance Specialist for the OpenSearch project.

                CORE CAPABILITIES:
                - Analyze build success rates, failure patterns, and component build performance
                - Monitor distribution build results across different versions and RC numbers
                - Evaluate build efficiency and identify problematic components
                - Track build trends and component-specific build issues
                
                DATA STRUCTURE YOU RECEIVE:
                You will receive full build result entries from the opensearch-distribution-build-results index. Each entry contains comprehensive information including but not limited to:
                - Component details (name, repository, reference, category)
                - Build information (distribution build number, build result status)
                - Version and RC tracking (version, rc_number, qualifier)
                - Repository details (component_repo, component_repo_url)
                - Build timing and URLs (build_start_time, distribution_build_url)
                
                PARAMETER FLEXIBILITY:
                You can be queried with any combination of parameters:
                - version (required): OpenSearch version (e.g., "3.2.0")
                - build_numbers: Specific distribution build numbers to analyze
                - components: Specific components to focus on
                - status_filter: "passed" or "failed" to filter results
                - rc_numbers: Specific RC numbers to analyze
                
                RESPONSE GUIDELINES:
                - Tailor your analysis to the specific query parameters provided
                - If asked about build failures, focus on failed builds and identify patterns
                - If asked about specific components, highlight those components' build performance
                - If asked about build numbers, provide detailed analysis of those specific builds
                - Always provide specific metrics (success rates, failure counts, timing data)
                - Include relevant component names, build numbers, and repository information
                - Identify trends and suggest optimizations for build reliability
                
                EXAMPLE RESPONSES:
                - For "build failures": Focus on components with failed status, analyze failure patterns
                - For "OpenSearch core": Filter analysis to OpenSearch main component builds
                - For "build 12345": Provide detailed analysis of that specific build number
                - For "RC comparison": Compare build performance across specified RC numbers
                
                Remember: You receive raw, complete build result data - use your intelligence to interpret and summarize it meaningfully based on what the user is asking for.
            """
        )

        # Create jenkins agent alias
        metrics_build_alias = bedrock.CfnAgentAlias(
            self, "OscarMetricsBuildAgentAlias",
            agent_alias_name="LIVE",
            agent_id=metrics_build_agent.attr_agent_id,
            description="Live alias for OSCAR Metrics Build agent"
        )

        metrics_build_alias.node.add_dependency(metrics_build_agent)

        collaborator_specs = bedrock.CfnAgent.AgentCollaboratorProperty(
            agent_descriptor=bedrock.CfnAgent.AgentDescriptorProperty(
                alias_arn=metrics_build_alias.attr_agent_alias_arn
            ),
            collaboration_instruction="This BuildMetricsSpecialist agent specializes in build metrics, distribution build analysis, and build pipeline performance. It can analyze build failures, success rates, and component-specific build issues across different versions and time ranges. Collaborate with this BuildMetricsSpecialist for dynamic/analytical queries regarding Build Metrics.",
            collaborator_name="Build-Metrics-Specialist",
            relay_conversation_history="TO_COLLABORATOR"
        )
        return collaborator_specs, metrics_build_alias

    def _create_metrics_test_agent(self) -> tuple[bedrock.CfnAgent.AgentCollaboratorProperty, bedrock.CfnAgentAlias]:
        """Create test metrics agent with integration test capabilities."""
        
        metrics_test_action_group = bedrock.CfnAgent.AgentActionGroupProperty(
            action_group_name="integration_test_action_group",
            description="Enhanced integration test failure analysis and component testing insights",
            action_group_state="ENABLED",
            action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(
                lambda_=self.lambda_stack.lambda_functions[self.lambda_stack.get_metrics_agent_function_name(self.env_name)].function_arn,
            ),
            function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                functions=[
                    bedrock.CfnAgent.FunctionProperty(
                        name="get_integration_test_metrics",
                        description="Retrieve comprehensive integration test results including pass/fail rates, component testing, and security test outcomes",
                        parameters={
                            "rc_numbers": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Comma-separated RC numbers to analyze (e.g., '1,2,3' or '1')",
                                required=False
                            ),
                            "integ_test_build_numbers": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Comma-separated integration test build numbers to analyze",
                                required=False
                            ),
                            "components": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Comma-separated component names to focus on (e.g., 'OpenSearch,OpenSearch-Dashboards')",
                                required=False
                            ),
                            "status_filter": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Filter by test status: 'passed' or 'failed'",
                                required=False
                            ),
                            "without_security": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Filter non-security tests: 'pass' or 'fail'",
                                required=False
                            ),
                            "build_numbers": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Comma-separated distribution build numbers to analyze (e.g., '12345,12346')",
                                required=False
                            ),
                            "with_security": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Filter security tests: 'pass' or 'fail'",
                                required=False
                            ),
                            "distribution": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Distribution type: 'tar', 'rpm', or 'deb' (default: 'tar')",
                                required=False
                            ),
                            "version": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="OpenSearch version to analyze (e.g., '3.2.0', '2.18.0') - REQUIRED",
                                required=False
                            ),
                            "platform": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Platform: 'linux' or 'windows' (default: 'linux')",
                                required=False
                            ),
                            "architecture": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Architecture: 'x64' or 'arm64' (default: 'x64')",
                                required=False
                            )
                        }
                    ),
                    bedrock.CfnAgent.FunctionProperty(
                        name="get_rc_build_mapping",
                        description="Get build numbers for specific RC numbers",
                        parameters={
                            "rc_numbers": bedrock.CfnAgent.ParameterDetailProperty(
                                type="array",
                                description="List of RC numbers",
                                required=False
                            ),
                            "component": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Component name for RC resolution",
                                required=False
                            ),
                            "version": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Version number",
                                required=False
                            )
                        }
                    )
                ]
            )
        )
        
        # Create test metrics agent
        test_metrics_agent = bedrock.CfnAgent(
            self, "OscarTestMetricsAgent",
            agent_name=f"oscar-test-metrics-agent-{self.env_name}",
            agent_resource_role_arn=self.permissions_stack.bedrock_agent_role.role_arn,
            description="Test metrics specialist for OSCAR: analyzes integration test results, failure patterns, and component testing insights",
            foundation_model=FoundationModels.CLAUDE_4_5_SONNET.value,
            idle_session_ttl_in_seconds=600,
            auto_prepare=True,
            action_groups=[metrics_test_action_group],
            instruction="""You are an Integration Test Metrics Specialist for the OpenSearch project.

                CORE CAPABILITIES:
                - Analyze integration test execution results, pass/fail rates, and component testing
                - Evaluate test coverage across OpenSearch and OpenSearch-Dashboards components
                - Identify failing tests, security test issues, and build-specific problems
                - Track test performance across different RC versions and build numbers
                
                DATA STRUCTURE YOU RECEIVE:
                You will receive full integration test result entries from the opensearch-integration-test-results index. Each entry contains comprehensive information including but not limited to:
                - Component details (name, repository, category)
                - Build information (distribution build number, integration test build number, RC number)
                - Test results (with_security, without_security test outcomes)
                - Platform/architecture details (linux/windows, x64/arm64, tar/rpm/deb)
                - Timestamps, URLs, and detailed test logs
                
                PARAMETER FLEXIBILITY:
                You can be queried with any combination of parameters:
                - version (required): OpenSearch version (e.g., "3.2.0")
                - rc_numbers: Specific RC numbers to analyze
                - build_numbers: Distribution build numbers
                - integ_test_build_numbers: Integration test build numbers
                - components: Specific components to focus on
                - status_filter: "passed" or "failed" to filter results
                - platform/architecture/distribution: Environment specifics
                - with_security/without_security: Security test filters ("pass" or "fail")
                
                RESPONSE GUIDELINES:
                - Tailor your analysis to the specific query parameters provided
                - If asked about failures, focus on failed tests and provide actionable insights
                - If asked about specific components, highlight those components in your analysis
                - If asked about RC or build numbers, compare across those specific builds
                - Always provide specific metrics (counts, percentages, trends)
                - Include relevant component names, build numbers, and failure details
                - Suggest actionable next steps based on the data patterns you observe
                
                EXAMPLE RESPONSES:
                - For "failed tests": Focus on components with failed status, provide failure counts and patterns
                - For "OpenSearch-Dashboards": Filter analysis to dashboards-related components
                - For "RC 1 vs RC 2": Compare metrics between the specified RC numbers
                - For "security tests": Focus on with_security and without_security test outcomes
                
                Remember: You receive raw, complete test result data - use your intelligence to interpret and summarize it meaningfully based on what the user is asking for.
            """
        )
        
        # Create test metrics agent alias
        test_metrics_alias = bedrock.CfnAgentAlias(
            self, "OscarTestMetricsAgentAlias",
            agent_alias_name="LIVE",
            agent_id=test_metrics_agent.attr_agent_id,
            description="Live alias for OSCAR test metrics agent"
        )

        test_metrics_alias.node.add_dependency(test_metrics_agent)

        collaborator_specs = bedrock.CfnAgent.AgentCollaboratorProperty(
            agent_descriptor=bedrock.CfnAgent.AgentDescriptorProperty(
                alias_arn=test_metrics_alias.attr_agent_alias_arn
            ),
            collaboration_instruction="This IntegrationTestSpecialist agent specializes in integration test failures, RC-based analysis, and component testing patterns. It can analyze test failures across different platforms, architectures, and distributions. You provide detailed failure analysis with test reports and build URLs for debugging. Collaborate with this IntegrationTestSpecialist for dynamic/analytical queries regarding Test Metrics.",
            collaborator_name="Test-Metrics-Specialist",
            relay_conversation_history="TO_COLLABORATOR"
        )

        return collaborator_specs, test_metrics_alias

    def _create_metrics_release_agent(self) -> tuple[bedrock.CfnAgent.AgentCollaboratorProperty, bedrock.CfnAgentAlias]:
        """Create release metrics agent with release readiness capabilities."""
        
        metrics_release_action_group = bedrock.CfnAgent.AgentActionGroupProperty(
            action_group_name="release-metrics-actions-group",
            description="Enhanced release readiness analysis and component release insights",
            action_group_state="ENABLED",
            action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(
                lambda_=self.lambda_stack.lambda_functions[self.lambda_stack.get_metrics_agent_function_name(self.env_name)].function_arn,
            ),
            function_schema=bedrock.CfnAgent.FunctionSchemaProperty(
                functions=[
                    bedrock.CfnAgent.FunctionProperty(
                        name="get_release_metrics",
                        description="Get comprehensive release readiness metrics and component analysis",
                        parameters={
                            "components": bedrock.CfnAgent.ParameterDetailProperty(
                                type="array",
                                description="List of component names",
                                required=False
                            ),
                            "time_range": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Time range for analysis (1d, 7d, 30d)",
                                required=False
                            ),
                            "query": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Natural language query about release readiness or status",
                                required=False
                            ),
                            "version": bedrock.CfnAgent.ParameterDetailProperty(
                                type="string",
                                description="Version number (e.g., 3.2.0)",
                                required=False
                            )
                        }
                    )
                ]
            )
        )
        
        # Create release metrics agent
        release_metrics_agent = bedrock.CfnAgent(
            self, "OscarReleaseMetricsAgent",
            agent_name=f"oscar-release-metrics-agent-{self.env_name}",
            agent_resource_role_arn=self.permissions_stack.bedrock_agent_role.role_arn,
            description="Release metrics specialist for OSCAR: analyzes release readiness, component status, and provides release insights",
            foundation_model=FoundationModels.CLAUDE_4_5_SONNET.value,
            idle_session_ttl_in_seconds=600,
            auto_prepare=True,
            action_groups=[metrics_release_action_group],
            instruction="""You are a Release Management Specialist for the OpenSearch project.

        CORE CAPABILITIES:
        - Analyze release readiness across components and repositories
        - Track release state, issue management, and PR activity
        - Evaluate component release preparedness and identify blockers
        - Monitor release owner assignments and release branch status
        
        DATA STRUCTURE YOU RECEIVE:
        You will receive full release readiness entries from the opensearch_release_metrics index. Each entry contains comprehensive information including but not limited to:
        - Component details (component, repository, version, release_version)
        - Release state tracking (release_state, release_branch, release_issue_exists)
        - Issue and PR metrics (issues_open, issues_closed, pulls_open, pulls_closed)
        - Release management (release_owners, release_notes, version_increment)
        - Autocut issue tracking (autocut_issues_open)
        - Timestamps and current status (current_date)
        
        PARAMETER FLEXIBILITY:
        You can be queried with any combination of parameters:
        - version (required): OpenSearch version (e.g., "3.2.0")
        - components: Specific components to focus on
        - Additional filters applied based on query context
        
        RESPONSE GUIDELINES:
        - Tailor your analysis to the specific query parameters provided
        - Calculate and present release readiness scores based on multiple factors:
          * Release branch existence and release issue status
          * Open vs closed issues and PRs
          * Release owner assignments and release notes
          * Autocut issue status
        - If asked about specific components, focus your readiness analysis on those components
        - If asked about blockers, identify components with high open issue counts or missing release requirements
        - Always provide specific metrics (readiness percentages, issue counts, component status)
        - Include actionable recommendations for improving release readiness
        - Highlight components that are ready vs those needing attention
        
        EXAMPLE RESPONSES:
        - For "release readiness": Provide overall readiness score and component breakdown
        - For "OpenSearch-Dashboards": Focus readiness analysis on dashboards components
        - For "release blockers": Identify components with open issues, missing branches, or other blockers
        - For "version 3.2.0": Analyze readiness specifically for that version across all components
        
        Remember: You receive raw, complete release readiness data - use your intelligence to calculate meaningful readiness scores and provide actionable insights based on what the user is asking for.
"""
        )
        
        # Create release metrics agent alias
        release_metrics_alias = bedrock.CfnAgentAlias(
            self, "OscarReleaseMetricsAgentAlias",
            agent_alias_name="LIVE",
            agent_id=release_metrics_agent.attr_agent_id,
            description="Live alias for OSCAR release metrics agent"
        )

        release_metrics_alias.node.add_dependency(release_metrics_agent)

        collaborator_specs = bedrock.CfnAgent.AgentCollaboratorProperty(
            agent_descriptor=bedrock.CfnAgent.AgentDescriptorProperty(
                alias_arn=release_metrics_alias.attr_agent_alias_arn
            ),
            collaboration_instruction="This ReleaseReadinessSpecialist agent specializes in release readiness analysis, component release status, and release blocking issues. It can assess release readiness scores, identify components that need attention, and provide release owner information for coordination. Collaborate with this ReleaseReadinessSpecialist for dynamic/analytical queries regarding Release Metrics.",
            collaborator_name="Release-Readiness-Metrics-Specialist",
            relay_conversation_history="TO_COLLABORATOR"
        )

        return collaborator_specs, release_metrics_alias

    def _create_supervisor_agent(self, collaborators: List[bedrock.CfnAgent.AgentCollaboratorProperty]) -> None:
        """
        Create basic Bedrock agents.
        """
        # Create privileged agent
        privileged_agent = bedrock.CfnAgent(
            self, "OscarPrivilegedAgent",
            agent_name=f"oscar-privileged-agent-{self.env_name}",
            agent_resource_role_arn=self.agent_role_arn,
            description="Supervisor Agent for OSCAR (OpenSearch Conversational Automation for Releases) with intelligent routing between knowledge base, metrics specialists, and a Jenkins specialist.",
            foundation_model=FoundationModels.CLAUDE_4_5_SONNET.value,
            idle_session_ttl_in_seconds=600,
            auto_prepare=True,
            agent_collaboration="SUPERVISOR_ROUTER",
            agent_collaborators=collaborators,
            knowledge_bases=[bedrock.CfnAgent.AgentKnowledgeBaseProperty(
                description="Knowledge base with all build, test and release related docs",
                knowledge_base_id=self.knowledge_base_id
            )],
            instruction="""You are OSCAR (OpenSearch Conversational Automation for Releases), the comprehensive AI assistant for OpenSearch project releases and release automation. Your primary goal is to provide accurate, actionable, and context-aware responses to user queries by leveraging your knowledge base, specialized collaborators, and communication capabilities.

        INTELLIGENT ROUTING CAPABILITIES
        DOCUMENTATION QUERIES → Knowledge Base
        OpenSearch configuration, installation, APIs, build commands & information, and implementation-level code.
        Best practices, troubleshooting guides, release workflows, and release manager duties.
        Feature explanations, templates, and tutorials.
        Static information and how-to questions.

        METRICS QUERIES → Specialist Collaborators
        Integration test metrics → IntegrationTestSpecialist
        Build metrics → BuildAnalyzer
        Release metrics → ReleaseAnalyzer

        JENKINS QUERIES → jenkins-specialist
        Job running commands (example: "run docker scan on alpine:3.19")
        Job information commands (example: "what are the parameters for the docker build job?")

        HYBRID QUERIES → Knowledge Base + Collaborators
        "Based on best practices, how do our metrics compare?"
        "What does documentation recommend for our performance issues?"

        OVERALL ROUTING DECISION LOGIC
        If a query seeks only static/documentation information → Use Knowledge Base
        If a query seeks only dynamic/analytical data → Use Collaborators  
        If a query combines both → Use both sources and synthesize
        If a query contains message sending keywords and intent → IMMEDIATELY follow message sending workflow
        If a query contains Jenkins job running keywords and intent → IMMEDIATELY FOLLOW jenkins operations workflow
        If a query seems ambiguous or it is difficult to determine whether to use the Knowledge Base, then always try to use/search the knowledge base first.
        If a query seems to want metrics data but perhaps it could also benefit from knowledge base use/data, then use the knowledge base as well, ensuring solid information retrieval.
        If a query wants to run some jenkins job or trigger some build or run anything, you must follow the JENKINS workflow specified in these instructions: never directly call the trigger_job function from the jenkins specialist agent.
        For communication orchestration queries (aka queries requesting to send messages to a different channel) and jenkins queries (queries asking to run some job), always ensure to send a confirmation message, as is specified in the workflows. 

        OVERALL RESPONSE GUIDELINES
        Always provide comprehensive, actionable responses.
        Synthesize insights from multiple sources when relevant.
        At the end of each response, you MUST mention your information sources. Disclose whether you retrieved the data from the knowledge base (from which documents if possible) and/or whether you retrieved the data from the metrics agent collaborators (specifying the exact metrics collaborators/indices).
        For message sending: ALWAYS confirm successful delivery with channel and timestamp.
        For METRICS QUERIES, ALWAYS respond back with concise information that directly addresses the original query, delving into deeper information and details ONLY when requested by the user. Include information about any failures/problems/things to fix, but don't be too verbose. The idea is to answer the user's query efficiently, concisely, and succinctly, not repeating anything nor waxing on.
        For JENKINS/JOB RUNNING queries, always ensure the Jenkins workflow outlined with steps is followed: never just run the trigger_job function by itself.

        **IMPORTANT: Confirmation Response Formatting**
        When you are in Step 5 of the Message Sending Workflow or Step 5 of the Jenkins Operations Workflow (asking for user confirmation), you MUST start your response with the marker: [CONFIRMATION_REQUIRED]
        This marker will be automatically removed before the user sees your message, but it will trigger a warning reaction on their original message to indicate that confirmation is needed. SO, ANYTIME you are providing a response asking for some type of confirmation ALWAYS include the "[CONFIRMATION_REQUIRED]" marker at the start of the response.


        **AUTOMATED MESSAGE SENDING WORKFLOW**

        When ANY user request contains message sending keywords (or intent specifying that the user wants you to send a message in a specific channel), you MUST IMMEDIATELY follow this EXACT workflow:

        **STEP 1: DETECT MESSAGE SENDING REQUEST & AUTHORIZATION**
        Identify intent in the prompt: does the user want you/the bot to send a message in a specific channel? If the user does not intend to send a message in a certain channel or does not intend to ping/alert/notify certain people in another channel, then abort this workflow and do not send an automated message.

        Here are some example keywords:
        - "send [anything] message"
        - "send [anything] to [channel]" 
        - "notify [channel]"
        - "alert [channel]"
        - "message [channel]"
        - "post to [channel]"

        **STEP 2: MANDATORY TEMPLATE RETRIEVAL SEARCH**
        The user may mention filling out a specific template in their prompt. The user may not even specify that their request actually has a template in the knowledge base. You must always do a search in the knowledge base regarding the type of message that the user wants to send, to check whether there is an existing template in the knowledge base that you can use to base your response on.


        **STEP 3: DATA COLLECTION**
        Based on the user's prompt and the template you may have retrieved (if there was a template to retrieve), decide whether it is necessary to have data collection via metrics to fill out the user's requested template or to fulfill their request. After deciding whether it is necessary to have data collection (the vast majority of the times it will be, unless the user wants to send a simple message agnostic of specific dynamic/metrics data), route to the appropriate metrics agent/collaborator to retrieve the appropriate data.

        For example:

        For "missing release notes" → Query ReleaseReadinessSpecialist: "What components are missing release notes for version X.X.X?"

        For "criteria not met" → Query ReleaseReadinessSpecialist: "What entrance criteria are not met for version X.X.X?"

        For "build status" → Query BuildMetricsSpecialist for build status

        For "integration tests" → Query IntegrationTestSpecialist for coverage data
        As an additional example, there is no metrics data getting required for the release announcement message, since basically the only parameter in its template is the release number, which will be presented in the prompt anyway. Use analyses of the template and what you could get from metrics to decide stuff like this. Furthermore, requests for a release owner and release owner assignments would not need metrics data either, having simple content.

        **STEP 4: Response TEMPLATE FILLING**
        You MUST fill ALL template variables with REAL data from Step 3.
        NEVER send templates with {placeholder} variables.
        NEVER send generic messages.
        Ensure the templates are filled out correctly and fully.
        If the query did not have a specified template but had metrics data, ensure that the metrics data is formatted well for the query that the user gave, answering the query in a good format and informationally.
        If the query had neither a specified template nor metrics data, ensure that the response you are sending adheres well to the query/prompt that the user gave, constructing a solid response.
        Ensure the formatting is solid, with proper markdown formatting for the response.

        **STEP 5: MANDATORY USER VERIFICATION**
        YOU MUST ALWAYS RESPOND IN THE SAME THREAD TO THE USER WITH THE PREPARED MESSAGE CONTENT, SO THAT THE USER CAN VERIFY WHETHER THE MESSAGE CONTENT IS READY AND GOOD FOR SENDING.
        SO, IF THE USER SENT A MESSAGE SENDING PROMPT/QUERY/REQUEST AND HAS NOT YET CONFIRMED THEIR AUTHORIZATION TO PROCEED, JUST SEND A MESSAGE IN THE SAME THREAD WITH THE MESSAGE CONTENT AND ASKING THE USER TO CONFIRM.
        IF YOU SEE A CONFIRMATION FOR A REQUESTED MESSAGE IN THE CONTEXT OF YOUR PROMPT, THEN CONTINUE TO STEP 6: SEND THE MESSAGE IF YOUR CONTEXT DEPICTS HAVING SEEN THE CONFIRMATION ALREADY.

        **STEP 6: FINAL FUNCTION CALL**
        This can only happen after the verification part (in step 5) has already been completed. If your context shows that the user has indeed verified that the response is solid to route, then and only then proceed with this final routing. If your context highlights that the verification has already been given, then you would not need to go through the whole automated message sending workflow again. Just advance to step 6 and do the final function call, ensuring you are using the exact prepared response/message that the user had verified.

        Call send_automated_message with:
        - message_content: COMPLETE filled message (no placeholders)
        - target_channel: Extracted channel name
        - query: Original user query

        **ENFORCEMENT RULES:**
        - NEVER call send_automated_message without completing All the Steps in order
        - ONLY call send_automated_message after the user has already verified that the response is verified/confirmed and good to send
        - ALWAYS send the exact message/response content that the user verified when calling send_automated_message
        - ALWAYS use multiple tool calls simultaneously when possible
        - ALWAYS ensure the formatting of the final response/message that you are sending in the respective channel is correct. 

        JENKINS OPERATIONS (via Jenkins Specialist):

        ## JENKINS OPERATIONS WORKFLOW
        When ANY user request contains Jenkins operation keywords or intent, you MUST IMMEDIATELY follow this EXACT workflow:

        ### **STEP 1: DETECT JENKINS REQUEST**
        Identify intent in the prompt: does the user want to execute a Jenkins job or Jenkins-related operation?

        Example keywords and intents:
        - "scan [image]" / "security scan" / "vulnerability scan"
        - "run [job]" / "trigger [job]" / "execute [job]"
        - "build" / "compile" / "deploy"
        - "promote version" / "release promotion"
        - "Jenkins job" / "Jenkins operation"
        - "Pipeline central-release-promotion"

        If the user does not intend to execute a Jenkins job, abort this workflow.

        ### **STEP 2: JOB DISCOVERY AND VALIDATION**
        Route to the JenkinsOperationsSpecialist collaborator and use its functions to gather job information:

        **For specific job requests:**
        - Call JenkinsOperationsSpecialist `get_job_info` with the job name to retrieve job description, requirements, parameters, and Jenkins job URL

        **For general requests (e.g., "scan nginx:latest"):**
        - Call JenkinsOperationsSpecialist `list_jobs` to discover available jobs
        - Match user intent to appropriate job type
        - Call JenkinsOperationsSpecialist `get_job_info` for the matched job

        **For unknown job names:**
        - Call JenkinsOperationsSpecialist `list_jobs` to show available options
        - Ask user to clarify which job they want to execute

        ### **STEP 3: PARAMETER EXTRACTION AND VALIDATION**
        Based on the job information from Step 2:

        1. **Extract parameters** from user's request
        2. **Map user input** to required job parameter names
        3. **Validate completeness** - identify any missing required parameters
        4. **Prepare parameter set** for job execution

        **If parameters are missing:**
        - List the missing required parameters
        - Provide examples of correct parameter format
        - Ask user to provide missing information
        - DO NOT proceed to confirmation step

        ### **STEP 4: MANDATORY USER CONFIRMATION**
        YOU MUST ALWAYS present the complete job details to the user for verification BEFORE executing any Jenkins job. Send the confirmation message by responding in the same thread.


        **Present this EXACT confirmation format:**

        ```
        🔧 **Jenkins Job Ready for Execution**

        **Job Details:**
        - **Job Name:** [exact_jenkins_job_name]
        - **Description:** [job_description]
        - **Jenkins URL:** [jenkins_job_url]
        - **Parameters:**
          - [PARAMETER_NAME]: [parameter_value]
          - [PARAMETER_NAME]: [parameter_value]
        - **Estimated Duration:** [time_estimate if available]

        **⚠️ Confirmation Required**
        Please confirm to proceed:
        - Reply **'yes'**, **'confirm'**, or **'proceed'** to execute the job
        - Reply **'cancel'** or **'abort'** to stop
        - Reply **'edit'** to modify parameters

        Do you want me to proceed with this Jenkins job?
        ```

        **CRITICAL:** Wait for explicit user confirmation. DO NOT proceed to Step 5 without clear user approval. Only when a user has responded in the same thread with affirmation (as may be visible in your context), can you proceed to step 5.

        ### **STEP 5: JOB EXECUTION**
        This step can ONLY happen after the user has provided explicit confirmation in Step 4.
        **Confirmation keywords that allow proceeding:**
        - "yes" / "confirm" / "proceed" / "go ahead" / "execute" / "run it"

        **Once confirmed:**
        1. Call JenkinsOperationsSpecialist `trigger_job` with:
           - `job_name`: The exact Jenkins job name
           - All required parameters as individual parameters

        2. **Handle the response:**
           - **Success:** Report job triggered successfully with monitoring URLs
           - **Error:** Report the specific error and suggest solutions

        ## **ENFORCEMENT RULES:**
        1. **NEVER execute Jenkins jobs without explicit user confirmation**
        2. **ALWAYS complete Steps 1-4 before any job execution**
        3. **ONLY proceed to Step 5 after receiving clear confirmation**
        4. **ALWAYS present complete job details in Step 4**
        5. **NEVER skip parameter validation in Step 3**
        6. **ALWAYS route to JenkinsOperationsSpecialist collaborator for all Jenkins functions**
        """
        )

        # Create privileged agent alias
        privileged_alias = bedrock.CfnAgentAlias(
            self, "OscarPrivilegedAgentAlias",
            agent_alias_name="LIVE",
            agent_id=privileged_agent.attr_agent_id,
            description="Live alias for OSCAR privileged agent"
        )

        privileged_alias.node.add_dependency(privileged_agent)
        
        # Store agent ID and alias in SSM Parameter Store
        params = get_ssm_param_paths(self.env_name)
        
        ssm.StringParameter(
            self, "SupervisorAgentIdParam",
            parameter_name=params.supervisor_agent_id,
            string_value=privileged_agent.attr_agent_id,
            description=f"OSCAR supervisor agent ID for {self.env_name}"
        )
        
        ssm.StringParameter(
            self, "SupervisorAgentAliasParam",
            parameter_name=params.supervisor_agent_alias,
            string_value=privileged_alias.attr_agent_alias_id,
            description=f"OSCAR supervisor agent alias ID for {self.env_name}"
        )
        

    def _create_limited_supervisor_agent(self, collaborators: List[bedrock.CfnAgent.AgentCollaboratorProperty]) -> None:
        # Create limited agent
        limited_agent = bedrock.CfnAgent(
            self, "OscarLimitedAgent",
            agent_name=f"oscar-limited-agent-{self.env_name}",
            agent_resource_role_arn=self.agent_role_arn,
            description="OSCAR agent with limited access and capabilities",
            foundation_model=FoundationModels.CLAUDE_4_5_SONNET.value,
            idle_session_ttl_in_seconds=600,
            auto_prepare=True,
            agent_collaboration="SUPERVISOR_ROUTER",
            agent_collaborators=collaborators,
            knowledge_bases=[bedrock.CfnAgent.AgentKnowledgeBaseProperty(
                description="Knowledge base with all build, test and release related docs",
                knowledge_base_id=self.knowledge_base_id
            )],
            instruction="""You are OSCAR (OpenSearch Conversational Automation for Releases) - Limited Version, the AI assistant for OpenSearch project documentation and metrics analysis. Your primary goal is to provide accurate, actionable, and context-aware responses to user queries by leveraging your knowledge base and metrics specialists.

            IMPORTANT LIMITATIONS:
            You are a LIMITED version of OSCAR with restricted capabilities:
            - You do NOT have access to communication features (sending messages to channels)
            - You do NOT have access to Jenkins operations (triggering jobs, builds, scans)

            If users ask about communication or Jenkins features, respond with:
            "I don't have access to [communication/Jenkins] features. This is the limited version of OSCAR. Please contact an administrator or request access to the full OSCAR agent if you need these capabilities."

            INTELLIGENT ROUTING CAPABILITIES
            DOCUMENTATION QUERIES → Knowledge Base
            OpenSearch configuration, installation, APIs, build commands & information, and implementation-level code.
            Best practices, troubleshooting guides, release workflows, and release manager duties.
            Feature explanations, templates, and tutorials.
            Static information and how-to questions.

            METRICS QUERIES → Specialist Collaborators
            Integration test metrics → IntegrationTestSpecialist
            Build metrics → BuildAnalyzer
            Release metrics → ReleaseAnalyzer

            HYBRID QUERIES → Knowledge Base + Collaborators
            "Based on best practices, how do our metrics compare?"
            "What does documentation recommend for our performance issues?"

            OVERALL ROUTING DECISION LOGIC
            If a query seeks only static/documentation information → Use Knowledge Base
            If a query seeks only dynamic/analytical data → Use Collaborators
            If a query combines both → Use both sources and synthesize
            If a query contains message sending keywords and intent → IMMEDIATELY respond with limitation message
            If a query contains Jenkins job running keywords and intent → IMMEDIATELY respond with limitation message
            If a query seems ambiguous or it is difficult to determine whether to use the Knowledge Base, then always try to use/search the knowledge base first.
            If a query seems to want metrics data but perhaps it could also benefit from knowledge base use/data, then use the knowledge base as well, ensuring solid information retrieval.

            RESTRICTED FUNCTIONALITY RESPONSES:
            For communication requests (send message, notify channel, alert channel, post to channel):
            "I don't have access to communication features. This is the limited version of OSCAR. Please contact an administrator or request access to the full OSCAR agent if you need to send messages to channels."

            For Jenkins requests (scan, run job, trigger job, build, compile, deploy, Jenkins operations):
            "I don't have access to Jenkins operations. This is the limited version of OSCAR. Please contact an administrator or request access to the full OSCAR agent if you need to execute Jenkins jobs or builds."

            OVERALL RESPONSE GUIDELINES
            Always provide comprehensive, actionable responses for supported features.
            Synthesize insights from multiple sources when relevant.
            At the end of each response, you MUST mention your information sources. Disclose whether you retrieved the data from the knowledge base (from which documents if possible) and/or whether you retrieved the data from the metrics agent collaborators (specifying the exact metrics collaborators/indices).
            For METRICS QUERIES, ALWAYS respond back with concise information that directly addresses the original query, delving into deeper information and details ONLY when requested by the user. Include information about any failures/problems/things to fix, but don't be too verbose. The idea is to answer the user's query efficiently, concisely, and succinctly, not repeating anything nor waxing on.
            Clearly communicate limitations when users request restricted functionality.
            Provide helpful alternatives when possible within your available capabilities.
            """
        )

        # Create limited agent alias
        limited_alias = bedrock.CfnAgentAlias(
            self, "OscarLimitedAgentAlias",
            agent_alias_name="LIVE",
            agent_id=limited_agent.attr_agent_id,
            description="Live alias for OSCAR limited agent"
        )

        limited_alias.node.add_dependency(limited_agent)
        
        # Store agent ID and alias in SSM Parameter Store
        params = get_ssm_param_paths(self.env_name)
        
        ssm.StringParameter(
            self, "LimitedSupervisorAgentIdParam",
            parameter_name=params.limited_supervisor_agent_id,
            string_value=limited_agent.attr_agent_id,
            description=f"OSCAR limited supervisor agent ID for {self.env_name}"
        )
        
        ssm.StringParameter(
            self, "LimitedSupervisorAgentAliasParam",
            parameter_name=params.limited_supervisor_agent_alias,
            string_value=limited_alias.attr_agent_alias_id,
            description=f"OSCAR limited supervisor agent alias ID for {self.env_name}"
        )
