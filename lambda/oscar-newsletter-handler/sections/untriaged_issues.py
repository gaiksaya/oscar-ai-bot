#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""
Untriaged Issues section for the newsletter.

Identifies repositories with high counts of untriaged issues.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_client import AgentClient

logger = logging.getLogger(__name__)


class UntriagedIssuesSection:
    """Generates the Untriaged Issues section of the newsletter."""

    def generate(self, agent_client: 'AgentClient', month: str, year: str, section_name: str = '') -> str:
        """Generate untriaged issues section.

        Queries the metrics agent for repositories with the highest
        count of untriaged issues older than 2 weeks.
        """
        prompt = (
            "List the opensearch-project repositories with the highest count of "
            "untriaged issues that are older than 2 weeks. "
            "Show the top repositories with their untriaged issue counts. "
            "Format as a table with columns: Repository, Issue Count."
        )

        response = agent_client.query_metrics(prompt)
        if not response:
            return "[Untriaged issues data unavailable - metrics agent not configured]"

        action_note = (
            "\n\n_Action Item: Requesting the maintainers to please take a look "
            "and triage issues._"
        )

        return response + action_note
