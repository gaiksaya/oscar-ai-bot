#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""
Lambda handler for Newsletter Handler.
"""

import json
import logging
from typing import Any, Dict

from config import config
from orchestrator import NewsletterOrchestrator
from response_builder import ResponseBuilder
from slack_sdk import WebClient

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda handler for newsletter generation.

    Args:
        event: Lambda event containing the action group request
        context: Lambda context

    Returns:
        Response for the Bedrock agent
    """
    action_group = ''
    function_name = ''

    try:
        if context:
            config.set_request_id(context.aws_request_id)

        logger.info(f"Received event: {json.dumps(event, indent=2)}")

        action_group = event.get('actionGroup', '')
        function_name = event.get('function', '')
        parameters = event.get('parameters', [])

        # Convert parameters list to dictionary
        params = {}
        for param in parameters:
            params[param['name']] = param['value']

        logger.info(f"Processing action: {action_group}, function: {function_name}, params: {params}")

        response_builder = ResponseBuilder()

        if function_name == 'generate_newsletter':
            return _handle_generate_newsletter(params, action_group, function_name, response_builder)
        else:
            logger.error(f"Unknown function: {function_name}")
            return response_builder.create_error_response(
                action_group, function_name, f'Unknown function: {function_name}'
            )

    except Exception as e:
        logger.error(f"Error in lambda_handler: {e}", exc_info=True)
        response_builder = ResponseBuilder()
        return response_builder.create_error_response(
            action_group, function_name, f'Internal server error: {str(e)}'
        )


def _handle_generate_newsletter(
    params: Dict[str, str],
    action_group: str,
    function_name: str,
    response_builder: ResponseBuilder
) -> Dict[str, Any]:
    """Handle the generate_newsletter function call.

    Generates the newsletter, posts it to Slack directly (to avoid Bedrock response
    size limits), and returns a short confirmation to the supervisor.
    """
    month = params.get('month', '')
    year = params.get('year', '')
    sections_str = params.get('sections', 'all')

    if not month or not year:
        return response_builder.create_error_response(
            action_group, function_name,
            'Both "month" and "year" parameters are required. Example: month=March, year=2026'
        )

    sections = None if sections_str == 'all' else [s.strip() for s in sections_str.split(',')]

    logger.info(f"Generating newsletter for {month} {year}, sections: {sections or 'all'}")

    orchestrator = NewsletterOrchestrator()
    newsletter = orchestrator.generate(month, year, sections)

    # Post the newsletter directly to Slack to avoid Bedrock response size limits
    target_channel = params.get('target_channel')
    if target_channel and config.slack_bot_token:
        try:
            slack_client = WebClient(token=config.slack_bot_token)
            _post_newsletter_to_slack(slack_client, target_channel, newsletter, month, year)
            return response_builder.create_success_response(
                action_group, function_name,
                f'Newsletter for {month} {year} has been posted to the channel.'
            )
        except Exception as e:
            logger.error(f"Failed to post newsletter to Slack: {e}", exc_info=True)

    # If no target channel or Slack post failed, return the newsletter in the response
    # Truncate if too large for Bedrock response
    max_response_size = 20000
    if len(newsletter) > max_response_size:
        newsletter = newsletter[:max_response_size] + "\n\n[Newsletter truncated due to size limits]"

    return response_builder.create_success_response(
        action_group, function_name, newsletter
    )


def _post_newsletter_to_slack(
    slack_client: WebClient,
    channel: str,
    newsletter: str,
    month: str,
    year: str
) -> None:
    """Post the newsletter to Slack, splitting into multiple messages if needed."""
    max_message_length = 3900  # Slack limit is ~4000, leave margin

    if len(newsletter) <= max_message_length:
        slack_client.chat_postMessage(
            channel=channel,
            text=newsletter,
            unfurl_links=False,
            unfurl_media=False
        )
        return

    # Split into sections and post each as a separate message
    sections = newsletter.split('\n\n---\n\n')
    header = f"*[Open-Source] OpenSearch - {month} {year} Newsletter*\n\n"

    slack_client.chat_postMessage(
        channel=channel,
        text=header + "Newsletter sections follow in this thread:",
        unfurl_links=False,
        unfurl_media=False
    )

    for section in sections:
        if not section.strip():
            continue
        # Further split if a single section exceeds the limit
        while len(section) > max_message_length:
            split_point = section.rfind('\n', 0, max_message_length)
            if split_point == -1:
                split_point = max_message_length
            slack_client.chat_postMessage(
                channel=channel,
                text=section[:split_point],
                unfurl_links=False,
                unfurl_media=False
            )
            section = section[split_point:]

        if section.strip():
            slack_client.chat_postMessage(
                channel=channel,
                text=section,
                unfurl_links=False,
                unfurl_media=False
            )
