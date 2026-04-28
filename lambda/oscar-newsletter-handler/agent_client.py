#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""
Agent Client for Newsletter Handler.

Invokes collaborator agents (Metrics, GitHub) via Bedrock API
to gather data for newsletter sections.
"""

import logging
import time
from typing import Optional

import boto3
from botocore.exceptions import ClientError
from config import config

logger = logging.getLogger(__name__)


class AgentClient:
    """Invokes collaborator agents via Bedrock API."""

    def __init__(self) -> None:
        self.client = boto3.client('bedrock-agent-runtime', region_name=config.region)

    def query_metrics(self, prompt: str, session_id: Optional[str] = None) -> Optional[str]:
        """Invoke the metrics agent with a natural language prompt."""
        if not config.has_metrics_agent:
            logger.warning("Metrics agent not configured, skipping query")
            return None

        return self._invoke(
            config.metrics_agent_id,
            config.metrics_agent_alias_id,
            prompt,
            session_id
        )

    def query_github(self, prompt: str, session_id: Optional[str] = None) -> Optional[str]:
        """Invoke the GitHub agent with a natural language prompt."""
        if not config.has_github_agent:
            logger.warning("GitHub agent not configured, skipping query")
            return None

        return self._invoke(
            config.github_agent_id,
            config.github_agent_alias_id,
            prompt,
            session_id
        )

    def _invoke(
        self,
        agent_id: str,
        alias_id: str,
        prompt: str,
        session_id: Optional[str] = None
    ) -> str:
        """Core Bedrock agent invocation.

        Args:
            agent_id: Bedrock agent ID
            alias_id: Bedrock agent alias ID
            prompt: Natural language prompt
            session_id: Optional session ID for multi-turn conversations

        Returns:
            Agent response text

        Raises:
            ClientError: If the Bedrock API call fails
        """
        request_session_id = session_id or f"newsletter-{int(time.time())}"

        logger.info(
            f"Invoking agent {agent_id} with prompt: {prompt[:100]}..."
        )

        try:
            response = self.client.invoke_agent(
                agentId=agent_id,
                agentAliasId=alias_id,
                sessionId=request_session_id,
                inputText=prompt,
                enableTrace=True
            )

            response_text = ""

            if 'completion' in response:
                for event in response['completion']:
                    if 'chunk' in event:
                        chunk = event['chunk']
                        if 'bytes' in chunk:
                            response_text += chunk['bytes'].decode('utf-8')

            logger.info(f"Agent {agent_id} response length: {len(response_text)} characters")
            return response_text.strip()

        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            logger.error(f"Bedrock agent error ({error_code}): {error_message}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error invoking agent {agent_id}: {e}", exc_info=True)
            raise
