#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""
Release Handlers.

Read-only handlers for the release agent's two action-group functions:
  - get_release_status(version): per-criterion state + R/Y/G verdict + reasoning
  - get_release_window(version): schedule dates + days remaining + cadence phase

Both issue deterministic term-filter DSL queries against the release-state
indices on the metrics cluster (no agentic/LLM query generation).
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import rubric
from aws_utils import opensearch_request
from config import config

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_VERSION_RE_HINT = "three-part version like '3.8.0'"


def _validate_version(params: Dict[str, Any]) -> Optional[str]:
    """Return the version string, or None if missing/blank."""
    version = params.get("version")
    if isinstance(version, str):
        version = version.strip()
    return version or None


def _parse_date(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-ish date/datetime string into a timezone-aware datetime.

    Accepts 'YYYY-MM-DD' and full ISO 8601 (with optional trailing 'Z').
    Returns None if the value is missing or unparseable.
    """
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        try:
            dt = datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            logger.warning(f"Could not parse date value: {value!r}")
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------- get_release_status

def handle_get_release_status(params: Dict[str, Any], request_id: str = "unknown") -> Dict[str, Any]:
    """Fetch per-criterion state for a version and compute the R/Y/G verdict."""
    version = _validate_version(params)
    if not version:
        return {"error": f"A version is required ({_VERSION_RE_HINT})."}

    index = config.release_state_index
    query = {
        "size": 100,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"version": version}},
                    {"term": {"doc_type": "criterion"}},
                ]
            }
        },
    }

    try:
        result = opensearch_request("GET", f"/{index}/_search", query)
    except Exception as e:
        logger.error(f"RELEASE_STATUS_QUERY_FAILED [{request_id}]: {e}")
        return {"error": f"Failed to query release state: {e}", "type": "query_error"}

    hits = result.get("hits", {}).get("hits", [])
    if not hits:
        return {
            "version": version,
            "found": False,
            "message": (
                f"No indexed release state found for {version}. It may not be an "
                f"active release yet, or state has not been indexed."
            ),
        }

    # Deduplicate to the latest doc per criterion_name (by last_checked).
    latest_by_name: Dict[str, Dict[str, Any]] = {}
    for hit in hits:
        src = hit.get("_source", {})
        name = src.get("criterion_name")
        if not name:
            continue
        existing = latest_by_name.get(name)
        if existing is None:
            latest_by_name[name] = src
            continue
        if _is_newer(src.get("last_checked"), existing.get("last_checked")):
            latest_by_name[name] = src

    criteria: List[Dict[str, Any]] = list(latest_by_name.values())
    verdict = rubric.compute_verdict(criteria)

    # Surface release_issue if present on any criterion doc (all share it).
    release_issue = next(
        (c.get("release_issue") for c in criteria if c.get("release_issue")), None
    )

    response = {
        "version": version,
        "found": True,
        "verdict": verdict["verdict"],
        "blocking_failures": verdict["blocking_failures"],
        "blocking_unknowns": verdict["blocking_unknowns"],
        "non_blocking_gaps": verdict["non_blocking_gaps"],
        "not_applicable": verdict["not_applicable"],
        "counts": verdict["counts"],
        "criteria": verdict["criteria"],
    }
    if release_issue:
        response["release_issue"] = release_issue
    return response


def _is_newer(candidate: Optional[str], current: Optional[str]) -> bool:
    """True if candidate last_checked is newer than current (missing sorts oldest)."""
    cand_dt = _parse_date(candidate)
    curr_dt = _parse_date(current)
    if cand_dt is None:
        return False
    if curr_dt is None:
        return True
    return cand_dt > curr_dt


# --------------------------------------------------------------- get_release_window

def handle_get_release_window(params: Dict[str, Any], request_id: str = "unknown") -> Dict[str, Any]:
    """Fetch the schedule for a version and compute days remaining + cadence phase."""
    version = _validate_version(params)
    if not version:
        return {"error": f"A version is required ({_VERSION_RE_HINT})."}

    index = config.release_schedule_index
    query = {
        "size": 1,
        "query": {"bool": {"filter": [{"term": {"version": version}}]}},
        "sort": [{"registered_at": {"order": "desc"}}],
    }

    try:
        result = opensearch_request("GET", f"/{index}/_search", query)
    except Exception as e:
        logger.error(f"RELEASE_WINDOW_QUERY_FAILED [{request_id}]: {e}")
        return {"error": f"Failed to query release schedule: {e}", "type": "query_error"}

    hits = result.get("hits", {}).get("hits", [])
    if not hits:
        return {
            "version": version,
            "found": False,
            "message": (
                f"No release schedule found for {version}. It may not have been "
                f"registered yet."
            ),
        }

    src = hits[0].get("_source", {})
    rc_date = _parse_date(src.get("rc_date"))
    release_date = _parse_date(src.get("release_date"))
    now = _now()

    days_to_rc = (rc_date.date() - now.date()).days if rc_date else None
    days_to_release = (release_date.date() - now.date()).days if release_date else None
    phase = _cadence_phase(days_to_rc, days_to_release)

    return {
        "version": version,
        "found": True,
        "status": src.get("status"),
        "rc_date": src.get("rc_date"),
        "release_date": src.get("release_date"),
        "days_to_rc": days_to_rc,
        "days_to_release": days_to_release,
        "cadence_phase": phase,
        "release_manager": src.get("release_manager"),
        "release_issue": src.get("release_issue"),
    }


def _cadence_phase(days_to_rc: Optional[int], days_to_release: Optional[int]) -> str:
    """Classify the schedule phase from days-to-RC / days-to-release.

    Phases (from the proposal's escalating notification cadence):
      - pre_rc_daily:      14..8 days before RC (daily)
      - pre_rc_frequent:   7..0 days before RC (every 6h)
      - rc_to_release:     after RC, before release (daily)
      - final_push:        final 2 days before release (every 6h)
      - released:          release date passed
      - not_scheduled:     no usable dates
      - out_of_window:     more than 14 days before RC
    """
    if days_to_release is not None and days_to_release < 0:
        return "released"

    if days_to_rc is not None:
        if days_to_rc > 14:
            return "out_of_window"
        if 8 <= days_to_rc <= 14:
            return "pre_rc_daily"
        if 0 <= days_to_rc <= 7:
            return "pre_rc_frequent"
        # days_to_rc < 0 → RC has passed; fall through to release-based phases

    if days_to_release is not None:
        if days_to_release <= 2:
            return "final_push"
        return "rc_to_release"

    return "not_scheduled"
