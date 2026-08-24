#!/usr/bin/env python3
# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0
#
# The OpenSearch Contributors require contributions made to
# this file be licensed under the Apache-2.0 license or a
# compatible open source license.

"""
Release-readiness Rubric.

Deterministic Red/Yellow/Green verdict over the per-criterion release state.
The severity tiers are authoritative from oscar-ai-bot issue #159; the criterion
names match ReleaseCriterionCatalog in opensearch-build-libraries.

Verdict logic:
  - Any BLOCKING criterion not_met OR unknown          -> Red
  - All blocking met/N/A, but any NON-BLOCKING unmet/
    unknown/in_progress                                -> Yellow
  - All criteria met or not_applicable                 -> Green

not_applicable is treated as satisfied (not a gap). An unknown blocking
criterion drives Red — an unverified gate never reads as ready.
"""

from typing import Any, Dict, List

# Blocking criteria (8) — a gap here can block the release.
BLOCKING_CRITERIA = frozenset({
    "documentation_draft_prs_up",       # entrance
    "release_notes_ready",              # entrance
    "release_ticket_and_forum_post",    # entrance
    "security_reviews_complete",        # entrance
    "performance_tests_posted",         # exit
    "documentation_reviewed_signed_off",  # exit
    "all_integration_tests_passing",    # exit
    "release_blog_ready",               # exit
})

# Non-blocking criteria (4) — quality/process signals; do not force a Red.
NON_BLOCKING_CRITERIA = frozenset({
    "release_owners_assigned",          # entrance
    "sanity_testing_done",              # entrance
    "code_coverage_not_decreased",      # entrance
    "roadmap_up_to_date",               # entrance
})

# Verdict constants
RED = "red"
YELLOW = "yellow"
GREEN = "green"

# Status constants
MET = "met"
NOT_MET = "not_met"
IN_PROGRESS = "in_progress"
UNKNOWN = "unknown"
NOT_APPLICABLE = "not_applicable"

_SATISFIED = frozenset({MET, NOT_APPLICABLE})


def severity_of(criterion_name: str) -> str:
    """Return 'blocking' or 'non_blocking' for a criterion name.

    Unknown criterion names default to 'blocking' — an unclassified gate is
    treated conservatively rather than silently ignored.
    """
    if criterion_name in NON_BLOCKING_CRITERIA:
        return "non_blocking"
    return "blocking"


def compute_verdict(criteria: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute the R/Y/G verdict and a structured breakdown.

    Args:
        criteria: list of dicts each with at least 'criterion_name' and 'status'.
            Optional keys ('details', 'blocking_components', 'criterion_type',
            'last_checked') are passed through into the breakdown.

    Returns:
        Dict with:
          - verdict: 'red' | 'yellow' | 'green'
          - blocking_failures: names of blocking criteria that are not_met
          - blocking_unknowns: names of blocking criteria that are unknown
          - non_blocking_gaps: names of non-blocking criteria unmet/unknown/in_progress
          - not_applicable: names treated as satisfied via N/A
          - criteria: normalized per-criterion breakdown (with severity)
          - counts: tallies for quick display
    """
    blocking_failures: List[str] = []
    blocking_unknowns: List[str] = []
    non_blocking_gaps: List[str] = []
    not_applicable: List[str] = []
    breakdown: List[Dict[str, Any]] = []

    for c in criteria:
        name = c.get("criterion_name", "")
        status = (c.get("status") or UNKNOWN).lower()
        severity = severity_of(name)

        breakdown.append({
            "criterion_name": name,
            "status": status,
            "severity": severity,
            "criterion_type": c.get("criterion_type"),
            "details": c.get("details"),
            "blocking_components": c.get("blocking_components"),
            "last_checked": c.get("last_checked"),
        })

        if status == NOT_APPLICABLE:
            not_applicable.append(name)
            continue

        if severity == "blocking":
            if status == NOT_MET:
                blocking_failures.append(name)
            elif status == UNKNOWN:
                blocking_unknowns.append(name)
        else:  # non_blocking
            if status in (NOT_MET, UNKNOWN, IN_PROGRESS):
                non_blocking_gaps.append(name)

    if blocking_failures or blocking_unknowns:
        verdict = RED
    elif non_blocking_gaps:
        verdict = YELLOW
    else:
        verdict = GREEN

    return {
        "verdict": verdict,
        "blocking_failures": blocking_failures,
        "blocking_unknowns": blocking_unknowns,
        "non_blocking_gaps": non_blocking_gaps,
        "not_applicable": not_applicable,
        "criteria": breakdown,
        "counts": {
            "total": len(criteria),
            "blocking_failures": len(blocking_failures),
            "blocking_unknowns": len(blocking_unknowns),
            "non_blocking_gaps": len(non_blocking_gaps),
            "not_applicable": len(not_applicable),
        },
    }
