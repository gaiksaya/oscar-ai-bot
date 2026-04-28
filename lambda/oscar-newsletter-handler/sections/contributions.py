#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""
Contributions section for the newsletter.

Generates organization-level contribution summaries for all contributors
(Amazon/AWS, external organizations, and independent).

Data sources (OpenSearch indices in the metrics cluster):
- github-user-activity-events-{MM-YYYY}: sender, type, action, repository, created_at
- github_pulls: user_login, repository, state, merged, title, additions, deletions,
  html_url, author_association, created_at, merged_at

The metrics agent's agentic search discovers these indices via ListIndexTool
and translates natural language prompts to DSL queries.

Company resolution for contributors requires the GitHub agent.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_client import AgentClient

logger = logging.getLogger(__name__)

# Bot accounts to exclude from contribution metrics
BOT_ACCOUNTS = [
    "opensearch-trigger-bot[bot]",
    "github-actions[bot]",
    "dependabot[bot]",
    "codecov[bot]",
    "opensearch-ci-bot",
    "opensearchstaging",
    "whitesource-bolt-for-github[bot]",
    "mend-bolt-for-github[bot]",
]


class ContributionsSection:
    """Generates the Contributions section of the newsletter."""

    def generate(self, agent_client: 'AgentClient', month: str, year: str, section_name: str = '') -> str:
        """Generate contributions section.

        Queries the metrics agent for contributor activity and PR data,
        and optionally the GitHub agent for company resolution.
        Includes all contributors — Amazon/AWS, external, and independent.
        """
        from config import config

        if not config.has_metrics_agent:
            return self._placeholder(month, year, "metrics agent not configured")

        parts = []

        # Step 1: Contributor activity from github-user-activity-events index
        activity_summary = self._get_activity_summary(agent_client, month, year)
        if activity_summary:
            parts.append(activity_summary)

        # Step 2: PR contributions from github_pulls index
        pr_summary = self._get_pr_summary(agent_client, month, year)
        if pr_summary:
            parts.append(pr_summary)

        # Step 3: Company resolution via GitHub agent (if available)
        company_summary = self._get_company_summary(agent_client, month, year)
        if company_summary:
            parts.append(company_summary)

        # Step 4: Top contributors
        top_contributors = self._get_top_contributors(agent_client, month, year)
        if top_contributors:
            parts.append(top_contributors)

        # Step 5: Top repositories by contributions
        top_repos = self._get_top_repos(agent_client, month, year)
        if top_repos:
            parts.append(top_repos)

        if not parts:
            return self._placeholder(month, year, "no data returned from agents")

        return "\n\n".join(parts)

    def _get_activity_summary(self, agent_client: 'AgentClient', month: str, year: str) -> str:
        """Get contributor activity summary from github-user-activity-events index."""
        try:
            response = agent_client.query_metrics(
                f"From the github-user-activity-events index for {month} {year}, "
                f"show the unique senders grouped by their activity type. "
                f"How many unique senders had PullRequestEvent, PullRequestReviewEvent, "
                f"IssuesEvent, IssueCommentEvent, and PullRequestReviewCommentEvent? "
                f"Exclude bot accounts like github-actions[bot] and dependabot[bot]. "
                f"Also show the total number of unique contributors and total events."
            )
            if response:
                return f"*Contributor Activity Summary:*\n\n{response}"
        except Exception as e:
            logger.error(f"Failed to get activity summary: {e}")
        return ""

    def _get_pr_summary(self, agent_client: 'AgentClient', month: str, year: str) -> str:
        """Get PR contribution summary from github_pulls index."""
        try:
            response = agent_client.query_metrics(
                f"From the github_pulls index, show pull request statistics for {month} {year}. "
                f"How many PRs were created, how many were merged (merged=true), "
                f"how many are still open (state=open)? "
                f"Show the top repositories by PR count. "
                f"Also break down by author_association to show how many PRs came from "
                f"MEMBER vs CONTRIBUTOR vs FIRST_TIMER vs other associations."
            )
            if response:
                return f"*Pull Request Contributions:*\n\n{response}"
        except Exception as e:
            logger.error(f"Failed to get PR summary: {e}")
        return ""

    def _get_company_summary(self, agent_client: 'AgentClient', month: str, year: str) -> str:
        """Get company-level contribution breakdown via GitHub agent."""
        from config import config

        if not config.has_github_agent:
            return ""

        try:
            response = agent_client.query_github(
                f"For contributors to opensearch-project in {month} {year}, "
                f"resolve their company affiliations from their GitHub profiles. "
                f"Group contributors by organization (Amazon/AWS, Uber, Apple, etc.). "
                f"For each organization show: contributor GitHub handles, "
                f"repositories they engaged with, and a summary of their contributions. "
                f"Include independent contributors. Exclude bot accounts."
            )
            if response:
                return f"*Contributions by Organization:*\n\n{response}"
        except Exception as e:
            logger.error(f"Company resolution failed: {e}")
        return ""

    def _get_top_contributors(self, agent_client: 'AgentClient', month: str, year: str) -> str:
        """Get top 3 contributors by activity."""
        try:
            response = agent_client.query_metrics(
                f"From the github-user-activity-events index for {month} {year}, "
                f"who are the top 3 senders by total event count? "
                f"Exclude bot accounts like github-actions[bot] and dependabot[bot]. "
                f"For each sender show: their total events, the types of events "
                f"(PullRequestEvent, PullRequestReviewEvent, etc.), "
                f"and the repositories they contributed to."
            )
            if response:
                return f"*Top 3 Contributors:*\n\n{response}"
        except Exception as e:
            logger.error(f"Failed to get top contributors: {e}")
        return ""

    def _get_top_repos(self, agent_client: 'AgentClient', month: str, year: str) -> str:
        """Get top 3 repositories by contribution count."""
        try:
            response = agent_client.query_metrics(
                f"From the github_pulls index, what are the top 3 repositories "
                f"with the most pull requests created in {month} {year}? "
                f"Show repository name and PR count."
            )
            if response:
                return f"*Top 3 Repositories by Contributions:*\n\n{response}"
        except Exception as e:
            logger.error(f"Failed to get top repos: {e}")
        return ""

    def _placeholder(self, month: str, year: str, reason: str = "") -> str:
        reason_note = f" ({reason})" if reason else ""
        return (
            f"[EDITORIAL: Add contributor metrics for {month} {year}{reason_note}]\n\n"
            "Include:\n"
            "- Organization-level contribution table (Organization, Contributors, Repos, PR Overview)\n"
            "- Top 3 contributors with activity breakdown\n"
            "- Top 3 repositories by contributions"
        )
