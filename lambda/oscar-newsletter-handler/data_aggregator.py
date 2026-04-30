# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Data aggregation — merge PRs + activity events, group by company, compute metrics."""

import logging
import re
from collections import defaultdict
from typing import Any, Dict, List

import company_resolver

logger = logging.getLogger(__name__)


# Strip backport prefixes like "[Backport] [3.x]", "[Backport 3.5]", "Backport "
_BACKPORT_PREFIX_RE = re.compile(
    r"^(?:\[Backport\]\s*\[[\d.x]+\]\s*|\[Backport\s+[\d.x]+\]\s*|Backport\s+)",
    re.IGNORECASE,
)


def _extract_buckets(result: Dict[str, Any], agg_name: str) -> List[Dict[str, Any]]:
    """Extract aggregation buckets from an agentic_query response."""
    if not result:
        return []
    aggs = result.get("aggregations", {})
    agg = aggs.get(agg_name, {})
    return agg.get("buckets", [])


def _find_buckets(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Find the first aggregation buckets in the result."""
    if not result:
        logger.info("AGGREGATOR: _find_buckets — result is empty")
        return []
    if not isinstance(result, dict):
        logger.warning(f"AGGREGATOR: _find_buckets — result is not a dict: {type(result).__name__}")
        return []
    aggs = result.get("aggregations", {})
    if not aggs:
        logger.info(
            f"AGGREGATOR: _find_buckets — no 'aggregations' key; top-level keys={list(result.keys())}"
        )
        return []
    for key, value in aggs.items():
        if isinstance(value, dict) and "buckets" in value:
            buckets = value["buckets"]
            logger.info(
                f"AGGREGATOR: _find_buckets — picked agg='{key}' with {len(buckets)} buckets"
            )
            return buckets
    logger.info(
        f"AGGREGATOR: _find_buckets — no nested buckets found; agg_keys={list(aggs.keys())}"
    )
    return []


def deduplicate_pr_titles(titles: List[str]) -> List[str]:
    """Strip backport prefixes and dedupe by normalized core title."""
    seen = set()
    unique = []
    for title in titles:
        if not title:
            continue
        core = _BACKPORT_PREFIX_RE.sub("", title).strip()
        key = core.lower()[:60]
        if key not in seen:
            seen.add(key)
            unique.append(core)
    return unique


def aggregate_contributors(
    pr_data: Dict[str, Any],
    activity_data: Dict[str, Any],
    companies: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Group contributors by company with merged PR + activity data.

    Users NOT in the `companies` dict are dropped entirely (this is how the
    Amazon filter works — the resolver returns a companies dict without
    filtered users, and we only keep users that are in it).
    """
    logger.info("AGGREGATOR: aggregate_contributors — extracting PR buckets")
    pr_buckets = _find_buckets(pr_data)
    logger.info("AGGREGATOR: aggregate_contributors — extracting activity buckets")
    activity_buckets = _find_buckets(activity_data)
    logger.info(
        f"AGGREGATOR: aggregate_contributors — pr_buckets={len(pr_buckets)}, "
        f"activity_buckets={len(activity_buckets)}, known_companies={len(companies)}"
    )

    user_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "pr_count": 0,
        "additions": 0,
        "deletions": 0,
        "repos": set(),
        "pr_titles": [],
        "activity_breakdown": defaultdict(int),
    })

    for bucket in pr_buckets:
        username = bucket.get("key", "")
        if not username or company_resolver.is_bot(username):
            continue
        count = bucket.get("doc_count", 0)
        user_stats[username]["pr_count"] += count

        # Look for nested per-user aggregations
        for key, value in bucket.items():
            if not isinstance(value, dict):
                continue
            if "value" in value and isinstance(value["value"], (int, float)):
                if key in ("additions", "total_additions"):
                    user_stats[username]["additions"] += int(value["value"])
                elif key in ("deletions", "total_deletions"):
                    user_stats[username]["deletions"] += int(value["value"])

    # Merge activity events
    for bucket in activity_buckets:
        username = bucket.get("key", "")
        if not username or company_resolver.is_bot(username):
            continue
        if username not in user_stats:
            continue
        for key, value in bucket.items():
            if isinstance(value, dict) and "buckets" in value:
                for type_bucket in value["buckets"]:
                    event_type = type_bucket.get("key", "unknown")
                    count = type_bucket.get("doc_count", 0)
                    user_stats[username]["activity_breakdown"][event_type] += count

    # Group by company — only include users whose company we know
    by_company: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "users": [],
        "total_prs": 0,
        "additions": 0,
        "deletions": 0,
        "repos": set(),
        "activity_breakdown": defaultdict(int),
        "pr_titles": [],
    })

    for username, stats in user_stats.items():
        company = companies.get(username)
        if not company:
            continue  # Unknown or filtered (e.g. Amazon) — skip
        bucket = by_company[company]
        bucket["users"].append(username)
        bucket["total_prs"] += stats["pr_count"]
        bucket["additions"] += stats["additions"]
        bucket["deletions"] += stats["deletions"]
        bucket["repos"].update(stats["repos"])
        for event_type, count in stats["activity_breakdown"].items():
            bucket["activity_breakdown"][event_type] += count

    result = []
    for company, bucket in by_company.items():
        result.append({
            "company": company,
            "users": sorted(bucket["users"]),
            "total_prs": bucket["total_prs"],
            "additions": bucket["additions"],
            "deletions": bucket["deletions"],
            "repos": sorted(bucket["repos"]),
            "activity_breakdown": dict(bucket["activity_breakdown"]),
            "pr_titles": deduplicate_pr_titles(bucket["pr_titles"]),
        })
    result.sort(key=lambda x: x["total_prs"], reverse=True)
    logger.info(
        f"AGGREGATOR: aggregated into {len(result)} companies from "
        f"{len(user_stats)} users (after filters)"
    )
    return result


def compute_top_contributors(
    pr_data: Dict[str, Any],
    companies: Dict[str, str],
    n: int = 3,
) -> List[Dict[str, Any]]:
    """Return top N contributors by PR count from pr_data buckets.

    Excludes bots and users whose company was filtered out. Orders by
    per-user doc_count (PR count) descending.
    """
    buckets = _find_buckets(pr_data)
    candidates = []
    for bucket in buckets:
        username = bucket.get("key", "")
        if not username or company_resolver.is_bot(username):
            continue
        company = companies.get(username)
        if not company:
            continue  # skip users not in companies dict (filtered or unknown)
        candidates.append({
            "username": username,
            "company": company,
            "pr_count": bucket.get("doc_count", 0),
        })
    candidates.sort(key=lambda x: x["pr_count"], reverse=True)
    top = candidates[:n]
    logger.info(
        f"AGGREGATOR: compute_top_contributors — picked {len(top)} from "
        f"{len(candidates)} eligible candidates"
    )
    return top


def compute_top_repos(pr_data: Dict[str, Any], n: int = 3) -> List[Dict[str, Any]]:
    """Return top N repositories by PR count.

    Looks inside pr_data for a nested repo aggregation. If the LLM didn't
    include one, returns an empty list (we log a warning).
    """
    buckets = _find_buckets(pr_data)
    repo_counts: Dict[str, int] = defaultdict(int)

    for bucket in buckets:
        for key, value in bucket.items():
            if isinstance(value, dict) and "buckets" in value and "repo" in key.lower():
                for repo_bucket in value["buckets"]:
                    repo_counts[repo_bucket.get("key", "")] += repo_bucket.get("doc_count", 0)

    if not repo_counts:
        logger.info("AGGREGATOR: compute_top_repos — no nested repo aggregation found")
        return []

    sorted_repos = sorted(repo_counts.items(), key=lambda x: x[1], reverse=True)[:n]
    return [{"repository": r, "pr_count": c} for r, c in sorted_repos]


def compute_pr_trend(trend_result: Dict[str, Any]) -> Dict[str, Any]:
    """Extract current vs previous month PR counts and compute change.

    Handles multiple response shapes:
      1) Top-level aggs with doc_count each: {"aggs":{"current":{"doc_count":X}, "previous":{"doc_count":Y}}}
      2) Top-level aggs with value each:     {"aggs":{"current":{"value":X}, "previous":{"value":Y}}}
      3) Single date_histogram bucket agg:   {"aggs":{"prs_by_month":{"buckets":[{"key_as_string":"2026-03","doc_count":Y},{"key_as_string":"2026-04","doc_count":X}]}}}
    """
    if not trend_result:
        logger.info("AGGREGATOR: compute_pr_trend — trend_result empty")
        return {"pr_count_current": 0, "pr_count_previous": 0, "pr_change_percent": 0}

    aggs = trend_result.get("aggregations") or {}
    logger.info(
        f"AGGREGATOR: compute_pr_trend — aggregation keys={list(aggs.keys())}"
    )

    current, previous = 0, 0

    # Case 3: single bucket-style aggregation
    for key, value in aggs.items():
        if isinstance(value, dict) and "buckets" in value and isinstance(value["buckets"], list):
            buckets = value["buckets"]
            if len(buckets) >= 2:
                # Sort by key (usually date string) so previous < current
                def _bucket_sort_key(b):
                    return b.get("key_as_string") or b.get("key") or ""
                sorted_buckets = sorted(buckets, key=_bucket_sort_key)
                previous = sorted_buckets[-2].get("doc_count", 0)
                current = sorted_buckets[-1].get("doc_count", 0)
                logger.info(
                    f"AGGREGATOR: compute_pr_trend — using bucket agg '{key}': "
                    f"previous={_bucket_sort_key(sorted_buckets[-2])} ({previous}), "
                    f"current={_bucket_sort_key(sorted_buckets[-1])} ({current})"
                )
                break

    # Cases 1 and 2: two separate top-level filters
    if not current and not previous:
        counts = []
        for key, value in aggs.items():
            if isinstance(value, dict):
                if "doc_count" in value:
                    counts.append((key, value["doc_count"]))
                elif "value" in value and isinstance(value["value"], (int, float)):
                    counts.append((key, int(value["value"])))
        if len(counts) >= 2:
            # If keys suggest current/previous order, use them; else take first two
            named = {k.lower(): v for k, v in counts}
            if any("prev" in k for k in named) or any("current" in k for k in named):
                previous = next((v for k, v in named.items() if "prev" in k), 0)
                current = next((v for k, v in named.items() if "current" in k or "now" in k), 0)
                if not current and not previous:
                    previous, current = counts[0][1], counts[1][1]
            else:
                # Unnamed: first = current (matches our prompt order), second = previous
                current, previous = counts[0][1], counts[1][1]
            logger.info(
                f"AGGREGATOR: compute_pr_trend — picked current={current}, previous={previous} "
                f"from named aggs {[c[0] for c in counts]}"
            )

    change = round(((current - previous) / previous * 100), 1) if previous else 0
    logger.info(
        f"AGGREGATOR: compute_pr_trend — current={current} previous={previous} change={change}%"
    )

    return {
        "pr_count_current": current,
        "pr_count_previous": previous,
        "pr_change_percent": change,
    }


def extract_repo_counts(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract [{repository, count}] from an aggregation result."""
    buckets = _find_buckets(result)
    repo_counts = [
        {"repository": b.get("key", ""), "count": b.get("doc_count", 0)}
        for b in buckets if b.get("key")
    ]
    logger.info(f"AGGREGATOR: extract_repo_counts — extracted {len(repo_counts)} repos")
    return repo_counts
