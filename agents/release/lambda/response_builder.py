#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""
Response Builder for the Release Lambda.

Creates responses in the format expected by the Bedrock agent action-group
invocation contract.
"""

import json
import logging
from typing import Any, Dict

from config import config

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def create_response(event: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """Create a response in the format expected by the Bedrock agent."""
    action_group = event.get('actionGroup')
    function = event.get('function', 'unknown')

    response_body_string = json.dumps(result, default=str)

    return {
        "messageVersion": config.bedrock_message_version,
        "response": {
            "actionGroup": action_group,
            "function": function,
            "functionResponse": {
                "responseBody": {
                    "TEXT": {
                        "body": response_body_string
                    }
                }
            }
        }
    }
