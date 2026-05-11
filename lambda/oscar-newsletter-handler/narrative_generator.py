# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""LLM-backed narrative generator for per-company contribution summaries.

Produces a 2-3 sentence paragraph describing what each company's contributors
worked on in the month, synthesized from actual PR titles. Used in the
"Pull Requests Contributions Overview" column of the contributor metrics
table.
"""

import json
import logging
import os
from typing import Any, Dict, List

import boto3
from botocore.config import Config as BotoConfig

logger = logging.getLogger(__name__)

# Match the supervisor's default model (see utils/foundation_models.py::CLAUDE_4_5_SONNET).
# The "us." prefix indicates a cross-region inference profile.
_DEFAULT_MODEL_ID = os.environ.get(
    "NEWSLETTER_NARRATIVE_MODEL_ID",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
)

_bedrock_runtime = boto3.client(
    "bedrock-runtime",
    config=BotoConfig(
        read_timeout=60,
        connect_timeout=10,
        retries={"max_attempts": 2, "mode": "standard"},
        max_pool_connections=20,
    ),
)


def _rule_based_fallback(
    company: str,
    users: List[str],
    titles_by_repo: Dict[str, List[str]],
) -> str:
    """Non-LLM fallback: concatenate top repos + a sample of titles.

    Used when Bedrock is unavailable or the call fails.
    """
    if not titles_by_repo:
        return ""
    sorted_repos = sorted(
        titles_by_repo.items(), key=lambda x: len(x[1]), reverse=True
    )
    primary_repo, primary_titles = sorted_repos[0]
    primary_examples = "; ".join(primary_titles[:3])
    parts = [
        f"Core contributions include {primary_repo} repository: {primary_examples}."
    ]
    if len(sorted_repos) > 1:
        secondary = [
            f"{r} ({titles[0] if titles else ''})"
            for r, titles in sorted_repos[1:4]
        ]
        parts.append(f"Secondary contributions to {', '.join(secondary)}.")
    return " ".join(parts)


def generate_company_narrative(
    company: str,
    users: List[str],
    titles_by_repo: Dict[str, List[str]],
    month: str,
    year: str,
) -> str:
    """Generate a 2-3 sentence narrative for one company's contributions.

    Calls Bedrock Claude. Falls back to a rule-based summary on any error
    so the newsletter always has *something* in the column.
    """
    if not titles_by_repo:
        return ""

    # Build the structured input for the LLM
    repo_lines = []
    for repo, titles in sorted(
        titles_by_repo.items(), key=lambda x: len(x[1]), reverse=True
    ):
        titles_str = "\n    - ".join(titles[:10])
        repo_lines.append(f"  - **{repo}** ({len(titles)} PRs):\n    - {titles_str}")

    user_intro = users[0] if len(users) == 1 else f"the {company} team"

    prompt = f"""You are writing a short summary paragraph for the OpenSearch community newsletter, describing what {user_intro} contributed to the OpenSearch project in {month} {year}.

Here are their pull requests grouped by repository (sorted by volume):

{chr(10).join(repo_lines)}

Write a single, concise paragraph (2-3 sentences max) that:
- Starts with "{user_intro}" or a similar active-voice subject
- Describes the main themes of their work (pick 3-5 key contributions across their top repositories)
- Uses past tense, active voice
- Keeps technical terms from the PR titles where relevant
- Does NOT invent details not present in the titles
- Does NOT list every single PR — synthesize themes
- Does NOT use phrases like "according to the data" or meta-commentary

Return ONLY the paragraph text, no markdown headings, no preamble, no quotation marks."""

    try:
        response = _bedrock_runtime.invoke_model(
            modelId=_DEFAULT_MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 400,
                "temperature": 0.3,
                "messages": [
                    {"role": "user", "content": prompt},
                ],
            }),
        )
        payload = json.loads(response["body"].read())
        content = payload.get("content") or []
        text_parts = [block.get("text", "") for block in content if block.get("type") == "text"]
        narrative = " ".join(text_parts).strip()

        if not narrative:
            logger.warning(
                f"NARRATIVE: Empty response from Bedrock for company={company}; "
                f"falling back to rule-based"
            )
            return _rule_based_fallback(company, users, titles_by_repo)

        logger.info(
            f"NARRATIVE: Generated {len(narrative)} chars for company={company}"
        )
        return narrative
    except Exception as e:
        logger.error(
            f"NARRATIVE: Bedrock invoke_model failed for company={company}: {e}; "
            f"falling back to rule-based"
        )
        return _rule_based_fallback(company, users, titles_by_repo)
