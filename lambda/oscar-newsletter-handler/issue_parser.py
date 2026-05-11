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


# Headings that indicate an additions section.
# Matches markdown headings (#+) and bold labels (**foo**):
#   ### Add
#   #### New Maintainer
#   ## To add
#   **Add:**
#   **New Maintainers:**
_ADD_SECTION_HEADING_RE = re.compile(
    r"^\s*(?:#+|\*\*)\s*"
    r"(?:to\s+add|add|additions?|new\s+maintainers?)"
    r"\s*:?\s*(?:\*\*)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Headings that indicate a removal / emeritus section — everything after this
# heading is skipped.
_REMOVAL_SECTION_HEADING_RE = re.compile(
    r"^\s*(?:#+|\*\*)\s*"
    r"(?:remove|removed|removal|emeritus|move\s+to\s+emeritus|delete|deleted|retired)"
    r"\s*:?\s*(?:\*\*)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Any subsequent section heading — used to find where the "Add" section ends.
_ANY_SECTION_HEADING_RE = re.compile(
    r"^\s*(?:#+|\*\*[\w\s]+\*\*:?\s*$)",
    re.IGNORECASE | re.MULTILINE,
)


def _extract_additions_section(body: str) -> str:
    """Return only the portion of the body that contains additions.

    Logic:
      1. If the body has an explicit additions heading (Add / New Maintainer
         / Additions), take from there until the next section heading.
      2. Otherwise cut off the body at the first removal/emeritus heading,
         so handles listed under "Remove" / "Emeritus" aren't harvested.
      3. If neither is present, return the whole body (typical for simple
         single-maintainer requests).
    """
    if not body:
        return ""

    # Case 1: explicit additions section
    add_match = _ADD_SECTION_HEADING_RE.search(body)
    if add_match:
        start = add_match.end()
        next_match = _ANY_SECTION_HEADING_RE.search(body, pos=start)
        end = next_match.start() if next_match else len(body)
        return body[start:end]

    # Case 2: truncate at first removal heading
    remove_match = _REMOVAL_SECTION_HEADING_RE.search(body)
    if remove_match:
        return body[: remove_match.start()]

    # Case 3: no structured sections
    return body


def _is_bulk_maintainer_issue(title: str) -> bool:
    """Check if this is a bulk maintainer request (multiple handles in body)."""
    title_lower = title.lower()
    return any(
        marker in title_lower
        for marker in ["new maintainers", "baseline", "update", "and update"]
    )


def _extract_handles_from_body(body: str, author: str = "") -> List[str]:
    """Extract unique GitHub handles from the additions portion of the body.

    When the body has an explicit 'Add' / 'To add' / 'Additions' / 'New
    Maintainer' section, only that section is searched. When the body has a
    removal/emeritus section, everything from that heading onward is skipped.
    Otherwise the whole body is searched. Filters out known-noise handles,
    team mentions, and the issue author.
    """
    if not body:
        return []
    additions = _extract_additions_section(body)
    handles = set()
    for match in _HANDLE_IN_BODY_RE.finditer(additions):
        handle = match.group(1) or match.group(2)
        if not handle:
            continue
        lower = handle.lower()
        # Noise filters
        if lower in {"opensearch-project", "opensearch-project/admin"}:
            continue
        if "/" in handle:
            # Team mentions like @opensearch-project/admin
            continue
        if author and lower == author.lower():
            # Issue author is the requester, not the maintainer being added
            continue
        handles.add(handle)
    return sorted(handles)


def _extract_repo_from_title(title: str) -> str:
    """Try to pull a repository name out of a maintainer-request title.

    Matches the "add ... maintainer(s) ... <preposition> <repo>" shape, where
    <repo> can be one word, hyphenated, or multiple words. Multi-word phrases
    are kebab-cased since that's the repo-naming convention.

    Returns "multiple" when nothing extracts cleanly.
    """
    # "... <preposition> <repo phrase> repo/repository/maintainers"
    m = re.search(
        r"\b(?:to|on|for|into)\s+(.+?)\s+(?:repo|repository|maintainers?)\b",
        title,
        re.IGNORECASE,
    )
    if m:
        phrase = m.group(1).strip().rstrip(".,;:")
        words = phrase.split()
        if len(words) == 1:
            if words[0].lower() not in {"new", "a", "the", "as"}:
                return words[0]
        elif len(words) > 1:
            # Multi-word: convert to kebab-case
            return "-".join(w.lower() for w in words)

    # "... <preposition> <single-word-repo>" at end of title
    m = re.search(r"\b(?:to|on|for|into)\s+([\w\-]+)\s*\.?\s*$", title, re.IGNORECASE)
    if m:
        candidate = m.group(1).strip()
        if candidate.lower() not in {"new", "a", "the", "as"}:
            return candidate

    return "multiple"


def parse_maintainer_issues(hits: List[Dict[str, Any]]) -> List["NewMaintainerEntry"]:
    """Extract new maintainer entries from GitHub request issues.

    Strategy:
      1. Try the title regex for clean "Add @handle as maintainer for <repo>" pattern.
      2. Otherwise, extract all @handles from the body (filtering noise and the
         issue author) and use title-derived repo name. This handles bulk
         requests AND single-maintainer issues where the handle only appears
         in the body (e.g. "add Rishav Sagar (@RS146BIJAY) as a maintainer").
    """
    from models import NewMaintainerEntry
    maintainers: List[NewMaintainerEntry] = []

    for hit in hits:
        source = hit.get("_source", hit)
        title = source.get("title", "")
        body = source.get("body", "")
        issue_url = source.get("html_url", "")
        closed_at = source.get("closed_at", "")
        author = source.get("user_login", "")

        match = _MAINTAINER_TITLE_RE.search(title)
        if match:
            handle = match.group(1).strip().lstrip("@")
            repository = match.group(2).strip()
            maintainers.append(NewMaintainerEntry(
                github_handle=handle,
                repository=repository,
                issue_url=issue_url,
                closed_at=closed_at,
            ))
            continue

        # Fallback: pull handle(s) from body
        handles = _extract_handles_from_body(body, author=author)
        if not handles:
            logger.warning(f"No handles found in issue: {title!r}")
            continue

        repository = _extract_repo_from_title(title)
        logger.info(
            f"Parsed {len(handles)} handle(s) from body for issue {title[:60]!r} "
            f"→ repo={repository}"
        )
        for handle in handles:
            maintainers.append(NewMaintainerEntry(
                github_handle=handle,
                repository=repository,
                issue_url=issue_url,
                closed_at=closed_at,
            ))

    logger.info(f"Parsed {len(maintainers)} maintainers from {len(hits)} issues")
    return maintainers


def parse_repository_issues(hits: List[Dict[str, Any]]) -> List["NewRepositoryEntry"]:
    """Extract new repository entries from repository request issues.

    Strategy:
      1. Try to pull the repo name from the body's "What is the new GitHub
         repository name?" template field. Most reliable — uses the author's
         stated value rather than whatever prose they put in the title.
      2. Fall back to the title regex `[Repository Request] <name>`.
    """
    from models import NewRepositoryEntry
    repos: List[NewRepositoryEntry] = []

    for hit in hits:
        source = hit.get("_source", hit)
        title = source.get("title", "")
        body = source.get("body", "")
        issue_url = source.get("html_url", "")
        closed_at = source.get("closed_at", "")

        name = _extract_repo_name_from_body(body)
        if not name:
            match = _REPO_REQUEST_TITLE_RE.search(title)
            if match:
                name = match.group(1).strip()

        if name:
            repos.append(NewRepositoryEntry(
                name=name,
                issue_url=issue_url,
                closed_at=closed_at,
            ))
        else:
            logger.warning(f"Could not parse repository name from issue: {title!r}")

    logger.info(f"Parsed {len(repos)} repositories from {len(hits)} issues")
    return repos


# Matches the "What is the new GitHub repository name?" template field.
# Captures whatever the author wrote in the value — can be quoted in
# backticks, prefixed with ">", or plain text. Stops at the next blank
# line or section heading.
_REPO_NAME_FIELD_RE = re.compile(
    r"(?:^|\n)\s*(?:#+\s*)?(?:\d+\.\s*)?[Ww]hat\s+is\s+the\s+new\s+GitHub\s+repository\s+name\s*\??\s*\n+"
    r"(?:>\s*)?`?([^\n`]+?)`?\s*(?:\n|$)",
    re.MULTILINE,
)


def _extract_repo_name_from_body(body: str) -> str:
    """Pull the repo name from the "What is the new GitHub repository name?"
    template field. Returns empty string if not found.

    Handles common authoring styles:
        `foo-bar`
        > foo-bar
        > opensearch-project/foo-bar
        foo-bar
    Strips the `opensearch-project/` prefix if present.
    """
    if not body:
        return ""
    m = _REPO_NAME_FIELD_RE.search(body)
    if not m:
        return ""
    name = m.group(1).strip()
    # Strip org prefix, backticks, surrounding punctuation
    name = name.strip("`").strip()
    if name.startswith("opensearch-project/"):
        name = name[len("opensearch-project/"):]
    # Ignore placeholder / non-answer text
    if name.lower() in {"_no response_", "", "n/a", "tbd", "todo"}:
        return ""
    # Sanity check — repo names don't have spaces
    if " " in name:
        return ""
    return name
