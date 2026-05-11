# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Company resolution via DynamoDB cache with name normalization."""

import logging
import os
from typing import Dict, List, Tuple

from config import COMPANY_ALIASES, COMPANY_SUFFIXES_TO_STRIP, COMPANY_TABLE_NAME

logger = logging.getLogger(__name__)


def normalize_company(name: str) -> str:
    """Normalize company name: handle known patterns, strip suffixes, apply aliases."""
    if not name:
        return ""
    stripped = name.strip()
    lower = stripped.lower()

    # Catch all Amazon/AWS variants, including:
    #   "aws @opensearch-project", "Amazon Web Services", "aws, @opensearch-project",
    #   "opensearch-project at @aws", "AWS-India", etc.
    # Use substring match on both "amazon" and "aws" — known false positives
    # like "awsome" are rare enough to accept.
    if "amazon" in lower or "aws" in lower:
        return "Amazon"

    # Users with just "opensearch-project" or "@opensearch-project" as their
    # "company" are typically Amazon employees who listed the GitHub org
    # instead of an employer. Treat as Amazon.
    cleaned = lower.lstrip("@").strip()
    if cleaned in {"opensearch-project", "opensearch project"} or "opensearch-project" in cleaned:
        return "Amazon"

    for suffix in COMPANY_SUFFIXES_TO_STRIP:
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)].strip()
            break

    return COMPANY_ALIASES.get(stripped.lower(), stripped)


def is_bot(username: str) -> bool:
    """Check if a username is a bot account."""
    return username.endswith("[bot]") or username.endswith("-bot")


def resolve_companies(usernames: List[str]) -> Tuple[Dict[str, str], List[str]]:
    """Resolve company affiliations for a list of usernames.

    Returns:
        Tuple of (companies dict {username: company}, unknown_users list)

    When FILTER_COMPANIES env var contains a comma-separated list of company
    names (default: 'Amazon'), users affiliated with those companies are
    EXCLUDED from the returned dict so they don't appear in contributor
    breakdowns.
    """
    if not COMPANY_TABLE_NAME:
        logger.warning("COMPANY_TABLE_NAME not set — skipping company resolution")
        return {}, [u for u in usernames if not is_bot(u)]
    companies, unknown = _resolve_from_dynamodb(usernames)

    filter_str = os.environ.get("FILTER_COMPANIES", "Amazon")
    filters = {c.strip().lower() for c in filter_str.split(",") if c.strip()}
    if filters:
        before = len(companies)
        companies = {u: c for u, c in companies.items() if c.lower() not in filters}
        dropped = before - len(companies)
        if dropped:
            logger.info(
                f"COMPANY_RESOLVER: filtered out {dropped} users from companies={sorted(filters)}"
            )
    return companies, unknown


def resolve_all_companies(usernames: List[str]) -> Dict[str, str]:
    """Resolve affiliations for a list of usernames WITHOUT applying the
    Amazon/exclude filter. Used for maintainer-attribution tables where
    transparency is desirable.
    """
    if not COMPANY_TABLE_NAME:
        return {}
    companies, _ = _resolve_from_dynamodb(usernames)
    return companies


def _resolve_from_dynamodb(usernames: List[str]) -> Tuple[Dict[str, str], List[str]]:
    """Batch read from DynamoDB company cache."""
    import boto3

    companies: Dict[str, str] = {}
    unknown: List[str] = []

    # DynamoDB String partition keys cannot be empty. Filter defensively.
    clean_usernames = [u for u in usernames if isinstance(u, str) and u.strip()]
    dropped = len(usernames) - len(clean_usernames)
    if dropped:
        logger.warning(
            f"COMPANY_RESOLVER: dropped {dropped} empty/invalid usernames before DDB read"
        )

    # Deduplicate to avoid DDB ValidationException on duplicate keys in one batch.
    clean_usernames = sorted(set(clean_usernames))
    logger.info(
        f"COMPANY_RESOLVER: looking up {len(clean_usernames)} unique usernames in {COMPANY_TABLE_NAME}"
    )

    # Use the resource-level client which auto-serializes — keys are plain values,
    # NOT {"S": "..."} wrappers. The typed wrapper form is only for the low-level
    # boto3.client('dynamodb') and double-wraps here, producing a schema error.
    ddb = boto3.resource("dynamodb").meta.client

    for i in range(0, len(clean_usernames), 100):
        batch = clean_usernames[i: i + 100]
        try:
            response = ddb.batch_get_item(
                RequestItems={
                    COMPANY_TABLE_NAME: {
                        "Keys": [{"github_username": u} for u in batch],
                    }
                }
            )
        except Exception as e:
            logger.error(
                f"COMPANY_RESOLVER: batch_get_item failed for batch starting with "
                f"'{batch[0] if batch else ''}' (size={len(batch)}): {e}"
            )
            continue

        for item in response.get("Responses", {}).get(COMPANY_TABLE_NAME, []):
            username = item.get("github_username", "")
            company = item.get("company", "")
            if username and company:
                companies[username] = normalize_company(company)

    for u in clean_usernames:
        if u not in companies and not is_bot(u):
            unknown.append(u)

    logger.info(
        f"COMPANY_RESOLVER: resolved {len(companies)} companies, "
        f"{len(unknown)} unknown (out of {len(clean_usernames)} clean usernames)"
    )
    return companies, unknown
