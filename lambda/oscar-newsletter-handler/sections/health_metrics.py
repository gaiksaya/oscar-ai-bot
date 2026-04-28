#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""
Health Metrics section for the newsletter.

Generates PR and issue statistics for the opensearch-project repositories.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_client import AgentClient

logger = logging.getLogger(__name__)


class HealthMetricsSection:
    """Generates the Health & Metrics section of the newsletter."""

    def generate(self, agent_client: 'AgentClient', month: str, year: str, section_name: str = '') -> str:
        """Generate health metrics section.

        Queries the metrics agent for overall repository health statistics.
        Month-over-month comparison percentages (from LFX Insights) are left
        as placeholders since they require data not currently in the metrics cluster.
        """
        prompt = (
            f"Provide overall GitHub issue and pull request statistics for all "
            f"opensearch-project repositories for {month} {year}. "
            f"Include total untriaged issues, total open issues, total closed issues, "
            f"total open PRs, and total merged PRs across the project."
        )

        response = agent_client.query_metrics(prompt)
        if not response:
            return "[Health metrics data unavailable - metrics agent not configured]"

        # Append LFX placeholder for month-over-month comparisons
        lfx_note = (
            "\n\n_Month-over-month comparison metrics from LFX Insights:_\n"
            "[EDITORIAL: Add PR count % change and issue resolution % change from "
            "https://insights.linuxfoundation.org/project/opensearch-foundation/development]"
        )

        return response + lfx_note
