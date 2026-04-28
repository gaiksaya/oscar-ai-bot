#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""
Newsletter Orchestrator.

Coordinates section-by-section newsletter generation,
invoking agents via AgentClient and assembling results via template.
"""

import logging
from typing import Dict, List, Optional

from agent_client import AgentClient
from sections.external_contributors import ExternalContributorsSection
from sections.health_metrics import HealthMetricsSection
from sections.placeholders import PlaceholderSections
from sections.stale_prs import StalePRsSection
from sections.untriaged_issues import UntriagedIssuesSection
from sections.whats_new import WhatsNewSection
from template import NewsletterTemplate

logger = logging.getLogger(__name__)

ALL_SECTIONS = [
    "whats_new",
    "wins",
    "misses",
    "external_contributors",
    "health_metrics",
    "stale_prs",
    "untriaged_issues",
    "upcoming_events",
]


class NewsletterOrchestrator:
    """Orchestrates newsletter generation section by section."""

    def __init__(self) -> None:
        self.agent_client = AgentClient()
        self.template = NewsletterTemplate()
        self.section_handlers = {
            "whats_new": WhatsNewSection(),
            "wins": PlaceholderSections(),
            "misses": PlaceholderSections(),
            "external_contributors": ExternalContributorsSection(),
            "health_metrics": HealthMetricsSection(),
            "stale_prs": StalePRsSection(),
            "untriaged_issues": UntriagedIssuesSection(),
            "upcoming_events": PlaceholderSections(),
        }

    def generate(self, month: str, year: str, sections: Optional[List[str]] = None) -> str:
        """Generate the newsletter for a given month.

        Args:
            month: Target month name (e.g., "March")
            year: Target year (e.g., "2026")
            sections: Optional list of section names to generate.
                      Defaults to all sections.

        Returns:
            Assembled newsletter as a formatted string.
        """
        target_sections = sections if sections else ALL_SECTIONS
        results: Dict[str, str] = {}

        logger.info(f"Generating newsletter for {month} {year}, sections: {target_sections}")

        for section_name in target_sections:
            handler = self.section_handlers.get(section_name)
            if not handler:
                logger.warning(f"Unknown section: {section_name}, skipping")
                continue

            try:
                logger.info(f"Generating section: {section_name}")
                results[section_name] = handler.generate(
                    self.agent_client, month, year, section_name
                )
                logger.info(f"Section {section_name} generated, length: {len(results[section_name])} chars")
            except Exception as e:
                logger.error(f"Section {section_name} failed: {e}", exc_info=True)
                results[section_name] = f"[Section generation failed: {section_name}. Error: {str(e)}]"

        return self.template.assemble(results, month, year)
