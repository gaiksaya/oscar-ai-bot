#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""
Stale PRs section for the newsletter.

Identifies repositories with the most stale pull requests.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_client import AgentClient

logger = logging.getLogger(__name__)


class StalePRsSection:
    """Generates the Stale PRs section of the newsletter."""

    def generate(self, agent_client: 'AgentClient', month: str, year: str, section_name: str = '') -> str:
        """Generate stale PRs section.

        Queries the metrics agent for repositories with the most
        pull requests that haven't been updated in the past 2 months.
        """
        prompt = (
            "List the top 5 opensearch-project repositories with the highest count of "
            "stale pull requests that have not been updated in the past 2 months. "
            "For each repository, show the repository name and the count of stale PRs. "
            "Format as a table with columns: Repository, Count."
        )

        response = agent_client.query_metrics(prompt)
        if not response:
            return "[Stale PR data unavailable - metrics agent not configured]"

        action_note = (
            "\n\n_Action Item: Requesting the maintainers to please take appropriate "
            "action (review, ask for an update or close)._"
        )

        return response + action_note
