#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""
Configuration for OSCAR Newsletter Handler.

Credentials are read from the central secret.
Collaborator agent IDs are read from SSM Parameter Store.
Other config comes from CDK Lambda environment variables.
"""

import json
import logging
import os
from typing import Any, Dict, Optional

import boto3

logger = logging.getLogger(__name__)


class Config:
    """Configuration for the newsletter handler Lambda."""

    def __init__(self) -> None:
        # Credentials from central secret
        secrets = self._load_from_central_secret()
        self.slack_bot_token = secrets.get('SLACK_BOT_TOKEN', '')
        self.channel_allow_list = [
            c.strip() for c in secrets.get('CHANNEL_ALLOW_LIST', '').split(',') if c.strip()
        ]

        if not self.slack_bot_token:
            raise ValueError("SLACK_BOT_TOKEN not found in central secret")

        # Infrastructure (set by CDK)
        self.region = os.environ.get('AWS_REGION', 'us-east-1')
        self.bedrock_message_version = os.environ.get('BEDROCK_RESPONSE_MESSAGE_VERSION', '1.0')
        self.log_level = os.environ.get('LOG_LEVEL', 'INFO')

        # Collaborator agent IDs (loaded from SSM)
        self._load_agent_ids_from_ssm()

    def _load_from_central_secret(self) -> Dict[str, str]:
        """Read credentials from the central secret."""
        keys_to_extract = {'SLACK_BOT_TOKEN', 'CHANNEL_ALLOW_LIST'}
        result: Dict[str, str] = {}

        secret_name = os.environ.get('CENTRAL_SECRET_NAME')
        if not secret_name:
            logger.error("CENTRAL_SECRET_NAME environment variable is not set")
            return result

        try:
            client = boto3.client(
                'secretsmanager',
                region_name=os.getenv('AWS_REGION', 'us-east-1')
            )
            response = client.get_secret_value(SecretId=secret_name)
            secret_data = json.loads(response['SecretString'])

            for key in keys_to_extract:
                if key in secret_data:
                    result[key] = str(secret_data[key])

            logger.info(f"Loaded {len(result)} keys from central secret")
        except Exception as e:
            logger.error(f"Failed to load central secret '{secret_name}': {e}")

        return result

    def _load_agent_ids_from_ssm(self) -> None:
        """Load collaborator agent IDs from SSM Parameter Store."""
        ssm_client = boto3.client('ssm', region_name=self.region)

        # Metrics agent
        self.metrics_agent_id = self._get_ssm_param(
            ssm_client, os.environ.get('OSCAR_METRICS_AGENT_ID_PARAM_PATH')
        )
        self.metrics_agent_alias_id = self._get_ssm_param(
            ssm_client, os.environ.get('OSCAR_METRICS_AGENT_ALIAS_PARAM_PATH')
        )

        # GitHub agent (may not exist yet)
        self.github_agent_id = self._get_ssm_param(
            ssm_client, os.environ.get('OSCAR_GITHUB_AGENT_ID_PARAM_PATH')
        )
        self.github_agent_alias_id = self._get_ssm_param(
            ssm_client, os.environ.get('OSCAR_GITHUB_AGENT_ALIAS_PARAM_PATH')
        )

        logger.info(
            f"Agent IDs loaded - Metrics: {self.metrics_agent_id}/{self.metrics_agent_alias_id}, "
            f"GitHub: {self.github_agent_id}/{self.github_agent_alias_id}"
        )

    def _get_ssm_param(self, ssm_client: Any, param_path: Optional[str]) -> Optional[str]:
        """Get a single SSM parameter value, returning None if not available."""
        if not param_path:
            return None
        try:
            response = ssm_client.get_parameter(Name=param_path)
            return response['Parameter']['Value']
        except Exception as e:
            logger.warning(f"Failed to load SSM param '{param_path}': {e}")
            return None

    @property
    def has_metrics_agent(self) -> bool:
        return bool(self.metrics_agent_id and self.metrics_agent_alias_id)

    @property
    def has_github_agent(self) -> bool:
        return bool(self.github_agent_id and self.github_agent_alias_id)


class _ConfigProxy:
    """Proxy that lazily initializes and caches config per Lambda invocation."""

    def __init__(self) -> None:
        self._cached_config: Optional[Config] = None
        self._lambda_request_id: Optional[str] = None
        self.aws_request_id: Optional[str] = None

    def set_request_id(self, request_id: str) -> None:
        self.aws_request_id = request_id

    def __getattr__(self, name: str) -> Any:
        if self._cached_config is None or (
            self.aws_request_id and self._lambda_request_id != self.aws_request_id
        ):
            self._cached_config = Config()
            self._lambda_request_id = self.aws_request_id
        return getattr(self._cached_config, name)


config = _ConfigProxy()
