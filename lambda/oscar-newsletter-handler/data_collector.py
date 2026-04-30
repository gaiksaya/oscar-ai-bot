# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Data collector — calls Metrics Agent 7 times to gather newsletter raw data."""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Any, Dict, Tuple

import boto3
from botocore.config import Config as BotoConfig

from config import DEFAULT_PIPELINE

logger = logging.getLogger(__name__)

# Bedrock agent invocations with large streaming payloads can take well beyond
# the default 60s read timeout. Give each call up to 4 minutes.
_bedrock_runtime = boto3.client(
    "bedrock-agent-runtime",
    config=BotoConfig(
        read_timeout=600,
        connect_timeout=10,
        retries={"max_attempts": 2, "mode": "standard"},
    ),
)
_ssm = boto3.client("ssm")


def _get_agent_config() -> Tuple[str, str]:
    """Read Metrics Agent ID and Alias from SSM at runtime."""
    agent_id_param = os.environ.get("METRICS_AGENT_ID_PARAM_PATH")
    agent_alias_param = os.environ.get("METRICS_AGENT_ALIAS_PARAM_PATH")
    logger.info(
        f"DATA_COLLECTOR: Looking up Metrics Agent SSM params "
        f"id_path={agent_id_param} alias_path={agent_alias_param}"
    )

    if not agent_id_param or not agent_alias_param:
        raise RuntimeError(
            "METRICS_AGENT_ID_PARAM_PATH and METRICS_AGENT_ALIAS_PARAM_PATH must be set"
        )

    agent_id = _ssm.get_parameter(Name=agent_id_param)["Parameter"]["Value"]
    agent_alias = _ssm.get_parameter(Name=agent_alias_param)["Parameter"]["Value"]
    logger.info(f"DATA_COLLECTOR: Resolved Metrics Agent id={agent_id} alias={agent_alias}")
    return agent_id, agent_alias


def _month_to_number(month: str) -> str:
    """Convert month name or number to zero-padded two-digit string."""
    try:
        num = int(month)
        if 1 <= num <= 12:
            return f"{num:02d}"
    except ValueError:
        pass
    return datetime.strptime(month.strip()[:3], "%b").strftime("%m")


def _previous_month(month: str, year: str) -> Tuple[str, str]:
    """Return (previous_month_name, previous_year) given current month/year."""
    dt = datetime.strptime(f"{_month_to_number(month)}-{year}", "%m-%Y")
    if dt.month == 1:
        prev = dt.replace(year=dt.year - 1, month=12)
    else:
        prev = dt.replace(month=dt.month - 1)
    return prev.strftime("%B"), str(prev.year)


def _invoke_agentic_query(agent_id: str, agent_alias: str, query: str, index: str) -> Dict[str, Any]:
    """Invoke the Metrics Agent's agentic_query function via Bedrock."""
    prompt = (
        f"Call agentic_query with the following parameters and return ONLY the raw JSON "
        f"response verbatim per the RAW RESPONSE MODE in your instructions — no prose, "
        f"no markdown fences, nothing before the opening brace or after the closing brace:\n"
        f"query: {query}\n"
        f"index: {index}\n"
        f"pipeline: {DEFAULT_PIPELINE}\n"
        f"return_raw: true"
    )

    session_id = str(uuid.uuid4())
    logger.info(
        f"DATA_COLLECTOR: Invoking metrics agent "
        f"agent_id={agent_id} alias={agent_alias} session={session_id[:8]} "
        f"index={index} query='{query[:120]}...'"
    )

    response = _bedrock_runtime.invoke_agent(
        agentId=agent_id,
        agentAliasId=agent_alias,
        sessionId=session_id,
        inputText=prompt,
    )

    # Parse streaming response
    completion = ""
    chunk_count = 0
    for event in response.get("completion", []):
        chunk = event.get("chunk", {})
        if "bytes" in chunk:
            completion += chunk["bytes"].decode("utf-8")
            chunk_count += 1

    logger.info(
        f"DATA_COLLECTOR: Metrics agent returned "
        f"chunks={chunk_count} completion_len={len(completion)}"
    )
    # Log full completion at INFO so we can diagnose parse issues
    logger.info(f"DATA_COLLECTOR: Raw completion for index={index}: {completion[:4000]}")

    # Try to extract JSON from the completion
    try:
        start = completion.find("{")
        end = completion.rfind("}")
        if start >= 0 and end > start:
            # strict=False allows literal newlines and control characters inside
            # strings — the Metrics Agent's raw OpenSearch payloads routinely
            # include \n inside issue body text.
            parsed = json.loads(completion[start: end + 1], strict=False)
            logger.info(
                f"DATA_COLLECTOR: Parsed JSON for index={index} — "
                f"keys={list(parsed.keys()) if isinstance(parsed, dict) else 'not-a-dict'}"
            )
            if isinstance(parsed, dict):
                hits_count = parsed.get("hits", {}).get("total", {}).get("value") if "hits" in parsed else None
                aggs = parsed.get("aggregations") or {}
                aggs_keys = list(aggs.keys()) if isinstance(aggs, dict) else []
                results_count = len(parsed.get("results", [])) if isinstance(parsed.get("results"), list) else None
                logger.info(
                    f"DATA_COLLECTOR: index={index} — "
                    f"total_hits={parsed.get('total_hits', hits_count)}, "
                    f"results_len={results_count}, aggregation_keys={aggs_keys}"
                )
            return parsed
        else:
            logger.warning(
                f"DATA_COLLECTOR: No JSON braces found in completion for index={index}"
            )
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(
            f"DATA_COLLECTOR: Could not parse JSON from agent response for "
            f"index={index}: {e}"
        )

    return {"raw_completion": completion, "error": "Could not parse agent response"}


def _safe_invoke(agent_id: str, agent_alias: str, query: str, index: str, section: str) -> Dict[str, Any]:
    """Invoke with error handling — returns empty dict on failure."""
    try:
        result = _invoke_agentic_query(agent_id, agent_alias, query, index)
        if "error" in result and "raw_completion" in result:
            logger.warning(
                f"DATA_COLLECTOR: section={section} returned an unparseable response"
            )
        return result
    except Exception as e:
        logger.error(f"DATA_COLLECTOR: Failed for section={section}: {e}", exc_info=True)
        return {}


def collect(month: str, year: str) -> Dict[str, Any]:
    """Call the Metrics Agent 7 times in parallel to gather all newsletter data.

    Returns dict of raw results keyed by section name.
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    agent_id, agent_alias = _get_agent_config()
    mm = _month_to_number(month)
    activity_index = f"github-user-activity-events-{mm}-{year}"
    prev_month, prev_year = _previous_month(month, year)

    logger.info(
        f"DATA_COLLECTOR: Collecting for {month} {year} "
        f"(activity_index={activity_index}, prev={prev_month} {prev_year})"
    )

    sections = [
        (
            "new_maintainers",
            "github_issues",
            f"Show all closed issues in .github repository for {month} {year} with label "
            f"github-request. Should have add/adding maintainer in the body AND type of "
            f"request User Permission or repository Management in the body.",
        ),
        (
            "new_repositories",
            "github_issues",
            f"Show all closed issues in .github repository with label repository-request "
            f"for {month} {year}",
        ),
        (
            "pr_data",
            "github_pulls",
            f"Show all pull requests grouped by user_login with count, total additions "
            f"and deletions for {month} {year}",
        ),
        (
            "activity_events",
            activity_index,
            f"Show document count grouped by sender and then by type for {month} {year}. "
            f"Do not use value_count or any metric aggregation — just the default doc_count "
            f"from terms aggregations.",
        ),
        (
            "stale_prs",
            "github_pulls",
            "Show top 5 repositories by count of open pull requests not updated in last 60 days",
        ),
        (
            "untriaged_issues",
            "github_issues",
            "Show top 5 repositories with the highest count of open issues with label "
            "untriaged older than 14 days, grouped by repository",
        ),
        (
            "pr_trend",
            "github_pulls",
            f"Show total count of pull requests created in {month} {year} and "
            f"total count created in {prev_month} {prev_year}",
        ),
    ]

    def _run_section(idx: int, section: str, index: str, query: str) -> Tuple[str, Dict[str, Any], float]:
        start = time.time()
        logger.info(
            f"DATA_COLLECTOR: [{idx}/{len(sections)}] Starting section='{section}' "
            f"index='{index}'"
        )
        result = _safe_invoke(agent_id, agent_alias, query, index, section)
        elapsed = time.time() - start
        is_empty = not result or (
            "error" in result and "raw_completion" in result
        )
        logger.info(
            f"DATA_COLLECTOR: [{idx}/{len(sections)}] Finished section='{section}' "
            f"in {elapsed:.2f}s — empty={is_empty}, "
            f"top_level_keys={list(result.keys()) if isinstance(result, dict) else 'n/a'}"
        )
        return section, result, elapsed

    results: Dict[str, Any] = {}
    total_started = time.time()

    # 7 parallel workers so all calls run concurrently. Each Bedrock invoke is
    # IO-bound (streaming from Bedrock), so threading gives near-linear speedup.
    with ThreadPoolExecutor(max_workers=len(sections)) as executor:
        futures = {
            executor.submit(_run_section, idx, section, index, query): section
            for idx, (section, index, query) in enumerate(sections, 1)
        }
        for future in as_completed(futures):
            section = futures[future]
            try:
                name, result, elapsed = future.result()
                results[name] = result
            except Exception as e:
                logger.error(f"DATA_COLLECTOR: section={section} raised: {e}", exc_info=True)
                results[section] = {}

    total_elapsed = time.time() - total_started
    logger.info(
        f"DATA_COLLECTOR: Collected {len(results)} sections in {total_elapsed:.2f}s "
        f"total (parallel) — section_keys={list(results.keys())}"
    )
    return results
