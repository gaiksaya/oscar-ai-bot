# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Data collector — runs 7 explicit OpenSearch DSL queries by invoking the
Metrics Lambda's `direct_query` handler directly via `lambda:Invoke`.

Bypasses Bedrock so there's no LLM in the transport path — deterministic,
fast, and free of the token-generation failure modes (index-name corruption,
streaming timeouts) we hit when routing data through an agent.
"""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple

import boto3
from botocore.config import Config as BotoConfig

logger = logging.getLogger(__name__)

# Direct Lambda invoke: each call is typically ~400ms (the Metrics Lambda's
# actual work). Keep a generous connection pool for our 7 parallel invokes.
_lambda_client = boto3.client(
    "lambda",
    config=BotoConfig(
        read_timeout=60,
        connect_timeout=10,
        retries={"max_attempts": 2, "mode": "standard"},
        max_pool_connections=10,
    ),
)


def _get_metrics_lambda_name() -> str:
    """Resolve the metrics Lambda function name from env."""
    explicit = os.environ.get("METRICS_LAMBDA_FUNCTION_NAME")
    if explicit:
        return explicit
    env_name = os.environ.get("OSCAR_ENV", "dev")
    return f"oscar-metrics-{env_name}"
_ssm = boto3.client("ssm")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _month_to_number(month: str) -> str:
    """Convert month name or number to zero-padded two-digit string."""
    try:
        num = int(month)
        if 1 <= num <= 12:
            return f"{num:02d}"
    except ValueError:
        pass
    return datetime.strptime(month.strip()[:3], "%b").strftime("%m")


def _month_date_range(month: str, year: str) -> Tuple[str, str]:
    """Return (gte, lt) ISO date strings for the given calendar month."""
    mm = _month_to_number(month)
    y = int(year)
    m = int(mm)
    start = datetime(y, m, 1)
    if m == 12:
        end = datetime(y + 1, 1, 1)
    else:
        end = datetime(y, m + 1, 1)
    return start.strftime("%Y-%m-%dT00:00:00Z"), end.strftime("%Y-%m-%dT00:00:00Z")


def _previous_month(month: str, year: str) -> Tuple[str, str]:
    """Return (previous_month_name, previous_year)."""
    dt = datetime.strptime(f"{_month_to_number(month)}-{year}", "%m-%Y")
    if dt.month == 1:
        prev = dt.replace(year=dt.year - 1, month=12)
    else:
        prev = dt.replace(month=dt.month - 1)
    return prev.strftime("%B"), str(prev.year)


def _invoke_direct_query(index: str, query_body: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke the Metrics Lambda's direct_query handler via lambda:Invoke.

    Constructs the same Bedrock action-group-shaped event that metrics Lambda
    expects, so `handle_direct_query` in metrics_handler.py receives the
    params exactly as it would from the Bedrock path.
    """
    function_name = _get_metrics_lambda_name()
    body_str = json.dumps(query_body)

    event = {
        "function": "direct_query",
        "actionGroup": "directSearchActionGroup",
        "parameters": [
            {"name": "index", "type": "string", "value": index},
            {"name": "query_body", "type": "string", "value": body_str},
        ],
    }

    logger.info(
        f"DATA_COLLECTOR: Invoking metrics Lambda '{function_name}' "
        f"index={index} body_bytes={len(body_str)}"
    )

    try:
        response = _lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(event).encode("utf-8"),
        )
    except Exception as e:
        logger.error(f"DATA_COLLECTOR: lambda:Invoke failed for index={index}: {e}")
        return {"error": f"lambda:Invoke failed: {e}", "type": "lambda_invoke_error"}

    status = response.get("StatusCode")
    payload_raw = response.get("Payload").read() if response.get("Payload") else b""
    payload_str = payload_raw.decode("utf-8") if payload_raw else ""
    logger.info(
        f"DATA_COLLECTOR: Metrics Lambda returned status={status} "
        f"payload_bytes={len(payload_str)}"
    )

    if response.get("FunctionError"):
        logger.error(
            f"DATA_COLLECTOR: Metrics Lambda FunctionError for index={index}: "
            f"{payload_str[:500]}"
        )
        return {"error": payload_str, "type": "metrics_lambda_error"}

    # The metrics Lambda wraps its result in the Bedrock action-group response
    # envelope: {messageVersion, response:{functionResponse:{responseBody:
    # {TEXT:{body: "<json string>"}}}}}.
    try:
        envelope = json.loads(payload_str)
    except json.JSONDecodeError as e:
        logger.warning(f"DATA_COLLECTOR: envelope JSON parse failed for index={index}: {e}")
        return {"error": f"envelope parse error: {e}", "raw_payload": payload_str[:500]}

    body_text = (
        envelope.get("response", {})
        .get("functionResponse", {})
        .get("responseBody", {})
        .get("TEXT", {})
        .get("body")
    )
    if body_text is None:
        logger.warning(
            f"DATA_COLLECTOR: no body in envelope for index={index}; "
            f"envelope_keys={list(envelope.keys())}"
        )
        return envelope

    try:
        parsed = json.loads(body_text, strict=False)
    except json.JSONDecodeError as e:
        logger.warning(f"DATA_COLLECTOR: body JSON parse failed for index={index}: {e}")
        return {"error": f"body parse error: {e}", "raw_body": body_text[:500]}

    hits_total = parsed.get("hits", {}).get("total", {}).get("value") if "hits" in parsed else None
    agg_keys = list((parsed.get("aggregations") or {}).keys())
    logger.info(
        f"DATA_COLLECTOR: Parsed response for index={index} — "
        f"hits_total={hits_total}, aggregation_keys={agg_keys}"
    )
    return parsed

def _safe_invoke(index: str, query_body: Dict[str, Any], section: str) -> Dict[str, Any]:
    """Invoke direct_query with defensive error handling."""
    try:
        return _invoke_direct_query(index, query_body)
    except Exception as e:
        logger.error(f"DATA_COLLECTOR: section={section} failed: {e}", exc_info=True)
        return {}


# ---------------------------------------------------------------------------
# DSL templates
# ---------------------------------------------------------------------------

def _build_dsl_new_maintainers(gte: str, lt: str) -> Dict[str, Any]:
    """Closed .github issues with github-request label, title matching
    'add or adding maintainer' with fuzziness, and body mentioning
    'User Permission'. DSL verified against the April 2026 data."""
    return {
        "size": 100,
        "query": {
            "bool": {
                "must": [
                    {"match": {"title": {"query": "add or adding maintainer", "fuzziness": "AUTO"}}},
                    {"match": {"body": {"query": "User Permission", "fuzziness": "AUTO"}}},
                ],
                "filter": [
                    {"term": {"state.keyword": "closed"}},
                    {"term": {"repository.keyword": ".github"}},
                    {"term": {"issue_labels.keyword": "github-request"}},
                    {"range": {"closed_at": {"gte": gte, "lt": lt}}},
                ],
            }
        },
    }


def _build_dsl_new_repositories(gte: str, lt: str) -> Dict[str, Any]:
    """Closed .github issues with repository-request label."""
    return {
        "size": 50,
        "_source": ["title", "html_url", "body", "user_login", "closed_at", "issue_labels"],
        "query": {
            "bool": {
                "filter": [
                    {"term": {"state.keyword": "closed"}},
                    {"term": {"repository.keyword": ".github"}},
                    {"term": {"issue_labels.keyword": "repository-request"}},
                    {"range": {"closed_at": {"gte": gte, "lt": lt}}},
                ]
            }
        },
    }


def _build_dsl_pr_data(gte: str, lt: str) -> Dict[str, Any]:
    """PRs grouped by user_login with per-user merged breakdown, top-5 repos
    (with sample PR titles per repo), and additions/deletions totals."""
    return {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"range": {"created_at": {"gte": gte, "lt": lt}}},
                ]
            }
        },
        "aggs": {
            "by_user_login": {
                "terms": {"field": "user_login.keyword", "size": 10000},
                "aggs": {
                    "total_additions": {"sum": {"field": "additions"}},
                    "total_deletions": {"sum": {"field": "deletions"}},
                    "by_merged": {
                        "terms": {"field": "merged", "size": 5}
                    },
                    "by_repository": {
                        "terms": {"field": "repository.keyword", "size": 5},
                        "aggs": {
                            "sample_titles": {
                                "top_hits": {
                                    "size": 10,
                                    "_source": {"includes": ["title"]},
                                    "sort": [{"created_at": "desc"}],
                                }
                            }
                        },
                    },
                },
            }
        },
    }


def _build_dsl_activity_events(gte: str, lt: str) -> Dict[str, Any]:
    """Activity events grouped by sender then by type (counts only)."""
    return {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"range": {"created_at": {"gte": gte, "lt": lt}}},
                ]
            }
        },
        "aggs": {
            "by_sender": {
                "terms": {"field": "sender.keyword", "size": 10000},
                "aggs": {
                    "by_type": {
                        "terms": {"field": "type.keyword", "size": 20}
                    }
                },
            }
        },
    }


def _build_dsl_stale_prs() -> Dict[str, Any]:
    """Top 5 repositories by count of open PRs not updated in the last 60 days."""
    cutoff = (datetime.utcnow() - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"state.keyword": "open"}},
                    {"range": {"updated_at": {"lt": cutoff}}},
                ]
            }
        },
        "aggs": {
            "by_repository": {
                "terms": {"field": "repository.keyword", "size": 5}
            }
        },
    }


def _build_dsl_untriaged_issues() -> Dict[str, Any]:
    """Top 5 repositories by count of open issues with untriaged label older than 14 days."""
    cutoff = (datetime.utcnow() - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"state.keyword": "open"}},
                    {"term": {"issue_labels.keyword": "untriaged"}},
                    {"range": {"created_at": {"lt": cutoff}}},
                ]
            }
        },
        "aggs": {
            "by_repository": {
                "terms": {"field": "repository.keyword", "size": 5}
            }
        },
    }


def _build_dsl_pr_trend(
    current_gte: str, current_lt: str, previous_gte: str, previous_lt: str
) -> Dict[str, Any]:
    """Total PR count for current and previous month — two filters aggregation.

    Excludes bot authors (user_login ending in `[bot]` or `-bot`) so counts
    align with LFX Insights.
    """
    bot_exclusions = {
        "must_not": [
            {"wildcard": {"user_login.keyword": "*[bot]"}},
            {"wildcard": {"user_login.keyword": "*-bot"}},
        ]
    }
    return {
        "size": 0,
        "aggs": {
            "current_month": {
                "filter": {
                    "bool": {
                        "filter": [
                            {"range": {"created_at": {"gte": current_gte, "lt": current_lt}}},
                        ],
                        **bot_exclusions,
                    }
                }
            },
            "previous_month": {
                "filter": {
                    "bool": {
                        "filter": [
                            {"range": {"created_at": {"gte": previous_gte, "lt": previous_lt}}},
                        ],
                        **bot_exclusions,
                    }
                }
            },
        },
    }


def _build_dsl_issue_trend(
    current_gte: str, current_lt: str, previous_gte: str, previous_lt: str
) -> Dict[str, Any]:
    """Issues CLOSED (resolved) in current vs previous month — two filters.

    Excludes pull requests (GitHub's REST API returns PRs as issues with
    `issue_pull_request: true`; LFX counts them separately).
    """
    return {
        "size": 0,
        "aggs": {
            "current_month": {
                "filter": {
                    "bool": {
                        "filter": [
                            {"range": {"closed_at": {"gte": current_gte, "lt": current_lt}}},
                            {"term": {"issue_pull_request": False}},
                        ]
                    }
                }
            },
            "previous_month": {
                "filter": {
                    "bool": {
                        "filter": [
                            {"range": {"closed_at": {"gte": previous_gte, "lt": previous_lt}}},
                            {"term": {"issue_pull_request": False}},
                        ]
                    }
                }
            },
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect(month: str, year: str) -> Dict[str, Any]:
    """Run 7 explicit-DSL queries in parallel via direct_query.

    Returns dict of raw OpenSearch responses keyed by section name.
    """
    mm = _month_to_number(month)
    activity_index = f"github-user-activity-events-{mm}-{year}"
    current_gte, current_lt = _month_date_range(month, year)
    prev_month, prev_year = _previous_month(month, year)
    previous_gte, previous_lt = _month_date_range(prev_month, prev_year)

    logger.info(
        f"DATA_COLLECTOR: Collecting for {month} {year} "
        f"(activity_index={activity_index}, current={current_gte}→{current_lt}, "
        f"previous={previous_gte}→{previous_lt})"
    )

    sections = [
        ("new_maintainers", "github_issues",
         _build_dsl_new_maintainers(current_gte, current_lt)),
        ("new_repositories", "github_issues",
         _build_dsl_new_repositories(current_gte, current_lt)),
        ("pr_data", "github_pulls",
         _build_dsl_pr_data(current_gte, current_lt)),
        ("activity_events", activity_index,
         _build_dsl_activity_events(current_gte, current_lt)),
        ("stale_prs", "github_pulls",
         _build_dsl_stale_prs()),
        ("untriaged_issues", "github_issues",
         _build_dsl_untriaged_issues()),
        ("pr_trend", "github_pulls",
         _build_dsl_pr_trend(current_gte, current_lt, previous_gte, previous_lt)),
        ("issue_trend", "github_issues",
         _build_dsl_issue_trend(current_gte, current_lt, previous_gte, previous_lt)),
    ]

    def _run_section(idx: int, section: str, index: str, body: Dict[str, Any]):
        started = time.time()
        logger.info(
            f"DATA_COLLECTOR: [{idx}/{len(sections)}] Starting section='{section}' index='{index}'"
        )
        result = _safe_invoke(index, body, section)
        elapsed = time.time() - started
        is_empty = not result or (
            "error" in result and "raw_completion" in result
        )
        logger.info(
            f"DATA_COLLECTOR: [{idx}/{len(sections)}] Finished section='{section}' "
            f"in {elapsed:.2f}s — empty={is_empty}, "
            f"top_level_keys={list(result.keys()) if isinstance(result, dict) else 'n/a'}"
        )
        return section, result

    results: Dict[str, Any] = {}
    total_started = time.time()
    with ThreadPoolExecutor(max_workers=len(sections)) as executor:
        futures = {
            executor.submit(_run_section, idx, s, i, b): s
            for idx, (s, i, b) in enumerate(sections, 1)
        }
        for future in as_completed(futures):
            section = futures[future]
            try:
                name, result = future.result()
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
