#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Bedrock action group handler for newsletter generation.

Runs in two modes:
1. Synchronous (Bedrock action-group invocation): validates inputs, async-invokes
   self, returns a 'started' acknowledgment in ~500ms. The supervisor sees a
   fast response and clears Slack reactions cleanly.
2. Asynchronous (self-invoked via InvocationType='Event'): does the heavy
   work — 7 Metrics Agent calls, rendering, Slack upload. Posts an error to
   the thread if it fails. Return value is discarded.
"""

import json
import logging
import os
import traceback
from typing import Any, Dict

import boto3

import newsletter_processor
import slack_uploader
from config import BEDROCK_MESSAGE_VERSION

logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_lambda_client = boto3.client("lambda")
_ASYNC_MARKER = "_oscar_newsletter_async_worker"


def _parse_params(event: Dict[str, Any]) -> Dict[str, Any]:
    """Parse Bedrock action group parameters into a dict."""
    params: Dict[str, Any] = {}
    for p in event.get("parameters", []):
        if isinstance(p, dict) and "name" in p and "value" in p:
            params[p["name"]] = p["value"]
    return params


def _create_response(event: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    """Format response for Bedrock agent."""
    return {
        "messageVersion": BEDROCK_MESSAGE_VERSION,
        "response": {
            "actionGroup": event.get("actionGroup"),
            "function": event.get("function", "generate_newsletter"),
            "functionResponse": {
                "responseBody": {
                    "TEXT": {
                        "body": json.dumps(result, default=str)
                    }
                }
            }
        }
    }


def _post_error_to_thread(channel: str, thread_ts: Any, text: str) -> None:
    """Post a plain-text error message to the same Slack thread."""
    try:
        cfg = slack_uploader._load_slack_config()
        from slack_sdk import WebClient
        client = WebClient(token=cfg["token"])
        kwargs: Dict[str, Any] = {"channel": channel, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts
        client.chat_postMessage(**kwargs)
        logger.info(f"ASYNC_WORKER: Posted error to channel={channel}")
    except Exception as post_err:
        logger.error(f"ASYNC_WORKER: Failed to post error message to Slack: {post_err}")


def _run_async_worker(payload: Dict[str, Any]) -> None:
    """Async path — do the real work and post results to Slack directly.

    Any unrecoverable error is caught, logged, and posted back to the Slack
    thread so the user isn't left wondering.
    """
    month = payload.get("month")
    year = payload.get("year")
    target_channel = payload.get("target_channel")
    initial_comment = payload.get("initial_comment")
    thread_ts = payload.get("thread_ts")

    logger.info(
        f"ASYNC_WORKER: Starting newsletter generation for {month} {year} "
        f"-> channel={target_channel}, thread_ts={thread_ts}"
    )

    try:
        result = newsletter_processor.generate(
            month=month,
            year=year,
            target_channel=target_channel,
            initial_comment=initial_comment,
            thread_ts=thread_ts,
        )
        logger.info(
            f"ASYNC_WORKER: Completed — upload_success={result.get('upload_success')}, "
            f"counts={result.get('counts')}"
        )

        if not result.get("upload_success") and target_channel:
            error_msg = result.get("error", "Unknown error")
            logger.error(f"ASYNC_WORKER: Upload failed: {error_msg}")
            _post_error_to_thread(
                target_channel, thread_ts,
                f":warning: Newsletter for {month} {year} was generated but upload failed: "
                f"{error_msg}"
            )
    except Exception as e:
        logger.error(f"ASYNC_WORKER: Exception during generation: {e}")
        logger.error(traceback.format_exc())
        if target_channel:
            _post_error_to_thread(
                target_channel, thread_ts,
                f":warning: Newsletter generation for {month} {year} failed: {e}"
            )


def _lambda_handler_sync(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Bedrock sync path — validate, dispatch async, return fast."""
    function_name = event.get("function", "")
    action_group = event.get("actionGroup", "")
    logger.info(
        f"NEWSLETTER_HANDLER: Sync start — actionGroup={action_group}, function={function_name}"
    )
    logger.info(f"NEWSLETTER_HANDLER: Raw event: {json.dumps(event)[:2000]}")

    params = _parse_params(event)
    month = params.get("month")
    year = params.get("year")
    target_channel = params.get("target_channel")
    initial_comment = params.get("initial_comment")
    thread_ts = params.get("thread_ts")
    logger.info(
        f"NEWSLETTER_HANDLER: Parsed params — month={month}, year={year}, "
        f"target_channel={target_channel}, thread_ts={thread_ts}"
    )

    if function_name != "generate_newsletter":
        return _create_response(event, {"error": f"Unknown function: {function_name}"})
    if not month or not year:
        return _create_response(event, {"error": "month and year are required"})
    if not target_channel:
        return _create_response(event, {"error": "target_channel is required"})

    async_payload = {
        _ASYNC_MARKER: True,
        "payload": {
            "month": month,
            "year": year,
            "target_channel": target_channel,
            "initial_comment": initial_comment,
            "thread_ts": thread_ts,
        },
    }

    function_arn = context.invoked_function_arn if context else os.environ.get(
        "AWS_LAMBDA_FUNCTION_NAME"
    )
    logger.info(
        f"NEWSLETTER_HANDLER: Dispatching async worker for {month} {year} -> {target_channel}"
    )
    try:
        _lambda_client.invoke(
            FunctionName=function_arn,
            InvocationType="Event",
            Payload=json.dumps(async_payload).encode("utf-8"),
        )
    except Exception as e:
        logger.error(f"NEWSLETTER_HANDLER: Failed to dispatch async worker: {e}")
        return _create_response(event, {
            "error": f"Failed to dispatch newsletter worker: {e}",
            "type": "dispatch_error",
        })

    return _create_response(event, {
        "status": "started",
        "month": month,
        "year": year,
        "target_channel": target_channel,
        "message": (
            f"Generating {month} {year} newsletter now!"
        ),
    })


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Entry point — dispatches between sync (Bedrock) and async (self-invoke) modes."""
    if isinstance(event, dict) and event.get(_ASYNC_MARKER):
        _run_async_worker(event.get("payload", {}))
        return {"status": "done"}

    try:
        return _lambda_handler_sync(event, context)
    except Exception as e:
        logger.error(f"NEWSLETTER_HANDLER: Sync path exception: {e}")
        logger.error(traceback.format_exc())
        return _create_response(event, {"error": str(e), "type": "newsletter_error"})
