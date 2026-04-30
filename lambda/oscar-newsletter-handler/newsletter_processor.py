# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Main orchestrator for newsletter generation."""

import logging
import os
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

import company_resolver
import data_aggregator
import data_collector
import issue_parser
import slack_uploader

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
    buckets = data_aggregator._find_buckets(pr_data)
    return [b.get("key", "") for b in buckets if b.get("key")]


def _render_markdown(data: Dict[str, Any]) -> str:
    """Render the structured data to a newsletter markdown string via Jinja2."""
    template = _env.get_template("newsletter_template.j2")
    return template.render(**data)


def generate(
    month: str,
    year: str,
    target_channel: Optional[str] = None,
    initial_comment: Optional[str] = None,
    thread_ts: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate newsletter data and markdown for a given month/year.

    If target_channel is provided, uploads the rendered markdown as a `.md`
    file to that Slack channel. If thread_ts is also provided, the upload is
    posted as a threaded reply. If target_channel is None, returns the
    rendered markdown in the response payload (useful for testing).
    """
    logger.info(
        f"NEWSLETTER: Generating for {month} {year} "
        f"(channel={target_channel or 'none'}, thread_ts={thread_ts or 'none'})"
    )

    # Step 1: Collect raw data from Metrics Agent
    logger.info("NEWSLETTER: Step 1 — collecting raw data from Metrics Agent")
    raw = data_collector.collect(month, year)
    for section, result in raw.items():
        if isinstance(result, dict):
            logger.info(
                f"NEWSLETTER: raw[{section}] keys={list(result.keys())[:10]}"
            )
        else:
            logger.info(f"NEWSLETTER: raw[{section}] type={type(result).__name__}")

    # Step 2: Parse GitHub request issues
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

    # Step 3: Resolve companies
    logger.info("NEWSLETTER: Step 3 — resolving companies")
    usernames = set(m["github_handle"] for m in new_maintainers)
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

    # Annotate maintainer affiliations — use unfiltered lookup so Amazon
    # maintainers are still credited in the "New maintainers" table.
    if new_maintainers:
        all_companies = company_resolver.resolve_all_companies(
            [m["github_handle"] for m in new_maintainers]
        )
        for m in new_maintainers:
            m["affiliation"] = all_companies.get(m["github_handle"], "")

    # Step 4: Aggregate contributor metrics
    logger.info("NEWSLETTER: Step 4 — aggregating contributor metrics")
    by_company = data_aggregator.aggregate_contributors(
        raw.get("pr_data", {}),
        raw.get("activity_events", {}),
        companies,
    )
    top_contributors = data_aggregator.compute_top_contributors(
        raw.get("pr_data", {}), companies
    )
    top_repos = data_aggregator.compute_top_repos(raw.get("pr_data", {}))
    logger.info(
        f"NEWSLETTER: by_company={len(by_company)}, "
        f"top_contributors={len(top_contributors)}, top_repos={len(top_repos)}"
    )

    # Step 5: Health metrics
    logger.info("NEWSLETTER: Step 5 — computing health metrics")
    pr_trend = data_aggregator.compute_pr_trend(raw.get("pr_trend", {}))
    stale_prs = data_aggregator.extract_repo_counts(raw.get("stale_prs", {}))
    untriaged_issues = data_aggregator.extract_repo_counts(raw.get("untriaged_issues", {}))
    logger.info(
        f"NEWSLETTER: pr_trend={pr_trend}, stale_prs={len(stale_prs)}, "
        f"untriaged={len(untriaged_issues)}"
    )

    # Step 6: Assemble structured output
    data = {
        "month": f"{month} {year}",
        "new_maintainers": new_maintainers,
        "new_repositories": new_repositories,
        "contribution_metrics": {
            "by_company": by_company,
            "top_3_contributors": top_contributors,
            "top_3_repos": top_repos,
            "unknown_company_users": unknown_users,
        },
        "health_metrics": {
            "stale_prs": stale_prs,
            "untriaged_issues": untriaged_issues,
            **pr_trend,
        },
    }

    # Step 7: Render markdown
    logger.info("NEWSLETTER: Step 7 — rendering markdown template")
    markdown = _render_markdown(data)
    data["markdown"] = markdown
    logger.info(f"NEWSLETTER: Rendered markdown length={len(markdown)} chars")

    logger.info(
        f"NEWSLETTER: Complete — {len(new_maintainers)} maintainers, "
        f"{len(new_repositories)} repos, {len(by_company)} companies, "
        f"{len(unknown_users)} unknown users"
    )

    # Step 8: Upload to Slack if channel provided; strip markdown from response
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

        summary = {
            "month": data["month"],
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

    return data
