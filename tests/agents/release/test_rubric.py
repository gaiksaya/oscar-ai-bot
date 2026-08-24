# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for the release agent's rubric.py.

The rubric is a pure, deterministic Red/Yellow/Green mapper over per-criterion
release state. It has no external dependencies, so it is loaded directly from
its file location (the lambda modules use bare, package-less imports).

Verdict rules under test:
  - Any BLOCKING criterion not_met OR unknown          -> Red
  - All blocking met/N/A, but any NON-BLOCKING unmet/
    unknown/in_progress                                -> Yellow
  - All criteria met or not_applicable                 -> Green
"""

import importlib.util
import os

# Path to the real rubric module
_RUBRIC_PATH = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'agents', 'release', 'lambda', 'rubric.py',
)


def _load_rubric():
    spec = importlib.util.spec_from_file_location('release_rubric', _RUBRIC_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rubric = _load_rubric()


def _crit(name, status, **extra):
    return {"criterion_name": name, "status": status, **extra}


# A blocking criterion name that exists in the catalog.
BLOCKING = "security_reviews_complete"
# A non-blocking criterion name.
NON_BLOCKING = "code_coverage_not_decreased"


class TestSeverityOf:
    def test_known_blocking(self):
        assert rubric.severity_of("all_integration_tests_passing") == "blocking"

    def test_known_non_blocking(self):
        assert rubric.severity_of("roadmap_up_to_date") == "non_blocking"

    def test_unknown_defaults_to_blocking(self):
        # An unclassified gate is treated conservatively.
        assert rubric.severity_of("some_brand_new_criterion") == "blocking"

    def test_every_declared_blocking_is_blocking(self):
        for name in rubric.BLOCKING_CRITERIA:
            assert rubric.severity_of(name) == "blocking"

    def test_every_declared_non_blocking_is_non_blocking(self):
        for name in rubric.NON_BLOCKING_CRITERIA:
            assert rubric.severity_of(name) == "non_blocking"

    def test_tiers_are_disjoint(self):
        assert rubric.BLOCKING_CRITERIA.isdisjoint(rubric.NON_BLOCKING_CRITERIA)


class TestVerdictGreen:
    def test_all_met_is_green(self):
        criteria = [_crit(n, rubric.MET) for n in rubric.BLOCKING_CRITERIA]
        criteria += [_crit(n, rubric.MET) for n in rubric.NON_BLOCKING_CRITERIA]
        result = rubric.compute_verdict(criteria)
        assert result["verdict"] == rubric.GREEN
        assert result["blocking_failures"] == []
        assert result["non_blocking_gaps"] == []

    def test_not_applicable_counts_as_satisfied(self):
        # N/A on both a blocking and non-blocking criterion is still Green.
        criteria = [
            _crit(BLOCKING, rubric.NOT_APPLICABLE),
            _crit(NON_BLOCKING, rubric.NOT_APPLICABLE),
        ]
        result = rubric.compute_verdict(criteria)
        assert result["verdict"] == rubric.GREEN
        assert BLOCKING in result["not_applicable"]
        assert NON_BLOCKING in result["not_applicable"]

    def test_empty_criteria_is_green(self):
        # No criteria means no gaps -> Green (vacuously).
        result = rubric.compute_verdict([])
        assert result["verdict"] == rubric.GREEN
        assert result["counts"]["total"] == 0


class TestVerdictYellow:
    def test_non_blocking_not_met_is_yellow(self):
        criteria = [
            _crit(BLOCKING, rubric.MET),
            _crit(NON_BLOCKING, rubric.NOT_MET),
        ]
        result = rubric.compute_verdict(criteria)
        assert result["verdict"] == rubric.YELLOW
        assert NON_BLOCKING in result["non_blocking_gaps"]

    def test_non_blocking_unknown_is_yellow(self):
        criteria = [
            _crit(BLOCKING, rubric.MET),
            _crit(NON_BLOCKING, rubric.UNKNOWN),
        ]
        result = rubric.compute_verdict(criteria)
        assert result["verdict"] == rubric.YELLOW

    def test_non_blocking_in_progress_is_yellow(self):
        criteria = [
            _crit(BLOCKING, rubric.MET),
            _crit(NON_BLOCKING, rubric.IN_PROGRESS),
        ]
        result = rubric.compute_verdict(criteria)
        assert result["verdict"] == rubric.YELLOW
        assert NON_BLOCKING in result["non_blocking_gaps"]

    def test_blocking_in_progress_does_not_force_red(self):
        # in_progress on a BLOCKING criterion is neither not_met nor unknown,
        # so it does not drive Red — verdict stays Green here.
        criteria = [_crit(BLOCKING, rubric.IN_PROGRESS)]
        result = rubric.compute_verdict(criteria)
        assert result["verdict"] == rubric.GREEN
        assert result["blocking_failures"] == []
        assert result["blocking_unknowns"] == []


class TestVerdictRed:
    def test_blocking_not_met_is_red(self):
        criteria = [
            _crit(BLOCKING, rubric.NOT_MET),
            _crit(NON_BLOCKING, rubric.MET),
        ]
        result = rubric.compute_verdict(criteria)
        assert result["verdict"] == rubric.RED
        assert BLOCKING in result["blocking_failures"]

    def test_blocking_unknown_is_red(self):
        criteria = [_crit(BLOCKING, rubric.UNKNOWN)]
        result = rubric.compute_verdict(criteria)
        assert result["verdict"] == rubric.RED
        assert BLOCKING in result["blocking_unknowns"]

    def test_unknown_criterion_name_treated_as_blocking_red(self):
        # An unclassified criterion that is not_met drives Red.
        criteria = [_crit("mystery_gate", rubric.NOT_MET)]
        result = rubric.compute_verdict(criteria)
        assert result["verdict"] == rubric.RED
        assert "mystery_gate" in result["blocking_failures"]

    def test_red_takes_precedence_over_yellow(self):
        # A blocking failure AND a non-blocking gap -> Red wins.
        criteria = [
            _crit(BLOCKING, rubric.NOT_MET),
            _crit(NON_BLOCKING, rubric.NOT_MET),
        ]
        result = rubric.compute_verdict(criteria)
        assert result["verdict"] == rubric.RED


class TestBreakdownAndCounts:
    def test_missing_status_defaults_to_unknown(self):
        result = rubric.compute_verdict([{"criterion_name": BLOCKING}])
        assert result["criteria"][0]["status"] == rubric.UNKNOWN
        # unknown on a blocking criterion -> Red
        assert result["verdict"] == rubric.RED

    def test_status_is_lowercased(self):
        result = rubric.compute_verdict([_crit(BLOCKING, "MET")])
        assert result["criteria"][0]["status"] == rubric.MET
        assert result["verdict"] == rubric.GREEN

    def test_breakdown_passes_through_optional_fields(self):
        criteria = [_crit(
            BLOCKING, rubric.NOT_MET,
            details="waiting on review",
            blocking_components=["k-NN"],
            criterion_type="entrance",
            last_checked="2026-08-24T00:00:00Z",
        )]
        entry = rubric.compute_verdict(criteria)["criteria"][0]
        assert entry["details"] == "waiting on review"
        assert entry["blocking_components"] == ["k-NN"]
        assert entry["criterion_type"] == "entrance"
        assert entry["last_checked"] == "2026-08-24T00:00:00Z"
        assert entry["severity"] == "blocking"

    def test_counts_tally_correctly(self):
        criteria = [
            _crit(BLOCKING, rubric.NOT_MET),
            _crit("all_integration_tests_passing", rubric.UNKNOWN),
            _crit(NON_BLOCKING, rubric.NOT_MET),
            _crit("roadmap_up_to_date", rubric.NOT_APPLICABLE),
        ]
        counts = rubric.compute_verdict(criteria)["counts"]
        assert counts["total"] == 4
        assert counts["blocking_failures"] == 1
        assert counts["blocking_unknowns"] == 1
        assert counts["non_blocking_gaps"] == 1
        assert counts["not_applicable"] == 1
