#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""
Response builder for Newsletter Handler.
"""

from typing import Any, Dict

from config import config


class ResponseBuilder:
    """Builds standardized responses for Bedrock agent."""

    @staticmethod
    def create_success_response(action_group: str, function_name: str, message: str) -> Dict[str, Any]:
        """Create a success response for Bedrock agent."""
        return {
            "messageVersion": config.bedrock_message_version,
            "response": {
                "actionGroup": action_group,
                "function": function_name,
                "functionResponse": {
                    "responseBody": {
                        "TEXT": {
                            "body": message
                        }
                    }
                }
            }
        }

    @staticmethod
    def create_error_response(action_group: str, function_name: str, error_message: str) -> Dict[str, Any]:
        """Create an error response for Bedrock agent."""
        return {
            "messageVersion": config.bedrock_message_version,
            "response": {
                "actionGroup": action_group,
                "function": function_name,
                "functionResponse": {
                    "responseBody": {
                        "TEXT": {
                            "body": f'ERROR! {error_message}'
                        }
                    }
                }
            }
        }
