#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""
Bedrock Agent Core Module for OSCAR Agent.

This module handles the core Bedrock agent invocation, session management,
and response processing for the OSCAR agent system.
"""

import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

import boto3
from botocore.exceptions import ClientError

from config import config

logger = logging.getLogger(__name__)


class BedrockAgentCore:
    """Core Bedrock agent invocation and session management."""
    
    def __init__(self, region: Optional[str] = None) -> None:
        """
        Initialize the Bedrock agent core.
        
        Args:
            region: AWS region for Bedrock service, defaults to config value
        """
        self.region = region or config.region
        self.client = boto3.client('bedrock-agent-runtime', region_name=self.region)
        
        # Privileged supervisor agent configuration (current full-featured agent)
        self.privileged_agent_id = config.oscar_privileged_bedrock_agent_id
        self.privileged_agent_alias_id = config.oscar_privileged_bedrock_agent_alias_id
        
        # Limited supervisor agent configuration (restricted capabilities)
        self.limited_agent_id = config.oscar_limited_bedrock_agent_id
        self.limited_agent_alias_id = config.oscar_limited_bedrock_agent_alias_id
        
        # Timeout and retry settings
        self.timeout = config.agent_timeout
        self.max_retries = config.agent_max_retries
        
        logger.info(
            f"Initialized BedrockAgentCore - Privileged ID: {self.privileged_agent_id}, "
            f"Privileged Alias: {self.privileged_agent_alias_id}, "
            f"Limited ID: {self.limited_agent_id}, "
            f"Limited Alias: {self.limited_agent_alias_id}, "
            f"Region: {self.region}"
        )
    
    def create_agent_request(self, query: str, privilege: bool, session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Create a request for the Bedrock agent.
        
        Args:
            query: The user's query
            session_id: Optional session ID for maintaining conversation context
            
        Returns:
            A dictionary containing the request parameters
        """
        agent_id = self.limited_agent_id
        alias_id = self.limited_agent_alias_id
        if privilege:
            agent_id = self.privileged_agent_id
            alias_id = self.privileged_agent_alias_id
        request = {
            'agentId': agent_id,
            'agentAliasId': alias_id,
            'inputText': query,
            'sessionId': session_id or f"session-{int(time.time())}",
            'enableTrace': True  # Enable trace to see raw model output
        }
        
        return request
    
    def invoke_agent(self, query: str, privilege: bool, session_id: Optional[str] = None) -> Tuple[str, Optional[str]]:
        """
        Invoke the Bedrock agent with the given query.
        
        Args:
            query: The user's query
            privilege: Whether to use privileged or limited agent
            session_id: Optional session ID for maintaining conversation context
            
        Returns:
            A tuple containing (response_text, session_id)
            
        Raises:
            Exception: If the agent invocation fails after all retries
        """
        request = self.create_agent_request(query, privilege, session_id)
        logger.info(f"Invoking agent with request: {json.dumps({k: v for k, v in request.items() if k != 'inputText'}, indent=2)}")
        logger.info(f"Query: {query[:config.log_query_preview_length]}...")
        
        try:
            response = self.client.invoke_agent(**request)
            
            # Log the full response structure for debugging
            logger.info(f"Full response keys: {response.keys()}")
            
            # Process the streaming response
            response_text = ""
            returned_session_id = None
            all_events = []
            
            if 'completion' in response:
                logger.info("Found 'completion' in response")
                for event in response['completion']:
                    all_events.append(event)
                    logger.info(f"Event keys: {event.keys()}")
                    
                    # Log trace events to see raw model output
                    if 'trace' in event:
                        logger.info(f"TRACE EVENT: {json.dumps(event['trace'], default=str, indent=2)}")
                        
                        # Extract response from AgentCommunication__sendMessage in trace
                        trace = event['trace'].get('trace', {})
                        if 'orchestrationTrace' in trace:
                            orch_trace = trace['orchestrationTrace']
                            
                            # Check for model invocation output with tool use
                            if 'modelInvocationOutput' in orch_trace:
                                model_output = orch_trace['modelInvocationOutput']
                                if 'rawResponse' in model_output:
                                    raw_content = model_output['rawResponse'].get('content', '')
                                    try:
                                        # Parse the JSON response
                                        parsed = json.loads(raw_content)
                                        message_content = parsed.get('output', {}).get('message', {}).get('content', [])
                                        
                                        # Look for AgentCommunication__sendMessage tool use
                                        for content_item in message_content:
                                            if 'toolUse' in content_item:
                                                tool_use = content_item.get('toolUse')
                                                if tool_use and tool_use.get('name') == 'AgentCommunication__sendMessage':
                                                    tool_input = tool_use.get('input', {})
                                                    if tool_input.get('recipient') == 'User':
                                                        # Extract the actual response content
                                                        extracted_content = tool_input.get('content', '')
                                                        if extracted_content:
                                                            response_text = extracted_content
                                                            logger.info(f"Extracted response from AgentCommunication tool: {extracted_content[:200]}...")
                                    except json.JSONDecodeError as e:
                                        logger.warning(f"Failed to parse raw response: {e}")
                    
                    logger.info(f"Full event: {json.dumps(event, default=str, indent=2)}")
                    
                    if 'chunk' in event:
                        chunk = event['chunk']
                        logger.info(f"Chunk keys: {chunk.keys()}")
                        logger.info(f"Full chunk: {json.dumps(chunk, default=str, indent=2)}")
                        if 'bytes' in chunk:
                            chunk_text = chunk['bytes'].decode('utf-8')
                            # Only use chunk text if we haven't extracted from tool use
                            if not response_text:
                                response_text += chunk_text
                            logger.info(f"Added chunk text (first 500 chars): {chunk_text[:500]}")

                        # Extract session ID from the chunk if available
                        if 'sessionId' in chunk:
                            returned_session_id = chunk['sessionId']
                            logger.debug(f"Found session ID in chunk: {returned_session_id}")
            else:
                logger.warning("No 'completion' key in response")
            
            logger.info(f"Total events processed: {len(all_events)}")

            # Also check for session ID at the top level of the response
            if 'sessionId' in response:
                returned_session_id = response['sessionId']
                logger.debug(f"Found session ID at top level: {returned_session_id}")

            # If no session ID found in response, use the one from request
            elif not returned_session_id and session_id:
                returned_session_id = session_id
                logger.debug(f"Using request session ID: {returned_session_id}")

            # If still no session ID, generate one for consistency
            else:
                returned_session_id = f"session-{int(time.time())}"
                logger.debug(f"Generated new session ID: {returned_session_id}")
            logger.info(f"Response: {response_text}")
            logger.info(f"Agent response received, length: {len(response_text)} characters")
            logger.info(f"Final session ID: {returned_session_id}")
            
            return response_text.strip(), returned_session_id
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            logger.error(f"Bedrock agent error ({error_code}): {error_message}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error invoking agent: {e}", exc_info=True)
            raise