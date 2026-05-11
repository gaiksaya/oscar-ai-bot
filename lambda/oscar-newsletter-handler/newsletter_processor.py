# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Main orchestrator for newsletter generation.

Pipeline (each step produces typed dataclasses defined in models.py):
  1. Collect raw OpenSearch responses via data_collector
  2. Parse GitHub request issues                 → NewMaintainerEntry, NewRepositoryEntry
  3. Resolve company affiliations                → Dict[str, str]
  4. Aggregate contributor metrics                → List[CompanySummary]
  4b. Generate per-company narratives via Bedrock → CompanySummary.narrative
  5. Compute health metrics                       → HealthMetrics
  6. Assemble NewsletterData
  7. Render Jinja2 template
  8. (optional) Upload to Slack
"""

import concurrent.futures as _cf
import logging
import os
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

import company_resolver
import data_aggregator
import data_collector
import issue_parser
import narrative_generator
import slack_uploader
from models import (
    CompanySummary,
    ContributionMetrics,
    HealthMetrics,
    NewsletterData,
)

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = os.path.dirname(os.path.abspath(__file__))
_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(enabled_extensions=()),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _unique_usernames_from_pr_data(pr_data: Dict[str, Any]) -> List[str]:
    """Extract all unique PR author usernames from PR aggregation buckets."""
    buckets = data_aggregator._pr_user_buckets(pr_data)
    return [b.get("key", "") for b in buckets if b.get("key")]


def _attach_narratives(
    by_company: List[CompanySummary],
    month: str,
    year: str,
) -> List[CompanySummary]:
    """Run per-company narrative generation in parallel, mutate in place."""
    if not by_company:
        return by_company

    def _gen(c: CompanySummary) -> CompanySummary:
        c.narrative = narrative_generator.generate_company_narrative(
            company=c.company,
            users=c.users,
            titles_by_repo=c.pr_titles_by_repo,
            month=month,
            year=year,
        )
        return c

    with _cf.ThreadPoolExecutor(max_workers=min(10, len(by_company))) as pool:
        return list(pool.map(_gen, by_company))


def _render_markdown(data: NewsletterData) -> str:
    """Render NewsletterData to markdown via Jinja2.

    Jinja2 supports attribute access on dataclasses out of the box, so the
    template (newsletter_template.j2) uses `{{ c.company }}`, `{{ r.repository }}`
    etc. without any conversion here.
    """
    template = _env.get_template("newsletter_template.j2")
    return template.render(
        month=data.month,
        new_maintainers=data.new_maintainers,
        new_repositories=data.new_repositories,
        contribution_metrics=data.contribution_metrics,
        health_metrics=data.health_metrics,
    )


def generate(
    month: str,
    year: str,
    target_channel: Optional[str] = None,
    initial_comment: Optional[str] = None,
    thread_ts: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate newsletter data and markdown for a given month/year.

    If target_channel is provided, uploads the rendered markdown as a `.md`
    file to that Slack channel and returns a compact summary dict. Otherwise
    returns the full payload including the rendered markdown.
    """
    logger.info(
        f"NEWSLETTER: Generating for {month} {year} "
        f"(channel={target_channel or 'none'}, thread_ts={thread_ts or 'none'})"
    )

    # Step 1: Collect
    logger.info("NEWSLETTER: Step 1 — collecting raw data from Metrics Agent")
    raw = data_collector.collect(month, year)
    for section, result in raw.items():
        if isinstance(result, dict):
            logger.info(f"NEWSLETTER: raw[{section}] keys={list(result.keys())[:10]}")
        else:
            logger.info(f"NEWSLETTER: raw[{section}] type={type(result).__name__}")

    # Step 2: Parse issues
    logger.info("NEWSLETTER: Step 2 — parsing GitHub request issues")
    maintainer_hits = (
        raw.get("new_maintainers", {}).get("hits", {}).get("hits", [])
        or raw.get("new_maintainers", {}).get("results", [])
    )
    logger.info(f"NEWSLETTER: maintainer_hits count={len(maintainer_hits)}")
    new_maintainers = issue_parser.parse_maintainer_issues(maintainer_hits)

    repo_hits = (
        raw.get("new_repositories", {}).get("hits", {}).get("hits", [])
        or raw.get("new_repositories", {}).get("results", [])
    )
    logger.info(f"NEWSLETTER: repo_hits count={len(repo_hits)}")
    new_repositories = issue_parser.parse_repository_issues(repo_hits)

    # Step 3: Company resolution
    logger.info("NEWSLETTER: Step 3 — resolving companies")
    usernames = set(m.github_handle for m in new_maintainers)
    pr_usernames = _unique_usernames_from_pr_data(raw.get("pr_data", {}))
    logger.info(
        f"NEWSLETTER: maintainer_usernames={len(usernames)}, "
        f"pr_usernames={len(pr_usernames)}"
    )
    usernames.update(pr_usernames)
    companies, unknown_users = company_resolver.resolve_companies(sorted(usernames))
    logger.info(
        f"NEWSLETTER: companies_resolved={len(companies)}, "
        f"unknown_users={len(unknown_users)}"
    )

    # Annotate maintainer affiliations using the unfiltered lookup so Amazon
    # maintainers still get credited in the "New maintainers" table.
    if new_maintainers:
        all_companies = company_resolver.resolve_all_companies(
            [m.github_handle for m in new_maintainers]
        )
        for m in new_maintainers:
            m.affiliation = all_companies.get(m.github_handle, "")

    # Step 4: Contribution aggregation
    logger.info("NEWSLETTER: Step 4 — aggregating contributor metrics")
    by_company = data_aggregator.aggregate_contributors(
        raw.get("pr_data", {}),
        raw.get("activity_events", {}),
        companies,
    )
    by_company_full_count = len(by_company)
    by_company = by_company[:10]

    # Step 4b: Narratives (LLM, parallel)
    logger.info("NEWSLETTER: Step 4b — generating per-company narratives")
    by_company = _attach_narratives(by_company, month=month, year=year)

    top_contributors = data_aggregator.compute_top_contributors(
        raw.get("pr_data", {}), companies
    )
    top_repos = data_aggregator.compute_top_repos(raw.get("pr_data", {}))
    logger.info(
        f"NEWSLETTER: by_company={len(by_company)} (of {by_company_full_count} total), "
        f"top_contributors={len(top_contributors)}, top_repos={len(top_repos)}"
    )

    # Step 5: Health metrics
    logger.info("NEWSLETTER: Step 5 — computing health metrics")
    pr_current, pr_previous, pr_change = data_aggregator.compute_trend(
        raw.get("pr_trend", {})
    )
    issue_current, issue_previous, issue_change = data_aggregator.compute_trend(
        raw.get("issue_trend", {})
    )
    stale_prs = data_aggregator.extract_repo_counts(raw.get("stale_prs", {}))
    untriaged_issues = data_aggregator.extract_repo_counts(
        raw.get("untriaged_issues", {})
    )
    health_metrics = HealthMetrics(
        pr_count_current=pr_current,
        pr_count_previous=pr_previous,
        pr_change_percent=pr_change,
        issue_count_current=issue_current,
        issue_count_previous=issue_previous,
        issue_change_percent=issue_change,
        stale_prs=stale_prs,
        untriaged_issues=untriaged_issues,
    )
    logger.info(
        f"NEWSLETTER: pr_trend=({pr_previous}→{pr_current}, {pr_change}%), "
        f"issue_trend=({issue_previous}→{issue_current}, {issue_change}%), "
        f"stale_prs={len(stale_prs)}, untriaged={len(untriaged_issues)}"
    )

    # Step 6: Assemble
    data = NewsletterData(
        month=f"{month} {year}",
        new_maintainers=new_maintainers,
        new_repositories=new_repositories,
        contribution_metrics=ContributionMetrics(
            by_company=by_company,
            top_3_contributors=top_contributors,
            top_3_repos=top_repos,
            unknown_company_users=unknown_users,
        ),
        health_metrics=health_metrics,
    )

    # Step 7: Render
    logger.info("NEWSLETTER: Step 7 — rendering markdown template")
    markdown = _render_markdown(data)
    data.markdown = markdown
    logger.info(f"NEWSLETTER: Rendered markdown length={len(markdown)} chars")

    logger.info(
        f"NEWSLETTER: Complete — {len(new_maintainers)} maintainers, "
        f"{len(new_repositories)} repos, {len(by_company)} companies, "
        f"{len(unknown_users)} unknown users"
    )

    # Step 8: Upload to Slack if channel provided
    if target_channel:
        mm = data_collector._month_to_number(month)
        filename = f"newsletter-{year}-{mm}.md"
        logger.info(
            f"NEWSLETTER: Step 8 — uploading to Slack channel={target_channel}, "
            f"thread_ts={thread_ts}, filename={filename}"
        )
        upload_result = slack_uploader.upload_markdown(
            channel=target_channel,
            file_content=markdown,
            filename=filename,
            initial_comment=initial_comment or f"OpenSearch monthly newsletter — {month} {year}",
            title=f"OpenSearch Newsletter — {month} {year}",
            thread_ts=thread_ts,
        )
        logger.info(f"NEWSLETTER: Slack upload_result={upload_result}")

        summary: Dict[str, Any] = {
            "month": data.month,
            "target_channel": target_channel,
            "thread_ts": thread_ts,
            "filename": filename,
            "upload_success": upload_result.get("success", False),
            "counts": {
                "new_maintainers": len(new_maintainers),
                "new_repositories": len(new_repositories),
                "companies": len(by_company),
                "unknown_users": len(unknown_users),
            },
        }
        if not upload_result.get("success"):
            summary["error"] = upload_result.get("error")
        else:
            summary["file_id"] = upload_result.get("file_id")
        return summary

    # Non-upload path (testing): return full payload as a dict.
    from dataclasses import asdict
    return asdict(data)
