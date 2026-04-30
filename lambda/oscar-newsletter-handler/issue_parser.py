# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Parse GitHub request issues (maintainer/repository requests)."""

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


# Matches: "[GitHub Request] Add <handle> to <repo> maintainers"
# Also: "[GitHub Request] Add <handle> as maintainer on <repo>"
# Also: "[GitHub Request] Add <handle> as a maintainer for <repo>"
_MAINTAINER_TITLE_RE = re.compile(
    r"\[GitHub Request\]\s+Add\s+@?([\w\-]+?)\s+(?:to|as\s+(?:a\s+|new\s+)?maintainer\s+(?:on|for))\s+([\w\-]+?)(?:\s+(?:repo|maintainers?))?(?:\s|$)",
    re.IGNORECASE,
)

# Matches "[Repository Request] <repo-name>"
_REPO_REQUEST_TITLE_RE = re.compile(
    r"\[Repository Request\]:?\s*(.+)",
    re.IGNORECASE,
)

# Matches GitHub handles inside issue bodies: @username or (https://github.com/username)
_HANDLE_IN_BODY_RE = re.compile(
    r"@([\w\-]+)|\(https://github\.com/([\w\-]+)\)",
)


def _is_bulk_maintainer_issue(title: str) -> bool:
    """Check if this is a bulk maintainer request (multiple handles in body)."""
    title_lower = title.lower()
    return any(
        marker in title_lower
        for marker in ["new maintainers", "baseline", "update", "and update"]
    )


def _extract_handles_from_body(body: str) -> List[str]:
    """Extract unique GitHub handles from issue body."""
    if not body:
        return []
    handles = set()
    for match in _HANDLE_IN_BODY_RE.finditer(body):
        handle = match.group(1) or match.group(2)
        if handle and handle.lower() not in {"opensearch-project", "opensearch-project/admin"}:
            handles.add(handle)
    return sorted(handles)


def parse_maintainer_issues(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract new maintainer entries from GitHub request issues.

    Returns list of {github_handle, repository, issue_url, closed_at}.
    """
    maintainers = []

    for hit in hits:
        source = hit.get("_source", hit)
        title = source.get("title", "")
        body = source.get("body", "")
        issue_url = source.get("html_url", "")
        closed_at = source.get("closed_at", "")

        match = _MAINTAINER_TITLE_RE.search(title)
        if match:
            handle = match.group(1).strip().lstrip("@")
            repository = match.group(2).strip()
            maintainers.append({
                "github_handle": handle,
                "repository": repository,
                "issue_url": issue_url,
                "closed_at": closed_at,
            })
        elif _is_bulk_maintainer_issue(title):
            # Bulk request — parse body for multiple handles
            handles = _extract_handles_from_body(body)
            # Try to extract repository from title
            repo_match = re.search(r"for\s+([\w\-]+(?:-[\w]+)*)\s+(?:repo|maintainers)", title, re.IGNORECASE)
            repository = repo_match.group(1) if repo_match else "multiple"
            for handle in handles:
                maintainers.append({
                    "github_handle": handle,
                    "repository": repository,
                    "issue_url": issue_url,
                    "closed_at": closed_at,
                })
        else:
            logger.warning(f"Could not parse maintainer issue: {title}")

    logger.info(f"Parsed {len(maintainers)} maintainers from {len(hits)} issues")
    return maintainers


def parse_repository_issues(hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Extract new repository entries from repository request issues.

    Returns list of {name, issue_url, closed_at}.
    """
    repos = []

    for hit in hits:
        source = hit.get("_source", hit)
        title = source.get("title", "")
        issue_url = source.get("html_url", "")
        closed_at = source.get("closed_at", "")

        match = _REPO_REQUEST_TITLE_RE.search(title)
        if match:
            name = match.group(1).strip()
            repos.append({
                "name": name,
                "issue_url": issue_url,
                "closed_at": closed_at,
            })

    logger.info(f"Parsed {len(repos)} repositories from {len(hits)} issues")
    return repos
