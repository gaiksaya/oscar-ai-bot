#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""
Release Lambda Function.

AWS Lambda handler for the release agent. Routes the two read-only action-group
calls (get_release_status, get_release_window) to their handlers and wraps the
result in the Bedrock action-group response envelope.
"""

import logging
import traceback
import uuid
from typing import Any, Dict

from config import config
from release_handler import (handle_get_release_status,
                             handle_get_release_window)
from response_builder import create_response

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Main Lambda handler for the release agent."""
    if context and hasattr(context, 'aws_request_id'):
        config.set_request_id(context.aws_request_id)

    request_id = str(uuid.uuid4())[:8]

    try:
        function_name = event.get('function', '')
        parameters = event.get('parameters', [])
        logger.info(f"LAMBDA_HANDLER [{request_id}]: Function: '{function_name}', Params count: {len(parameters)}")

        # Convert parameters list to dict
        params: Dict[str, Any] = {}
        for param in parameters:
            if isinstance(param, dict) and 'name' in param and 'value' in param:
                params[param['name']] = param['value']

        match function_name:
            case 'get_release_status':
                result = handle_get_release_status(params, request_id)
            case 'get_release_window':
                result = handle_get_release_window(params, request_id)
            case _:
                result = {
                    'error': f'Unknown function: {function_name}',
                    'available_functions': ['get_release_status', 'get_release_window'],
                }

        return create_response(event, result)

    except Exception as e:
        logger.error(f"LAMBDA_HANDLER [{request_id}]: Exception occurred: {e}")
        logger.error(f"LAMBDA_HANDLER [{request_id}]: Stack trace: {traceback.format_exc()}")
        return create_response(event, {'error': str(e), 'type': 'lambda_error'})
