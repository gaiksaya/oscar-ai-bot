#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""
Configuration Management for the Release Lambda.

OPENSEARCH_HOST is read from the release agent's OWN secret
(RELEASE_STATE_SECRET_NAME) so credentials stay scoped to this agent. All other
config comes from CDK Lambda environment variables.
"""

import json
import logging
import os
from typing import Dict

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class ReleaseConfig:
    """Centralized configuration for the Release Lambda."""

    def __init__(self, validate_required: bool = True) -> None:
        # Release agent's own cross-account role ARN from CDK env var
        self.cross_account_role_arn = os.environ.get('RELEASE_STATE_CROSS_ACCOUNT_ROLE_ARN', '')

        # OpenSearch host from the release agent's own secret
        secrets = self._load_from_release_secret()
        self.opensearch_host = secrets.get('OPENSEARCH_HOST', '')

        if validate_required and not self.cross_account_role_arn:
            raise ValueError("RELEASE_STATE_CROSS_ACCOUNT_ROLE_ARN environment variable is not set")
        if validate_required and not self.opensearch_host:
            raise ValueError("OPENSEARCH_HOST not found in release secret")

        # AWS region
        self.region = os.environ.get('AWS_REGION', 'us-east-1')

        # OpenSearch SigV4 signing configuration (set by CDK)
        self.opensearch_region = os.environ.get('OPENSEARCH_REGION', 'us-east-1')
        self.opensearch_service = os.environ.get('OPENSEARCH_SERVICE', 'es')
        self.opensearch_request_timeout = int(os.environ.get('OPENSEARCH_REQUEST_TIMEOUT', 60))

        # Release-state index names (set by CDK; defaults match the Phase 0 mappings)
        self.release_state_index = os.environ.get('RELEASE_STATE_INDEX', 'opensearch_release_state')
        self.release_schedule_index = os.environ.get('RELEASE_SCHEDULE_INDEX', 'opensearch_release_schedule')

        # Response configuration
        self.bedrock_message_version = os.environ.get('BEDROCK_RESPONSE_MESSAGE_VERSION', '1.0')

        logger.info(f"Initialized ReleaseConfig - Region: {self.region}")

    def _load_from_release_secret(self) -> Dict[str, str]:
        """Load OPENSEARCH_HOST from the release agent's own secret (JSON format)."""
        keys_to_extract = {
            'OPENSEARCH_HOST',
        }
        result: Dict[str, str] = {}

        secret_name = os.environ.get('RELEASE_STATE_SECRET_NAME')
        if not secret_name:
            logger.error("RELEASE_STATE_SECRET_NAME environment variable is not set")
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

            logger.info(f"Loaded {len(result)} keys from release secret")
        except Exception as e:
            logger.error(f"Failed to load release secret '{secret_name}': {e}")

        return result

    def get_opensearch_host_clean(self) -> str:
        """Get OpenSearch host with https:// prefix removed."""
        return self.opensearch_host.replace('https://', '')


class _ConfigProxy:
    """Proxy that caches config per lambda execution."""
    def __init__(self):
        self._cached_config = None
        self.aws_request_id = None
        self._lambda_request_id = None

    def set_request_id(self, request_id: str) -> None:
        """Set the AWS Lambda request ID."""
        self.aws_request_id = request_id

    def __getattr__(self, name):
        if self._cached_config is None or (self.aws_request_id and self._lambda_request_id != self.aws_request_id):
            self._cached_config = ReleaseConfig(validate_required=False)
            self._lambda_request_id = self.aws_request_id

        return getattr(self._cached_config, name)


# Global configuration proxy
config = _ConfigProxy()
