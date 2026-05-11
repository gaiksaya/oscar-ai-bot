# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Typed data models for newsletter generation.

The Jinja2 template uses attribute access (`{{ c.company }}`, `{{ c.repos }}`,
`{{ r.repository }}`) which works transparently on dataclasses, so the
template did not change when we switched from dicts.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Issue parsing outputs
# ---------------------------------------------------------------------------

@dataclass
class NewMaintainerEntry:
    """One row in the 'New maintainers added this month' table."""
    github_handle: str
    repository: str
    issue_url: str
    closed_at: str
    affiliation: str = ""


@dataclass
class NewRepositoryEntry:
    """One row in the 'New repositories under OpenSearch-Project' list."""
    name: str
    issue_url: str
    closed_at: str


# ---------------------------------------------------------------------------
# Intermediate per-user stats
# ---------------------------------------------------------------------------

@dataclass
class UserPRStats:
    """Per-user PR aggregation extracted from pr_data buckets.

    Used internally by the aggregator; not exposed in the final newsletter.
    """
    pr_count: int
    merged: int
    open: int
    additions: int
    deletions: int
    repos: List[tuple]  # [(repo_name, doc_count), ...]
    pr_titles_by_repo: Dict[str, List[str]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Contribution metrics
# ---------------------------------------------------------------------------

@dataclass
class RepoContribution:
    """(repo, count) pair for the 'Repositories Engaged With' column."""
    repository: str
    count: int


@dataclass
class CompanySummary:
    """One row in the Contribution Metrics table."""
    company: str
    users: List[str]
    total_prs: int
    merged: int
    open: int
    additions: int
    deletions: int
    repos: List[RepoContribution]
    activity_breakdown: Dict[str, int]
    pr_summary_text: str
    pr_titles_by_repo: Dict[str, List[str]] = field(default_factory=dict)
    narrative: str = ""


@dataclass
class TopContributor:
    """One row in the 'Top 3 contributors' table."""
    username: str
    company: str
    pr_count: int


@dataclass
class TopRepo:
    """One row in the 'Top 3 Repositories with contributions' table."""
    repository: str
    pr_count: int


@dataclass
class ContributionMetrics:
    """Full contribution metrics block rendered in the newsletter."""
    by_company: List[CompanySummary]
    top_3_contributors: List[TopContributor]
    top_3_repos: List[TopRepo]
    unknown_company_users: List[str]


# ---------------------------------------------------------------------------
# Health metrics
# ---------------------------------------------------------------------------

@dataclass
class RepoCount:
    """Generic (repo, count) pair used for stale_prs and untriaged_issues."""
    repository: str
    count: int


@dataclass
class HealthMetrics:
    """PR/issue trend + stale/untriaged tables."""
    pr_count_current: int
    pr_count_previous: int
    pr_change_percent: float

    issue_count_current: int
    issue_count_previous: int
    issue_change_percent: float

    stale_prs: List[RepoCount]
    untriaged_issues: List[RepoCount]


# ---------------------------------------------------------------------------
# Top-level newsletter payload
# ---------------------------------------------------------------------------

@dataclass
class NewsletterData:
    """Root struct that gets passed to the Jinja2 template."""
    month: str
    new_maintainers: List[NewMaintainerEntry]
    new_repositories: List[NewRepositoryEntry]
    contribution_metrics: ContributionMetrics
    health_metrics: HealthMetrics
    markdown: str = ""
