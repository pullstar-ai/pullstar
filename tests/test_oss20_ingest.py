"""
OSS-20 ingest tests (requirements 1-11, 27-28) plus correction tests.

All GitHub network calls are patched — these tests run offline.
GraphQL tests patch pullstar.ingest._gql.

Window convention for tests that have actual PR/review data:
  _WIN_START / _WIN_END bracket the default mock timestamps so the
  exact-boundary post-filter does not silently drop them.
"""

from __future__ import annotations

import inspect
import os
import socket
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

# Standard analysis window used for tests that return real mock data.
# Default _pr_node created_at = "2026-07-01T10:00:00Z" and
# default _review_node submitted_at = "2026-07-15T10:00:00Z" both fall inside.
_WIN_START = datetime(2026, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
_WIN_END   = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers: minimal mock responses
# ---------------------------------------------------------------------------

def _search_response(nodes: list, has_next: bool = False) -> dict:
    return {"search": {"pageInfo": {"hasNextPage": has_next, "endCursor": None}, "nodes": nodes}}


def _pr_node(number: int, repo: str, state: str = "MERGED",
             created_at: str = "2026-07-01T10:00:00Z") -> dict:
    return {
        "number":      number,
        "title":       f"PR #{number}",
        "url":         f"https://github.com/{repo}/pull/{number}",
        "state":       state,
        "createdAt":   created_at,
        "mergedAt":    "2026-07-02T10:00:00Z" if state == "MERGED" else None,
        "additions":   50,
        "deletions":   10,
        "changedFiles": 3,
        "commits":     {"totalCount": 2},
        "body":        "Some description",
        "labels":      {"nodes": []},
        "baseRefName": "main",
        "repository":  {"nameWithOwner": repo},
        "reviews":     {"totalCount": 1},
    }


def _review_node(number: int, repo: str, login: str,
                 submitted_at: str = "2026-07-15T10:00:00Z") -> dict:
    return {
        "number":     number,
        "title":      f"PR #{number}",
        "repository": {"nameWithOwner": repo},
        "reviews": {"nodes": [
            {
                "state":       "APPROVED",
                "body":        "LGTM",
                "author":      {"login": login},
                "submittedAt": submitted_at,
            }
        ]},
    }


def _make_gql_side_effects(
    pr_nodes: list,
    review_nodes: list,
    user_name: str = "Test Engineer",
) -> list:
    """Side-effect list for _gql mock with no repositories filter (3 calls)."""
    return [
        {"user": {"name": user_name}},
        _search_response(pr_nodes),
        _search_response(review_nodes),
    ]


# ---------------------------------------------------------------------------
# Test 1 + 28: import pullstar.ingest has no network/file side effects
# ---------------------------------------------------------------------------

_IMPORT_PROBE = textwrap.dedent("""
    import socket, os, sys

    # Patch only connection-initiating functions, not socket.socket itself.
    # ssl.py subclasses socket.socket at load time — replacing it with a
    # function would crash ssl import, which requests/urllib3 triggers.
    def _blocked(*a, **k):
        raise AssertionError("network access during 'import pullstar.ingest'")
    socket.create_connection = _blocked
    socket.getaddrinfo = _blocked

    import pullstar.ingest
    assert not os.listdir("."), f"import created files: {os.listdir('.')}"
    print("OK")
""")


def test_import_is_side_effect_free(tmp_path) -> None:
    """Test 1 + 28: importing pullstar.ingest makes no network calls and creates no files."""
    proc = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip().endswith("OK")


# ---------------------------------------------------------------------------
# Tests 2: returns IngestedProfile in memory
# ---------------------------------------------------------------------------

@patch("pullstar.ingest._gql")
def test_returns_in_memory_profile(mock_gql) -> None:
    """Test 2: ingest_developer_activity returns a dict with IngestedProfile schema."""
    from pullstar.ingest import ingest_developer_activity

    mock_gql.side_effect = _make_gql_side_effects(
        pr_nodes=[_pr_node(1, "owner/repo-a")],
        review_nodes=[_review_node(5, "owner/repo-a", "testuser")],
    )

    result = ingest_developer_activity(
        "testuser", token="fake-token",
        start_at=_WIN_START, end_at=_WIN_END,
    )

    required_keys = {
        "engineer_login", "engineer_name", "org", "lookback_days",
        "ingested_at", "prs_authored", "reviews_given", "reviews_received",
        "summary_stats",
    }
    assert required_keys.issubset(result.keys())
    assert result["engineer_login"] == "testuser"
    assert result["engineer_name"] == "Test Engineer"
    assert isinstance(result["prs_authored"], list)
    assert isinstance(result["reviews_given"], list)
    assert isinstance(result["summary_stats"], dict)
    # Data should survive the post-filter (timestamps are inside _WIN_START.._WIN_END)
    assert len(result["prs_authored"]) == 1
    assert len(result["reviews_given"]) == 1


# ---------------------------------------------------------------------------
# Tests 3, 4, 5: per-repo search (OR semantics), exclusion, and post-filter
# ---------------------------------------------------------------------------

@patch("pullstar.ingest._gql")
def test_repo_scope_restricts_authored_prs(mock_gql) -> None:
    """Test 3: repositories=[A, B] — PRs from C are excluded; A and B are both present.

    Per-repo: search for A (returns PR#1 from A and spurious PR#3 from C),
    then search for B (returns PR#2 from B). Post-filter removes PR#3 from C.
    """
    from pullstar.ingest import ingest_developer_activity

    mock_gql.side_effect = [
        {"user": {"name": "Test"}},
        # authored search for repo-a: PR#1 (in-window) and spurious PR#3 from C
        _search_response([_pr_node(1, "owner/repo-a"), _pr_node(3, "owner/repo-c")]),
        # authored search for repo-b: PR#2 (in-window)
        _search_response([_pr_node(2, "owner/repo-b")]),
        # reviews for repo-a: empty
        _search_response([]),
        # reviews for repo-b: empty
        _search_response([]),
    ]

    result = ingest_developer_activity(
        "testuser",
        token="fake-token",
        start_at=_WIN_START,
        end_at=_WIN_END,
        repositories=["owner/repo-a", "owner/repo-b"],
    )

    repos_in_result = {p["repository"] for p in result["prs_authored"]}
    assert "owner/repo-c" not in repos_in_result
    assert "owner/repo-a" in repos_in_result
    assert "owner/repo-b" in repos_in_result


@patch("pullstar.ingest._gql")
def test_repo_scope_restricts_reviews(mock_gql) -> None:
    """Test 4: repositories=[A, B] — reviews from C are excluded."""
    from pullstar.ingest import ingest_developer_activity

    mock_gql.side_effect = [
        {"user": {"name": "Test"}},
        _search_response([]),   # authored repo-a
        _search_response([]),   # authored repo-b
        # reviews for repo-a: one from A (in-window) + spurious from C
        _search_response([
            _review_node(10, "owner/repo-a", "testuser"),
            _review_node(12, "owner/repo-c", "testuser"),
        ]),
        # reviews for repo-b: one from B (in-window)
        _search_response([_review_node(11, "owner/repo-b", "testuser")]),
    ]

    result = ingest_developer_activity(
        "testuser",
        token="fake-token",
        start_at=_WIN_START,
        end_at=_WIN_END,
        repositories=["owner/repo-a", "owner/repo-b"],
    )

    review_repos = {r["repository"] for r in result["reviews_given"]}
    assert "owner/repo-c" not in review_repos


@patch("pullstar.ingest._gql")
def test_repo_c_cannot_contaminate_ab_analysis(mock_gql) -> None:
    """Test 5: no repo-c data appears anywhere in an A-only result."""
    from pullstar.ingest import ingest_developer_activity

    # A-only search returns PR#1 from A and a spurious PR#2 from C
    mock_gql.side_effect = [
        {"user": {"name": "T"}},
        _search_response([_pr_node(1, "owner/repo-a"), _pr_node(2, "owner/repo-c")]),
        _search_response([_review_node(20, "owner/repo-c", "testuser")]),
    ]

    result = ingest_developer_activity(
        "testuser",
        token="fake-token",
        start_at=_WIN_START,
        end_at=_WIN_END,
        repositories=["owner/repo-a"],
    )

    all_repos = (
        {p["repository"] for p in result["prs_authored"]}
        | {r["repository"] for r in result["reviews_given"]}
    )
    assert "owner/repo-c" not in all_repos
    assert "owner/repo-a" in all_repos


# ---------------------------------------------------------------------------
# Tests 6, 7: exact start_at/end_at and one deterministic time window
# ---------------------------------------------------------------------------

@patch("pullstar.ingest._gql")
def test_exact_start_end_honored(mock_gql) -> None:
    """Test 6: explicit start_at/end_at → date strings appear in query variables."""
    from pullstar.ingest import ingest_developer_activity

    mock_gql.side_effect = _make_gql_side_effects(pr_nodes=[], review_nodes=[])

    start = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    end   = datetime(2026, 6, 30, 0, 0, 0, tzinfo=timezone.utc)

    ingest_developer_activity("testuser", token="fake-token", start_at=start, end_at=end)

    pr_call = mock_gql.call_args_list[1]
    query_var = pr_call.args[2]["query"]
    assert "2026-06-01" in query_var
    assert "2026-06-30" in query_var


@patch("pullstar.ingest._gql")
def test_one_deterministic_time_window(mock_gql) -> None:
    """Test 7: PR query and review query share the same since date."""
    from pullstar.ingest import ingest_developer_activity

    mock_gql.side_effect = _make_gql_side_effects(pr_nodes=[], review_nodes=[])

    ingest_developer_activity("testuser", token="fake-token", days=14)

    pr_query_var     = mock_gql.call_args_list[1].args[2]["query"]
    review_query_var = mock_gql.call_args_list[2].args[2]["query"]

    import re
    pr_dates     = re.findall(r"\d{4}-\d{2}-\d{2}", pr_query_var)
    review_dates = re.findall(r"\d{4}-\d{2}-\d{2}", review_query_var)
    assert pr_dates and review_dates
    assert pr_dates[0] == review_dates[0]


# ---------------------------------------------------------------------------
# Test 8: caller token is passed to _gql
# ---------------------------------------------------------------------------

@patch("pullstar.ingest._gql")
def test_caller_token_is_used(mock_gql) -> None:
    """Test 8: the token argument is forwarded as-is to _gql."""
    from pullstar.ingest import ingest_developer_activity

    mock_gql.side_effect = _make_gql_side_effects(pr_nodes=[], review_nodes=[])

    ingest_developer_activity("testuser", token="my-secret-token", days=7)

    for c in mock_gql.call_args_list:
        assert c.args[0] == "my-secret-token"


# ---------------------------------------------------------------------------
# Test 9: library does not resolve .env credentials
# ---------------------------------------------------------------------------

def test_library_does_not_load_dotenv() -> None:
    """Test 9: pullstar.ingest contains no dotenv or credential-file loading calls."""
    import pullstar.ingest as ingest_mod
    src = inspect.getsource(ingest_mod)
    assert "load_dotenv" not in src
    assert "dotenv_values" not in src
    assert "from dotenv" not in src
    assert "import dotenv" not in src


# ---------------------------------------------------------------------------
# Test 10: no file writes
# ---------------------------------------------------------------------------

@patch("pullstar.ingest._gql")
def test_no_file_writes(mock_gql, tmp_path) -> None:
    """Test 10: ingest_developer_activity writes nothing to disk."""
    from pullstar.ingest import ingest_developer_activity

    mock_gql.side_effect = _make_gql_side_effects(pr_nodes=[], review_nodes=[])

    orig_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        ingest_developer_activity("testuser", token="fake-token", days=7)
    finally:
        os.chdir(orig_cwd)

    assert list(tmp_path.iterdir()) == [], f"unexpected files: {list(tmp_path.iterdir())}"


# ---------------------------------------------------------------------------
# Test 11: repositories=None uses broader query (no repo: qualifiers)
# ---------------------------------------------------------------------------

@patch("pullstar.ingest._gql")
def test_repositories_none_uses_broad_query(mock_gql) -> None:
    """Test 11: repositories=None → no repo: qualifiers in the query."""
    from pullstar.ingest import ingest_developer_activity

    mock_gql.side_effect = _make_gql_side_effects(pr_nodes=[], review_nodes=[])

    ingest_developer_activity("testuser", token="fake-token", days=7)

    pr_query = mock_gql.call_args_list[1].args[2]["query"]
    assert "repo:" not in pr_query


# ---------------------------------------------------------------------------
# Test 27: package installed without scripts/ still imports ingest
# ---------------------------------------------------------------------------

def test_ingest_importable_without_scripts() -> None:
    """Test 27: pullstar.ingest can be imported (not dependent on scripts/)."""
    import pullstar.ingest
    assert hasattr(pullstar.ingest, "ingest_developer_activity")


# ---------------------------------------------------------------------------
# Edge: ambiguous time window spec raises ValueError
# ---------------------------------------------------------------------------

def test_ambiguous_window_raises() -> None:
    """days + start_at/end_at together must raise ValueError."""
    from pullstar.ingest import ingest_developer_activity

    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    end   = datetime(2026, 6, 30, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="both"):
        ingest_developer_activity("u", token="t", start_at=start, end_at=end, days=30)


def test_only_start_at_raises() -> None:
    """Providing start_at without end_at must raise ValueError."""
    from pullstar.ingest import ingest_developer_activity

    with pytest.raises(ValueError):
        ingest_developer_activity(
            "u", token="t",
            start_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )


# ===========================================================================
# CORRECTION TESTS
# ===========================================================================

# ---------------------------------------------------------------------------
# C1: Multi-repo OR semantics — one search per repo, not one combined search
# ---------------------------------------------------------------------------

@patch("pullstar.ingest._gql")
def test_multi_repo_or_semantics_separate_calls(mock_gql) -> None:
    """Per-repo: A and B each get their own search call — OR, not AND.

    With AND semantics (one combined query repo:A repo:B), only one authored
    search call would be made, and the test's 5-call side-effect list would
    trigger a StopIteration on the 4th call — failing the assertion.
    With OR (per-repo), we see exactly 5 calls and both PRs in the result.
    """
    from pullstar.ingest import ingest_developer_activity

    mock_gql.side_effect = [
        {"user": {"name": "Dev"}},
        _search_response([_pr_node(1, "owner/repo-a")]),   # authored repo-a
        _search_response([_pr_node(2, "owner/repo-b")]),   # authored repo-b
        _search_response([]),                               # reviews repo-a
        _search_response([]),                               # reviews repo-b
    ]

    result = ingest_developer_activity(
        "testuser",
        token="fake-token",
        start_at=_WIN_START,
        end_at=_WIN_END,
        repositories=["owner/repo-a", "owner/repo-b"],
    )

    pr_repos = {p["repository"] for p in result["prs_authored"]}
    assert "owner/repo-a" in pr_repos, "repo-a PR missing (AND semantics would lose both)"
    assert "owner/repo-b" in pr_repos, "repo-b PR missing (AND semantics would lose both)"
    assert len(result["prs_authored"]) == 2
    assert mock_gql.call_count == 5   # user + 2 authored + 2 reviews


@patch("pullstar.ingest._gql")
def test_multi_repo_dedup_no_phantom_prs(mock_gql) -> None:
    """Duplicate PR appearing in two per-repo searches appears only once."""
    from pullstar.ingest import ingest_developer_activity

    dup_pr = _pr_node(99, "owner/repo-a")
    mock_gql.side_effect = [
        {"user": {"name": "Dev"}},
        _search_response([dup_pr]),   # repo-a authored: PR#99
        _search_response([dup_pr]),   # repo-b authored: same PR#99 (index quirk)
        _search_response([]),
        _search_response([]),
    ]

    result = ingest_developer_activity(
        "testuser",
        token="fake-token",
        start_at=_WIN_START,
        end_at=_WIN_END,
        repositories=["owner/repo-a", "owner/repo-b"],
    )

    assert len(result["prs_authored"]) == 1
    assert result["prs_authored"][0]["number"] == 99


@patch("pullstar.ingest._gql")
def test_multi_repo_query_contains_repo_qualifier(mock_gql) -> None:
    """Each per-repo authored search query contains exactly one repo: qualifier."""
    from pullstar.ingest import ingest_developer_activity

    mock_gql.side_effect = [
        {"user": {"name": "Dev"}},
        _search_response([]),
        _search_response([]),
        _search_response([]),
        _search_response([]),
    ]

    ingest_developer_activity(
        "testuser",
        token="fake-token",
        days=30,
        repositories=["owner/repo-a", "owner/repo-b"],
    )

    call1_q = mock_gql.call_args_list[1].args[2]["query"]
    call2_q = mock_gql.call_args_list[2].args[2]["query"]
    assert "repo:owner/repo-a" in call1_q
    assert "repo:owner/repo-b" in call2_q
    assert call1_q.count("repo:") == 1
    assert call2_q.count("repo:") == 1


# ---------------------------------------------------------------------------
# C2: Exact window boundaries — post-filter for authored PRs and reviews
# ---------------------------------------------------------------------------

@patch("pullstar.ingest._gql")
def test_exact_boundary_post_filter_authored(mock_gql) -> None:
    """PRs at start-1s and end+1s are excluded; exactly at start and end are included."""
    from pullstar.ingest import ingest_developer_activity

    start = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    end   = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)
    one_s = timedelta(seconds=1)

    def _ts(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    mock_gql.side_effect = [
        {"user": {"name": "Dev"}},
        _search_response([
            _pr_node(1, "owner/repo", created_at=_ts(start - one_s)),   # before → excluded
            _pr_node(2, "owner/repo", created_at=_ts(start)),            # exactly start → included
            _pr_node(3, "owner/repo", created_at=_ts(end)),              # exactly end → included
            _pr_node(4, "owner/repo", created_at=_ts(end + one_s)),      # after → excluded
        ]),
        _search_response([]),
    ]

    result = ingest_developer_activity(
        "testuser", token="fake-token", start_at=start, end_at=end,
    )

    pr_nums = {p["number"] for p in result["prs_authored"]}
    assert 1 not in pr_nums, "PR created 1s before start must be excluded"
    assert 2 in pr_nums,     "PR created exactly at start must be included"
    assert 3 in pr_nums,     "PR created exactly at end must be included"
    assert 4 not in pr_nums, "PR created 1s after end must be excluded"


@patch("pullstar.ingest._gql")
def test_exact_boundary_post_filter_reviews(mock_gql) -> None:
    """Reviews at start-1s and end+1s are excluded; exactly at start and end are included."""
    from pullstar.ingest import ingest_developer_activity

    start = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    end   = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)
    one_s = timedelta(seconds=1)

    def _ts(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    def _rev(pr_num: int, ts: str) -> dict:
        return {
            "number":     pr_num,
            "title":      f"PR #{pr_num}",
            "repository": {"nameWithOwner": "owner/repo"},
            "reviews": {"nodes": [{
                "state":       "APPROVED",
                "body":        "ok",
                "author":      {"login": "testuser"},
                "submittedAt": ts,
            }]},
        }

    mock_gql.side_effect = [
        {"user": {"name": "Dev"}},
        _search_response([]),
        _search_response([
            _rev(101, _ts(start - one_s)),   # before → excluded
            _rev(102, _ts(start)),            # exactly start → included
            _rev(103, _ts(end)),              # exactly end → included
            _rev(104, _ts(end + one_s)),      # after → excluded
        ]),
    ]

    result = ingest_developer_activity(
        "testuser", token="fake-token", start_at=start, end_at=end,
    )

    review_pr_nums = {r["pr_number"] for r in result["reviews_given"]}
    assert 101 not in review_pr_nums, "review 1s before start must be excluded"
    assert 102 in review_pr_nums,     "review exactly at start must be included"
    assert 103 in review_pr_nums,     "review exactly at end must be included"
    assert 104 not in review_pr_nums, "review 1s after end must be excluded"


@patch("pullstar.ingest._gql")
def test_days_mode_uses_exact_window_in_query(mock_gql) -> None:
    """days=N derives a full ISO-8601 datetime range for query qualifiers."""
    from pullstar.ingest import ingest_developer_activity

    mock_gql.side_effect = _make_gql_side_effects(pr_nodes=[], review_nodes=[])

    ingest_developer_activity("testuser", token="fake-token", days=30)

    pr_query = mock_gql.call_args_list[1].args[2]["query"]
    assert "T" in pr_query, f"Expected ISO datetime in query, got: {pr_query}"
    assert ".." in pr_query, f"Expected range syntax in query, got: {pr_query}"


# ---------------------------------------------------------------------------
# C2b: Review-window correction — no upper bound on updated: qualifier
# ---------------------------------------------------------------------------

@patch("pullstar.ingest._gql")
def test_review_included_when_pr_updated_after_end(mock_gql) -> None:
    """Regression: review submitted Aug 3 is included even though PR was updated Aug 9.

    Window: Aug 1..Aug 7.  Review submitted Aug 3 (inside).  PR last updated Aug 9 (outside).

    With the old updated:START..END query the PR would be absent from the candidate
    set because updated:Aug9 > Aug7.  With updated:>=START there is no upper bound
    on the PR's update time, so the PR is a candidate and the Aug 3 review survives
    the submitted_at post-filter.
    """
    from pullstar.ingest import ingest_developer_activity

    window_start = datetime(2026, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
    window_end   = datetime(2026, 8, 7, 0, 0, 0, tzinfo=timezone.utc)

    # The PR was updated Aug 9 — AFTER the window end.
    # With an upper-bounded query (updated:..Aug7) this PR would not appear.
    # With updated:>=Aug1 it does appear, and the Aug 3 review passes submitted_at filter.
    review_ts = "2026-08-03T10:00:00Z"   # inside window

    mock_gql.side_effect = [
        {"user": {"name": "Dev"}},
        _search_response([]),   # authored PRs
        _search_response([{
            "number":     1,
            "title":      "PR that was updated after window closed",
            "repository": {"nameWithOwner": "owner/repo"},
            "reviews": {"nodes": [{
                "state":       "APPROVED",
                "body":        "LGTM",
                "author":      {"login": "testuser"},
                "submittedAt": review_ts,
            }]},
        }]),
    ]

    result = ingest_developer_activity(
        "testuser", token="fake-token",
        start_at=window_start, end_at=window_end,
    )

    assert len(result["reviews_given"]) == 1, (
        "Review submitted Aug 3 must appear even though PR was updated Aug 9"
    )
    assert result["reviews_given"][0]["pr_number"] == 1


@patch("pullstar.ingest._gql")
def test_review_query_uses_open_upper_bound(mock_gql) -> None:
    """Reviewed-PR candidate query must use updated:>=START, not updated:START..END."""
    import re
    from pullstar.ingest import ingest_developer_activity

    mock_gql.side_effect = _make_gql_side_effects(pr_nodes=[], review_nodes=[])

    start = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    end   = datetime(2026, 6, 30, 0, 0, 0, tzinfo=timezone.utc)

    ingest_developer_activity("testuser", token="fake-token", start_at=start, end_at=end)

    # call 0=user, 1=authored PRs, 2=reviews
    review_query = mock_gql.call_args_list[2].args[2]["query"]

    # Must use updated:>= (open upper bound)
    assert "updated:>=" in review_query, (
        f"Review query must use updated:>=START, got: {review_query!r}"
    )
    # Must NOT contain an upper bound on the updated qualifier (no .. in that clause)
    match = re.search(r"updated:\S+", review_query)
    assert match is not None
    updated_clause = match.group(0)
    assert ".." not in updated_clause, (
        f"Review query must not range-bound updated:, got clause: {updated_clause!r}"
    )
    # The end timestamp must not appear anywhere in the review query
    # (confirmed absence of upper bound — a false positive would be impossible
    #  unless the end date appeared as org/repo name)
    end_date_str = end.strftime("%Y-%m-%d")
    assert end_date_str not in review_query, (
        f"End date {end_date_str!r} must not appear in review query: {review_query!r}"
    )


# ---------------------------------------------------------------------------
# C3: socket.setdefaulttimeout is NOT called
# ---------------------------------------------------------------------------

@patch("pullstar.ingest._gql")
def test_socket_default_timeout_unchanged(mock_gql) -> None:
    """ingest_developer_activity must not mutate socket.getdefaulttimeout()."""
    from pullstar.ingest import ingest_developer_activity

    mock_gql.side_effect = _make_gql_side_effects(pr_nodes=[], review_nodes=[])

    before = socket.getdefaulttimeout()
    ingest_developer_activity("testuser", token="fake-token", days=7)
    after = socket.getdefaulttimeout()

    assert after == before, (
        f"socket default timeout changed: was {before!r}, now {after!r}"
    )


def test_socket_not_imported_for_timeout() -> None:
    """pullstar.ingest does not import socket (no timeout mutation possible)."""
    import pullstar.ingest as ingest_mod
    src = inspect.getsource(ingest_mod)
    # The module must not import socket at all — absence of the import is the
    # guarantee that setdefaulttimeout() cannot be called at runtime.
    assert "import socket" not in src


# ---------------------------------------------------------------------------
# C4: Public exception types
# ---------------------------------------------------------------------------

def test_public_exception_types_exist() -> None:
    """PullStarIngestError and PullStarRateLimitError are importable."""
    from pullstar.ingest import PullStarIngestError, PullStarRateLimitError
    assert issubclass(PullStarRateLimitError, PullStarIngestError)
    assert issubclass(PullStarIngestError, Exception)


@patch("pullstar.ingest._gql")
def test_rate_limit_raises_pullstar_rate_limit_error(mock_gql) -> None:
    """GitHub rate limit during PR search → PullStarRateLimitError."""
    from pullstar.ingest import PullStarRateLimitError, ingest_developer_activity

    mock_gql.side_effect = [
        {"user": {"name": "Dev"}},
        PullStarRateLimitError("rate limited"),
    ]

    with pytest.raises(PullStarRateLimitError):
        ingest_developer_activity("u", token="fake-token", days=7)


@patch("pullstar.ingest._gql")
def test_api_error_raises_pullstar_ingest_error(mock_gql) -> None:
    """Non-rate-limit API error during PR search → PullStarIngestError."""
    from pullstar.ingest import PullStarIngestError, ingest_developer_activity

    mock_gql.side_effect = [
        {"user": {"name": "Dev"}},
        PullStarIngestError("api error"),
    ]

    with pytest.raises(PullStarIngestError):
        ingest_developer_activity("u", token="fake-token", days=7)


def test_rate_limit_is_catchable_as_ingest_error() -> None:
    """PullStarRateLimitError can be caught as PullStarIngestError."""
    from pullstar.ingest import PullStarIngestError, PullStarRateLimitError

    exc = PullStarRateLimitError("limited")
    assert isinstance(exc, PullStarIngestError)


def test_exception_message_does_not_contain_token() -> None:
    """_gql raises PullStarIngestError with no token in the message."""
    from unittest.mock import patch as _patch
    from pullstar.ingest import PullStarIngestError, _gql

    class _FakeResp:
        status_code = 401
        ok          = False

    with _patch("requests.post", return_value=_FakeResp()):
        try:
            _gql("my-super-secret-token", "query{}", {})
        except PullStarIngestError as exc:
            assert "my-super-secret-token" not in str(exc)
        else:
            pytest.fail("Expected PullStarIngestError")
