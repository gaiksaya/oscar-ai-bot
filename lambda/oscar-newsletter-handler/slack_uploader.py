# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Slack file upload helper for the newsletter handler.

Pulls SLACK_BOT_TOKEN and CHANNEL_ALLOW_LIST from the central secret the same
way the communication handler does.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import boto3
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)

_secrets_client = boto3.client("secretsmanager")

_cache: Dict[str, Any] = {"token": None, "allowlist": None}


def _load_slack_config() -> Dict[str, Any]:
    """Fetch the Slack bot token and channel allowlist from the central secret."""
    if _cache["token"] is not None:
        return _cache

    secret_name = os.environ.get("CENTRAL_SECRET_NAME")
    if not secret_name:
        raise RuntimeError("CENTRAL_SECRET_NAME environment variable is not set")

    response = _secrets_client.get_secret_value(SecretId=secret_name)
    secret_data = json.loads(response["SecretString"])

    token = secret_data.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise RuntimeError("SLACK_BOT_TOKEN not found in central secret")

    allowlist_raw = secret_data.get("CHANNEL_ALLOW_LIST", "")
    allowlist = [c.strip() for c in allowlist_raw.split(",") if c.strip()]

    _cache["token"] = token
    _cache["allowlist"] = allowlist
    logger.info(f"Loaded Slack config (allowlist size={len(allowlist)})")
    return _cache


def _validate_channel(channel: str, allowlist: List[str]) -> bool:
    """Return True if channel ID or name is in the allowlist."""
    if not channel or not allowlist:
        return False
    normalized = channel.lstrip("#").strip()
    for allowed in allowlist:
        if allowed.lstrip("#").strip() == normalized:
            return True
    return False


def upload_markdown(
    channel: str,
    file_content: str,
    filename: str,
    initial_comment: Optional[str] = None,
    title: Optional[str] = None,
    thread_ts: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload markdown content as a file to a Slack channel.

    Validates the channel against the allowlist. When thread_ts is provided,
    the file is posted as a threaded reply instead of a top-level message.
    Returns a dict with `success`, `channel`, `file_id`, or `error`.
    """
    try:
        cfg = _load_slack_config()
    except Exception as e:
        logger.error(f"SLACK_UPLOADER: Failed to load Slack config: {e}")
        return {"success": False, "error": str(e)}

    if not _validate_channel(channel, cfg["allowlist"]):
        msg = f"Channel '{channel}' is not in the allowed channels list"
        logger.error(f"SLACK_UPLOADER: {msg}")
        return {"success": False, "error": msg}

    client = WebClient(token=cfg["token"])
    content_bytes = file_content.encode("utf-8") if isinstance(file_content, str) else file_content

    logger.info(
        f"SLACK_UPLOADER: Uploading channel={channel}, filename={filename}, size={len(content_bytes)} bytes"
    )

    try:
        kwargs: Dict[str, Any] = {
            "channel": channel,
            "content": content_bytes,
            "filename": filename,
        }
        if initial_comment:
            kwargs["initial_comment"] = initial_comment
        if title:
            kwargs["title"] = title
        if thread_ts:
            kwargs["thread_ts"] = thread_ts

        response = client.files_upload_v2(**kwargs)
        files = response.get("files") or ([response.get("file")] if response.get("file") else [])
        file_id = files[0].get("id") if files and files[0] else None

        logger.info(f"SLACK_UPLOADER: Upload succeeded ok={response.get('ok')}, file_id={file_id}")
        return {"success": True, "channel": channel, "file_id": file_id}

    except SlackApiError as e:
        err = e.response.get("error", str(e))
        logger.error(f"SLACK_UPLOADER: Slack API error: {err}")
        return {"success": False, "error": f"Slack API error: {err}"}
    except Exception as e:
        logger.error(f"SLACK_UPLOADER: Unexpected error: {e}", exc_info=True)
        return {"success": False, "error": f"Unexpected error: {e}"}
