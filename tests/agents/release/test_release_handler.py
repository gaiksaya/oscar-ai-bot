# Copyright OpenSearch Contributors
# SPDX-License-Identifier: Apache-2.0

"""Tests for the release agent's release_handler.py.

The handler's query functions depend on aws_utils.opensearch_request and the
config proxy, so those are replaced with mocks at import time (matching the
pattern in the SecurityAdvisories handler tests). The real rubric module is
loaded from the lambda path so verdict logic is exercised end-to-end.

Covered:
  - pure helpers: _validate_version, _parse_date, _is_newer, _cadence_phase
  - handle_get_release_status: missing version, empty hits, dedup-to-latest,
    verdict wiring, release_issue surfacing, query error
  - handle_get_release_window: missing version, not-found, days/phase math,
    query error
"""

import importlib.util
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

_LAMBDA_PATH = os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'agents', 'release', 'lambda',
)


def _make_mock_config():
    cfg = MagicMock()
    cfg.release_state_index = 'opensearch_release_state'
    cfg.release_schedule_index = 'opensearch_release_schedule'
    return cfg


def _load_handler(mock_opensearch_request=None, mock_config=None):
    """Load release_handler with mocked aws_utils + config, real rubric."""
    if _LAMBDA_PATH not in sys.path:
        sys.path.insert(0, _LAMBDA_PATH)

    mock_aws_utils = MagicMock()
    mock_aws_utils.opensearch_request = mock_opensearch_request or MagicMock()

    mock_config_mod = MagicMock()
    mock_config_mod.config = mock_config or _make_mock_config()

    with patch.dict('sys.modules', {
        'aws_utils': mock_aws_utils,
        'config': mock_config_mod,
    }):
        spec = importlib.util.spec_from_file_location(
            'release_handler',
            os.path.join(_LAMBDA_PATH, 'release_handler.py'),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod, mock_aws_utils.opensearch_request


def _hit(source):
    return {"_source": source}


def _search_response(sources):
    return {"hits": {"hits": [_hit(s) for s in sources]}}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestValidateVersion:
    def test_returns_trimmed_version(self):
        mod, _ = _load_handler()
        assert mod._validate_version({"version": "  3.8.0 "}) == "3.8.0"

    def test_missing_returns_none(self):
        mod, _ = _load_handler()
        assert mod._validate_version({}) is None

    def test_blank_returns_none(self):
        mod, _ = _load_handler()
        assert mod._validate_version({"version": "   "}) is None

    def test_non_string_returns_as_is_when_truthy(self):
        mod, _ = _load_handler()
        # A non-string truthy value is returned unchanged (no strip).
        assert mod._validate_version({"version": 380}) == 380


class TestParseDate:
    def test_date_only(self):
        mod, _ = _load_handler()
        dt = mod._parse_date("2026-08-24")
        assert dt == datetime(2026, 8, 24, tzinfo=timezone.utc)

    def test_iso_with_z(self):
        mod, _ = _load_handler()
        dt = mod._parse_date("2026-08-24T12:30:00Z")
        assert dt == datetime(2026, 8, 24, 12, 30, tzinfo=timezone.utc)

    def test_iso_with_offset(self):
        mod, _ = _load_handler()
        dt = mod._parse_date("2026-08-24T12:30:00+00:00")
        assert dt == datetime(2026, 8, 24, 12, 30, tzinfo=timezone.utc)

    def test_none_returns_none(self):
        mod, _ = _load_handler()
        assert mod._parse_date(None) is None

    def test_empty_returns_none(self):
        mod, _ = _load_handler()
        assert mod._parse_date("") is None

    def test_unparseable_returns_none(self):
        mod, _ = _load_handler()
        assert mod._parse_date("not-a-date") is None


class TestIsNewer:
    def test_candidate_newer(self):
        mod, _ = _load_handler()
        assert mod._is_newer("2026-08-24", "2026-08-01") is True

    def test_candidate_older(self):
        mod, _ = _load_handler()
        assert mod._is_newer("2026-08-01", "2026-08-24") is False

    def test_candidate_missing_is_not_newer(self):
        mod, _ = _load_handler()
        assert mod._is_newer(None, "2026-08-01") is False

    def test_current_missing_makes_candidate_newer(self):
        mod, _ = _load_handler()
        assert mod._is_newer("2026-08-01", None) is True


class TestCadencePhase:
    def test_out_of_window(self):
        mod, _ = _load_handler()
        assert mod._cadence_phase(20, 30) == "out_of_window"

    def test_pre_rc_daily_bounds(self):
        mod, _ = _load_handler()
        assert mod._cadence_phase(14, 20) == "pre_rc_daily"
        assert mod._cadence_phase(8, 14) == "pre_rc_daily"

    def test_pre_rc_frequent_bounds(self):
        mod, _ = _load_handler()
        assert mod._cadence_phase(7, 13) == "pre_rc_frequent"
        assert mod._cadence_phase(0, 6) == "pre_rc_frequent"

    def test_rc_passed_falls_through_to_release_phases(self):
        mod, _ = _load_handler()
        # RC in the past, release 5 days out -> rc_to_release
        assert mod._cadence_phase(-1, 5) == "rc_to_release"

    def test_final_push(self):
        mod, _ = _load_handler()
        assert mod._cadence_phase(-3, 2) == "final_push"
        assert mod._cadence_phase(-3, 0) == "final_push"

    def test_released(self):
        mod, _ = _load_handler()
        assert mod._cadence_phase(-10, -1) == "released"

    def test_not_scheduled_when_no_dates(self):
        mod, _ = _load_handler()
        assert mod._cadence_phase(None, None) == "not_scheduled"

    def test_released_takes_precedence(self):
        mod, _ = _load_handler()
        # Negative days_to_release wins even if days_to_rc is in a window.
        assert mod._cadence_phase(5, -1) == "released"


# ---------------------------------------------------------------------------
# handle_get_release_status
# ---------------------------------------------------------------------------

class TestGetReleaseStatus:
    def test_missing_version_returns_error(self):
        mod, req = _load_handler()
        result = mod.handle_get_release_status({})
        assert "error" in result
        req.assert_not_called()

    def test_no_hits_returns_not_found(self):
        req = MagicMock(return_value=_search_response([]))
        mod, _ = _load_handler(mock_opensearch_request=req)
        result = mod.handle_get_release_status({"version": "3.8.0"})
        assert result["found"] is False
        assert result["version"] == "3.8.0"

    def test_green_verdict_wiring(self):
        req = MagicMock(return_value=_search_response([
            {"criterion_name": "security_reviews_complete", "status": "met"},
            {"criterion_name": "code_coverage_not_decreased", "status": "met"},
        ]))
        mod, _ = _load_handler(mock_opensearch_request=req)
        result = mod.handle_get_release_status({"version": "3.8.0"})
        assert result["found"] is True
        assert result["verdict"] == "green"
        assert result["counts"]["total"] == 2

    def test_red_verdict_from_blocking_failure(self):
        req = MagicMock(return_value=_search_response([
            {"criterion_name": "security_reviews_complete", "status": "not_met"},
        ]))
        mod, _ = _load_handler(mock_opensearch_request=req)
        result = mod.handle_get_release_status({"version": "3.8.0"})
        assert result["verdict"] == "red"
        assert "security_reviews_complete" in result["blocking_failures"]

    def test_dedup_keeps_latest_by_last_checked(self):
        req = MagicMock(return_value=_search_response([
            {"criterion_name": "security_reviews_complete", "status": "not_met",
             "last_checked": "2026-08-01T00:00:00Z"},
            {"criterion_name": "security_reviews_complete", "status": "met",
             "last_checked": "2026-08-24T00:00:00Z"},
        ]))
        mod, _ = _load_handler(mock_opensearch_request=req)
        result = mod.handle_get_release_status({"version": "3.8.0"})
        # Latest says met -> green, and only one criterion counted.
        assert result["counts"]["total"] == 1
        assert result["verdict"] == "green"

    def test_release_issue_surfaced(self):
        req = MagicMock(return_value=_search_response([
            {"criterion_name": "security_reviews_complete", "status": "met",
             "release_issue": "https://github.com/opensearch-project/opensearch-build/issues/1"},
        ]))
        mod, _ = _load_handler(mock_opensearch_request=req)
        result = mod.handle_get_release_status({"version": "3.8.0"})
        assert result["release_issue"].endswith("/issues/1")

    def test_query_uses_configured_index_and_filters(self):
        req = MagicMock(return_value=_search_response([]))
        mod, _ = _load_handler(mock_opensearch_request=req)
        mod.handle_get_release_status({"version": "3.8.0"})
        method, path, body = req.call_args[0]
        assert method == "GET"
        assert path == "/opensearch_release_state/_search"
        filters = body["query"]["bool"]["filter"]
        assert {"term": {"version": "3.8.0"}} in filters
        assert {"term": {"doc_type": "criterion"}} in filters

    def test_query_error_returns_error_payload(self):
        req = MagicMock(side_effect=RuntimeError("boom"))
        mod, _ = _load_handler(mock_opensearch_request=req)
        result = mod.handle_get_release_status({"version": "3.8.0"})
        assert result["type"] == "query_error"
        assert "boom" in result["error"]


# ---------------------------------------------------------------------------
# handle_get_release_window
# ---------------------------------------------------------------------------

class TestGetReleaseWindow:
    def test_missing_version_returns_error(self):
        mod, req = _load_handler()
        result = mod.handle_get_release_window({})
        assert "error" in result
        req.assert_not_called()

    def test_not_found(self):
        req = MagicMock(return_value=_search_response([]))
        mod, _ = _load_handler(mock_opensearch_request=req)
        result = mod.handle_get_release_window({"version": "3.8.0"})
        assert result["found"] is False

    def test_days_and_phase_math(self):
        req = MagicMock(return_value=_search_response([{
            "status": "active",
            "rc_date": "2026-09-01",
            "release_date": "2026-09-15",
            "release_manager": "rm@example.com",
            "release_issue": "https://github.com/x/y/issues/2",
        }]))
        mod, _ = _load_handler(mock_opensearch_request=req)
        # Pin "now" so day math is deterministic.
        mod._now = lambda: datetime(2026, 8, 24, tzinfo=timezone.utc)
        result = mod.handle_get_release_window({"version": "3.8.0"})
        assert result["found"] is True
        assert result["days_to_rc"] == 8
        assert result["days_to_release"] == 22
        assert result["cadence_phase"] == "pre_rc_daily"
        assert result["status"] == "active"
        assert result["release_manager"] == "rm@example.com"

    def test_query_uses_schedule_index_sorted_desc(self):
        req = MagicMock(return_value=_search_response([]))
        mod, _ = _load_handler(mock_opensearch_request=req)
        mod.handle_get_release_window({"version": "3.8.0"})
        method, path, body = req.call_args[0]
        assert method == "GET"
        assert path == "/opensearch_release_schedule/_search"
        assert body["size"] == 1
        assert body["sort"] == [{"registered_at": {"order": "desc"}}]

    def test_missing_dates_yield_none_and_not_scheduled(self):
        req = MagicMock(return_value=_search_response([{"status": "active"}]))
        mod, _ = _load_handler(mock_opensearch_request=req)
        result = mod.handle_get_release_window({"version": "3.8.0"})
        assert result["days_to_rc"] is None
        assert result["days_to_release"] is None
        assert result["cadence_phase"] == "not_scheduled"

    def test_query_error_returns_error_payload(self):
        req = MagicMock(side_effect=RuntimeError("kaboom"))
        mod, _ = _load_handler(mock_opensearch_request=req)
        result = mod.handle_get_release_window({"version": "3.8.0"})
        assert result["type"] == "query_error"
        assert "kaboom" in result["error"]
