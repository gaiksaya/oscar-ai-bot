# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Data aggregation for newsletter data.

All responses come from the Metrics Agent's `direct_query` function, which
returns raw OpenSearch JSON. The aggregation shapes are KNOWN because we
wrote the DSL ourselves (see data_collector._build_dsl_*).

Returns strongly-typed dataclasses (see models.py) so callers get IDE
completion, mypy checking, and self-documenting field names.
"""

import logging
import re
from collections import defaultdict
from typing import Any, Dict, List

import company_resolver
from models import (
    CompanySummary,
    HealthMetrics,
    RepoContribution,
    RepoCount,
    TopContributor,
    TopRepo,
    UserPRStats,
)

logger = logging.getLogger(__name__)


# Strip backport prefixes like "[Backport] [3.x]", "[Backport 3.5]", "Backport "
_BACKPORT_PREFIX_RE = re.compile(
    r"^(?:\[Backport\]\s*\[[\d.x]+\]\s*|\[Backport\s+[\d.x]+\]\s*|Backport\s+)",
    re.IGNORECASE,
)


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


# ---------------------------------------------------------------------------
# Internal bucket extractors — kept as module-level helpers because they're
# used by both the aggregator and (via backdoor import) the processor.
# ---------------------------------------------------------------------------

def _pr_user_buckets(pr_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract the `by_user_login` buckets from pr_data response."""
    aggs = pr_data.get("aggregations") or {}
    return (aggs.get("by_user_login") or {}).get("buckets") or []


def _activity_sender_buckets(activity_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract the `by_sender` buckets from activity_events response."""
    aggs = activity_data.get("aggregations") or {}
    return (aggs.get("by_sender") or {}).get("buckets") or []


def _repo_buckets(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract the `by_repository` buckets from stale_prs / untriaged_issues."""
    aggs = result.get("aggregations") or {}
    return (aggs.get("by_repository") or {}).get("buckets") or []


# ---------------------------------------------------------------------------
# Per-user stats derived from pr_data
# ---------------------------------------------------------------------------

def _build_user_pr_stats(pr_data: Dict[str, Any]) -> Dict[str, UserPRStats]:
    """Return username -> UserPRStats.

    Handles `by_merged` as `true`/`false` boolean buckets, `by_repository`
    bucket keys, and `sample_titles` top_hits within each repo bucket.
    """
    stats: Dict[str, UserPRStats] = {}
    for bucket in _pr_user_buckets(pr_data):
        username = bucket.get("key") or ""
        if not username or company_resolver.is_bot(username):
            continue
        count = bucket.get("doc_count", 0)

        # by_merged — {buckets: [{key: 1 (true), doc_count: N}, {key: 0 (false), doc_count: M}]}
        merged = 0
        unmerged = 0
        for mb in (bucket.get("by_merged") or {}).get("buckets", []):
            k = mb.get("key")
            is_true = (k == 1) or (k is True) or (str(mb.get("key_as_string", "")).lower() == "true")
            if is_true:
                merged += mb.get("doc_count", 0)
            else:
                unmerged += mb.get("doc_count", 0)

        # by_repository — {buckets: [{key, doc_count, sample_titles: {hits: {hits: [{_source: {title}}]}}}, ...]}
        repos: List[tuple] = []
        pr_titles_by_repo: Dict[str, List[str]] = {}
        for rb in (bucket.get("by_repository") or {}).get("buckets", []):
            repo_name = rb.get("key", "")
            if not repo_name:
                continue
            repos.append((repo_name, rb.get("doc_count", 0)))
            hits = ((rb.get("sample_titles") or {}).get("hits") or {}).get("hits") or []
            titles = [
                h.get("_source", {}).get("title", "") for h in hits
                if h.get("_source", {}).get("title")
            ]
            if titles:
                pr_titles_by_repo[repo_name] = titles

        additions = int(((bucket.get("total_additions") or {}).get("value") or 0))
        deletions = int(((bucket.get("total_deletions") or {}).get("value") or 0))

        stats[username] = UserPRStats(
            pr_count=count,
            merged=merged,
            open=unmerged,  # non-merged includes open + closed-not-merged
            additions=additions,
            deletions=deletions,
            repos=repos,
            pr_titles_by_repo=pr_titles_by_repo,
        )
    logger.info(
        f"AGGREGATOR: _build_user_pr_stats — {len(stats)} non-bot users "
        f"(from {len(_pr_user_buckets(pr_data))} buckets)"
    )
    return stats


def _build_user_activity(activity_data: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    """Return username -> {type: count, ...} from activity_events."""
    per_user: Dict[str, Dict[str, int]] = {}
    for bucket in _activity_sender_buckets(activity_data):
        username = bucket.get("key") or ""
        if not username or company_resolver.is_bot(username):
            continue
        breakdown: Dict[str, int] = defaultdict(int)
        for tb in (bucket.get("by_type") or {}).get("buckets", []):
            breakdown[tb.get("key", "unknown")] += tb.get("doc_count", 0)
        if breakdown:
            per_user[username] = dict(breakdown)
    logger.info(
        f"AGGREGATOR: _build_user_activity — {len(per_user)} non-bot users "
        f"(from {len(_activity_sender_buckets(activity_data))} sender buckets)"
    )
    return per_user


# ---------------------------------------------------------------------------
# Public aggregation functions
# ---------------------------------------------------------------------------

def aggregate_contributors(
    pr_data: Dict[str, Any],
    activity_data: Dict[str, Any],
    companies: Dict[str, str],
) -> List[CompanySummary]:
    """Group contributors by company and produce the rich per-company summary.

    Users NOT in `companies` are dropped (that's how the Amazon filter works).
    """
    user_pr = _build_user_pr_stats(pr_data)
    user_activity = _build_user_activity(activity_data)

    # Accumulate per-company stats using plain dicts as scratch space; we
    # convert to CompanySummary dataclasses at the end.
    scratch: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "users": [],
        "total_prs": 0,
        "merged": 0,
        "open": 0,
        "additions": 0,
        "deletions": 0,
        "repo_counts": defaultdict(int),
        "activity_breakdown": defaultdict(int),
        "pr_titles_by_repo": defaultdict(list),
    })

    for username, stats in user_pr.items():
        company = companies.get(username)
        if not company:
            continue
        s = scratch[company]
        s["users"].append(username)
        s["total_prs"] += stats.pr_count
        s["merged"] += stats.merged
        s["open"] += stats.open
        s["additions"] += stats.additions
        s["deletions"] += stats.deletions
        for repo, rcount in stats.repos:
            s["repo_counts"][repo] += rcount
        for repo, titles in stats.pr_titles_by_repo.items():
            s["pr_titles_by_repo"][repo].extend(titles)

    for username, breakdown in user_activity.items():
        company = companies.get(username)
        if not company:
            continue
        s = scratch[company]
        for event_type, count in breakdown.items():
            s["activity_breakdown"][event_type] += count

    result: List[CompanySummary] = []
    for company, s in scratch.items():
        top_repos = sorted(
            s["repo_counts"].items(), key=lambda x: x[1], reverse=True
        )[:5]
        repos_list = [RepoContribution(repository=r, count=c) for r, c in top_repos]

        repo_phrase = ", ".join(f"{r} ({c})" for r, c in top_repos) or "n/a"
        pr_summary_text = (
            f"{s['total_prs']} PRs "
            f"({s['merged']} merged, {s['open']} open) "
            f"across {repo_phrase}. "
            f"Total changes: +{s['additions']}/-{s['deletions']} lines"
        )

        titles_by_repo: Dict[str, List[str]] = {}
        top_repo_names = {r for r, _ in top_repos}
        for repo, titles in s["pr_titles_by_repo"].items():
            if repo not in top_repo_names:
                continue
            deduped = deduplicate_pr_titles(titles)
            if deduped:
                titles_by_repo[repo] = deduped[:10]

        result.append(CompanySummary(
            company=company,
            users=sorted(s["users"]),
            total_prs=s["total_prs"],
            merged=s["merged"],
            open=s["open"],
            additions=s["additions"],
            deletions=s["deletions"],
            repos=repos_list,
            activity_breakdown=dict(s["activity_breakdown"]),
            pr_summary_text=pr_summary_text,
            pr_titles_by_repo=titles_by_repo,
        ))
    result.sort(key=lambda c: c.total_prs, reverse=True)
    logger.info(
        f"AGGREGATOR: aggregate_contributors — {len(result)} companies "
        f"from {len(user_pr)} PR users + {len(user_activity)} activity users"
    )
    return result


def compute_top_contributors(
    pr_data: Dict[str, Any],
    companies: Dict[str, str],
    n: int = 3,
) -> List[TopContributor]:
    """Top N contributors by PR count, excluding bots and filtered companies."""
    candidates: List[TopContributor] = []
    for bucket in _pr_user_buckets(pr_data):
        username = bucket.get("key") or ""
        if not username or company_resolver.is_bot(username):
            continue
        company = companies.get(username)
        if not company:
            continue
        candidates.append(TopContributor(
            username=username,
            company=company,
            pr_count=bucket.get("doc_count", 0),
        ))
    candidates.sort(key=lambda c: c.pr_count, reverse=True)
    top = candidates[:n]
    logger.info(
        f"AGGREGATOR: compute_top_contributors — picked {len(top)} from "
        f"{len(candidates)} eligible candidates"
    )
    return top


def compute_top_repos(pr_data: Dict[str, Any], n: int = 3) -> List[TopRepo]:
    """Top N repositories across all contributors by PR count."""
    repo_counts: Dict[str, int] = defaultdict(int)
    for bucket in _pr_user_buckets(pr_data):
        for rb in (bucket.get("by_repository") or {}).get("buckets", []):
            k = rb.get("key", "")
            if k:
                repo_counts[k] += rb.get("doc_count", 0)
    sorted_repos = sorted(repo_counts.items(), key=lambda x: x[1], reverse=True)[:n]
    logger.info(
        f"AGGREGATOR: compute_top_repos — {len(repo_counts)} unique repos, "
        f"returning top {len(sorted_repos)}"
    )
    return [TopRepo(repository=r, pr_count=c) for r, c in sorted_repos]


def compute_trend(trend_result: Dict[str, Any]) -> tuple:
    """Return (current_count, previous_count, change_percent) from a two-bucket
    trend aggregation. Works for both PR and issue trend queries because we
    wrote them with identical shapes (`current_month.doc_count`, `previous_month.doc_count`).
    """
    aggs = trend_result.get("aggregations") or {}
    current = (aggs.get("current_month") or {}).get("doc_count", 0)
    previous = (aggs.get("previous_month") or {}).get("doc_count", 0)
    change = round(((current - previous) / previous * 100), 1) if previous else 0.0
    logger.info(
        f"AGGREGATOR: compute_trend — current={current}, previous={previous}, change={change}%"
    )
    return current, previous, change


def extract_repo_counts(result: Dict[str, Any]) -> List[RepoCount]:
    """Extract repo buckets from stale_prs / untriaged_issues response."""
    buckets = _repo_buckets(result)
    out = [
        RepoCount(repository=b.get("key", ""), count=b.get("doc_count", 0))
        for b in buckets if b.get("key")
    ]
    logger.info(f"AGGREGATOR: extract_repo_counts — {len(out)} repos")
    return out
