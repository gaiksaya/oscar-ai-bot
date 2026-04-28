#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""
What's New section for the newsletter.

Covers new releases, new repositories, and new maintainers.
Depends on the GitHub agent (issue #118).
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_client import AgentClient

logger = logging.getLogger(__name__)


class WhatsNewSection:
    """Generates the What's New section of the newsletter."""

    def generate(self, agent_client: 'AgentClient', month: str, year: str, section_name: str = '') -> str:
        """Generate What's New section.

        Queries the GitHub agent for releases, new repos, and new maintainers.
        Returns placeholder text if the GitHub agent is not yet available.
        """
        from config import config

        if not config.has_github_agent:
            return self._placeholder(month, year)

        parts = []

        # New releases
        try:
            releases_response = agent_client.query_github(
                f"List all releases published under the opensearch-project GitHub organization "
                f"in {month} {year}. Include repository name, version, and release date."
            )
            if releases_response:
                parts.append(releases_response)
        except Exception as e:
            logger.error(f"Failed to fetch releases: {e}")
            parts.append(f"[Failed to fetch releases: {e}]")

        # New repositories
        try:
            repos_response = agent_client.query_github(
                f"List any new repositories created under the opensearch-project GitHub organization "
                f"in {month} {year}. Include the repository name and a brief description."
            )
            if repos_response:
                parts.append(repos_response)
        except Exception as e:
            logger.error(f"Failed to fetch new repos: {e}")
            parts.append(f"[Failed to fetch new repositories: {e}]")

        # New maintainers
        try:
            maintainers_response = agent_client.query_github(
                f"List new maintainers added to opensearch-project repositories in {month} {year}. "
                f"Check the .github repository for maintainer additions. "
                f"Include GitHub handle, repository, and affiliation if available."
            )
            if maintainers_response:
                parts.append(maintainers_response)
        except Exception as e:
            logger.error(f"Failed to fetch new maintainers: {e}")
            parts.append(f"[Failed to fetch new maintainers: {e}]")

        if not parts:
            return self._placeholder(month, year)

        return "\n\n".join(parts)

    def _placeholder(self, month: str, year: str) -> str:
        return (
            f"[EDITORIAL: Add What's New items for {month} {year}]\n"
            "- New releases (e.g., OpenSearch x.y.z released)\n"
            "- New repositories under opensearch-project\n"
            "- New maintainers added this month\n"
            "- Notable announcements\n\n"
            "_Note: This section will be auto-populated once the GitHub agent is available._"
        )
