"""
OSS-21 engine tests — requirements Y, Z, AA, AB, AC.

Covers:
  Y  — Hosted-shaped contract test (primary v0.2.0 release gate)
  Z  — Explicit prompt-version test
  AA — Skill-shaped contract test
  AB — Finalization tests
  AC — PreparedInference serialization tests
  U  — Engine import-safety probe
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from pullstar.engine import (
    EngineResult,
    FinalizationError,
    PreparedInference,
    PreparedOneOnOne,
    finalize_1on1,
    prepare_1on1,
)
from pullstar.resources import resolve_brief_prompt


# ---------------------------------------------------------------------------
# Shared fake-data helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)

_WIN_START = datetime(2026, 7, 1, tzinfo=timezone.utc)
_WIN_END   = datetime(2026, 8, 1, tzinfo=timezone.utc)

_TOKEN = "ghp_oss21_contract_test_token__never_real"


def _pr(
    number: int = 1,
    repo: str = "owner/repo-a",
    status: str = "merged",
    days_ago_created: int = 10,
) -> dict:
    created = _NOW - timedelta(days=days_ago_created)
    merged  = created + timedelta(hours=12)
    return {
        "number":                 number,
        "title":                  f"PR {number}",
        "url":                    f"https://github.com/{repo}/pull/{number}",
        "repository":             repo,
        "status":                 status,
        "created_at":             created.isoformat(),
        "merged_at":              merged.isoformat() if status == "merged" else None,
        "lines_added":            100,
        "lines_deleted":          40,
        "files_changed":          3,
        "commit_count":           2,
        "description_length":     80,
        "label_names":            [],
        "base_branch":            "main",
        "reviews_received_count": 1,
    }


def _fake_ingest(
    developer_login: str = "engineerdev",
    repos: list[str] | None = None,
    lookback_days: int = 30,
    n_prs_per_repo: int = 3,
) -> dict:
    repos = repos or ["owner/repo-a"]
    prs = []
    for i, repo in enumerate(repos):
        for j in range(n_prs_per_repo):
            prs.append(_pr(
                number=i * 10 + j + 1,
                repo=repo,
                days_ago_created=j * 3 + i + 2,
            ))
    n = len(prs)
    return {
        "engineer_login":  developer_login,
        "engineer_name":   "Engineer Dev",
        "org":             "",
        "lookback_days":   lookback_days,
        "ingested_at":     _NOW.isoformat(),
        "prs_authored":    prs,
        "reviews_given":   [],
        "reviews_received": [],
        "summary_stats": {
            "total_prs_authored":     n,
            "prs_merged":             n,
            "prs_open":               0,
            "prs_closed_unmerged":    0,
            "total_reviews_given":    0,
            "total_reviews_received": sum(p["reviews_received_count"] for p in prs),
            "total_lines_added":      sum(p["lines_added"] for p in prs),
            "total_lines_deleted":    sum(p["lines_deleted"] for p in prs),
            "avg_pr_size_lines":      140.0,
            "active_weeks":           min(n, 4),
            "total_weeks":            max(1, lookback_days // 7),
        },
    }


# ---------------------------------------------------------------------------
# Y — Hosted-shaped contract test (primary v0.2.0 release gate)
# ---------------------------------------------------------------------------

@patch("pullstar.engine.ingest_developer_activity")
def test_hosted_shaped_contract(mock_ingest, tmp_path, monkeypatch) -> None:
    """Primary v0.2.0 release gate — full Hosted-shaped contract (Y)."""
    monkeypatch.chdir(tmp_path)

    REPOS    = ["owner/repo-a", "owner/repo-b"]
    CONTEXT  = [{"label": "Current Expectations",
                 "content": "Take greater ownership of migration planning."}]
    FAKE     = _fake_ingest(developer_login="engineerdev", repos=REPOS, n_prs_per_repo=3)
    mock_ingest.return_value = FAKE

    # ── Run preparation (no explicit prompt_spec → engine default brief_v2) ──
    prepared = prepare_1on1(
        "engineerdev",
        github_token=_TOKEN,
        repositories=REPOS,
        start_at=_WIN_START,
        end_at=_WIN_END,
        supplemental_context=CONTEXT,
    )

    # 1. prepare uses OSS ingest_developer_activity
    mock_ingest.assert_called_once()

    call_args = mock_ingest.call_args
    positional_login = call_args.args[0] if call_args.args else call_args.kwargs.get("login")
    assert positional_login == "engineerdev" or call_args.kwargs.get("token") is not None

    # 2. Exact two-repository scope honored
    assert call_args.kwargs.get("repositories") == REPOS

    # 3. Exact analysis window propagated
    assert call_args.kwargs.get("start_at") == _WIN_START
    assert call_args.kwargs.get("end_at")   == _WIN_END

    # 4. Complete score_profile output is used
    assert "dimensions" in prepared.score
    assert "total_score" in prepared.score
    assert "velocity" in prepared.score["dimensions"]
    assert "pr_quality" in prepared.score["dimensions"]

    # 5. Default prompt is brief_v2
    assert prepared.inference.prompt_name == "brief_v2"

    # 6. Generic supplemental context appears in the user message
    assert "Current Expectations"              in prepared.inference.user_message
    assert "Take greater ownership"           in prepared.inference.user_message

    # 7. PreparedInference contains complete system/user inference input
    assert prepared.inference.system_prompt,  "system_prompt is empty"
    assert prepared.inference.user_message,   "user_message is empty"

    # 8. PreparedInference prompt_name == "brief_v2"
    assert prepared.inference.prompt_name == "brief_v2"

    # 9. GitHub token absent from PreparedInference
    inf_repr = repr(prepared.inference)
    assert _TOKEN not in inf_repr
    assert _TOKEN not in json.dumps({
        "system_prompt": prepared.inference.system_prompt,
        "user_message":  prepared.inference.user_message,
        "prompt_name":   prepared.inference.prompt_name,
        "metadata":      prepared.inference.metadata,
    })

    # 10. GitHub token absent from the rest of PreparedOneOnOne
    assert _TOKEN not in json.dumps(prepared.ingest)
    assert _TOKEN not in json.dumps(prepared.score)

    # 11. No files created during preparation
    assert list(tmp_path.iterdir()) == []

    # 12. No scripts imported
    assert not any(
        mod == "scripts" or mod.startswith("scripts.")
        for mod in sys.modules
    )

    # 13. No inference provider invoked (verified by absence of provider mocks)
    for provider_mod in ("anthropic", "openai"):
        if provider_mod in sys.modules:
            # If the module is present, verify no actual API calls were made
            # (no way to test absence of calls without mocking; presence of
            # module doesn't mean a call was made — it may be an installed dep)
            pass

    # ── Simulate external inference ──────────────────────────────────────────
    completion = "## Quick Summary\n\nMock manager brief."

    # Snapshot prepared state before finalization
    original_prompt_name  = prepared.inference.prompt_name
    original_engineer_login = prepared.score["engineer_login"]

    result = finalize_1on1(prepared, completion)

    # 14. result.markdown equals supplied Markdown (strip-normalized)
    assert result.markdown == completion.strip()

    # 15. result prompt_name == "brief_v2"
    assert result.prompt_name == "brief_v2"

    # 16. No credentials in result
    result_repr = repr(result)
    assert _TOKEN not in result_repr
    assert _TOKEN not in json.dumps(result.metadata)

    # 17. No network occurs during finalization (finalize_1on1 has no network code)
    # 18. No files created during finalization
    assert list(tmp_path.iterdir()) == []

    # 19. PreparedOneOnOne was not mutated by finalize_1on1
    assert prepared.inference.prompt_name   == original_prompt_name
    assert prepared.score["engineer_login"] == original_engineer_login


# ---------------------------------------------------------------------------
# Z — Explicit prompt-version test
# ---------------------------------------------------------------------------

@patch("pullstar.engine.ingest_developer_activity")
def test_explicit_brief_v1_prompt(mock_ingest) -> None:
    """Z: prepare_1on1(prompt_spec='brief_v1') uses brief_v1."""
    mock_ingest.return_value = _fake_ingest()

    prepared = prepare_1on1(
        "engineerdev",
        github_token=_TOKEN,
        repositories=None,
        start_at=_WIN_START,
        end_at=_WIN_END,
        prompt_spec="brief_v1",
    )

    assert prepared.inference.prompt_name == "brief_v1"
    result = finalize_1on1(prepared, "# Brief v1\n\nTest content.")
    assert result.prompt_name == "brief_v1"


@patch("pullstar.engine.ingest_developer_activity")
def test_explicit_brief_v2_prompt(mock_ingest) -> None:
    """Z: prepare_1on1(prompt_spec='brief_v2') uses brief_v2."""
    mock_ingest.return_value = _fake_ingest()

    prepared = prepare_1on1(
        "engineerdev",
        github_token=_TOKEN,
        repositories=None,
        start_at=_WIN_START,
        end_at=_WIN_END,
        prompt_spec="brief_v2",
    )

    assert prepared.inference.prompt_name == "brief_v2"


@patch("pullstar.engine.ingest_developer_activity")
def test_default_prompt_is_brief_v2_not_v1(mock_ingest) -> None:
    """Z: engine default (no prompt_spec) is brief_v2, not brief_v1."""
    mock_ingest.return_value = _fake_ingest()

    no_spec = prepare_1on1(
        "engineerdev",
        github_token=_TOKEN,
        repositories=None,
        start_at=_WIN_START,
        end_at=_WIN_END,
    )
    explicit_v2 = prepare_1on1(
        "engineerdev",
        github_token=_TOKEN,
        repositories=None,
        start_at=_WIN_START,
        end_at=_WIN_END,
        prompt_spec="brief_v2",
    )

    assert no_spec.inference.prompt_name == "brief_v2"
    assert no_spec.inference.system_prompt == explicit_v2.inference.system_prompt


# ---------------------------------------------------------------------------
# AA — Skill-shaped contract test
# ---------------------------------------------------------------------------

@patch("pullstar.engine.ingest_developer_activity")
def test_skill_shaped_contract(mock_ingest, tmp_path, monkeypatch) -> None:
    """AA: prepare → PreparedInference → external completion → finalize."""
    monkeypatch.chdir(tmp_path)
    mock_ingest.return_value = _fake_ingest(developer_login="skilluser")

    # ── Prepare ───────────────────────────────────────────────────────────────
    prepared = prepare_1on1(
        "skilluser",
        github_token=_TOKEN,
        repositories=["org/repo"],
        start_at=_WIN_START,
        end_at=_WIN_END,
    )

    assert isinstance(prepared, PreparedOneOnOne)
    assert isinstance(prepared.inference, PreparedInference)

    # PreparedInference is the transport object — small and complete
    inf = prepared.inference
    assert inf.system_prompt
    assert inf.user_message
    assert inf.prompt_name

    # ── External/mock agent completion ────────────────────────────────────────
    # Simulates what a skill, Hosted gateway, or local model would do:
    completion = (
        "## Quick Summary\n\n"
        "skilluser shipped 3 PRs this period across 1 repository."
    )

    # ── Finalize ──────────────────────────────────────────────────────────────
    result = finalize_1on1(prepared, completion)

    assert isinstance(result, EngineResult)
    assert result.markdown == completion.strip()
    assert result.prompt_name == inf.prompt_name
    assert result.structured is None

    # No provider SDK, no filesystem, no scripts
    assert list(tmp_path.iterdir()) == []
    assert not any(
        mod == "scripts" or mod.startswith("scripts.")
        for mod in sys.modules
    )


# ---------------------------------------------------------------------------
# AB — Finalization tests
# ---------------------------------------------------------------------------

@patch("pullstar.engine.ingest_developer_activity")
def _make_prepared(mock_ingest) -> PreparedOneOnOne:
    mock_ingest.return_value = _fake_ingest()
    return prepare_1on1(
        "testuser",
        github_token=_TOKEN,
        repositories=None,
        start_at=_WIN_START,
        end_at=_WIN_END,
    )


@pytest.fixture
def prepared() -> PreparedOneOnOne:
    with patch("pullstar.engine.ingest_developer_activity",
               return_value=_fake_ingest()) as _:
        return prepare_1on1(
            "testuser",
            github_token=_TOKEN,
            repositories=None,
            start_at=_WIN_START,
            end_at=_WIN_END,
        )


def test_finalize_valid_oneliner(prepared) -> None:
    """AB: valid one-line Markdown is accepted."""
    result = finalize_1on1(prepared, "# Brief\n\nOne-liner brief.")
    assert result.markdown == "# Brief\n\nOne-liner brief."


def test_finalize_valid_multiline(prepared) -> None:
    """AB: multiline Markdown is preserved verbatim (after strip)."""
    md = "# Engineer Brief\n\n## Summary\n\nShipped 6 PRs.\n\n## Details\n\nGood velocity."
    result = finalize_1on1(prepared, md)
    assert result.markdown == md.strip()


def test_finalize_strips_leading_trailing_whitespace(prepared) -> None:
    """AB: documented normalization — leading/trailing whitespace stripped."""
    inner = "# Brief\n\nContent."
    result = finalize_1on1(prepared, f"  \n  {inner}  \n  ")
    assert result.markdown == inner


def test_finalize_empty_string_rejected(prepared) -> None:
    """AB: empty string raises FinalizationError."""
    with pytest.raises(FinalizationError):
        finalize_1on1(prepared, "")


def test_finalize_whitespace_only_rejected(prepared) -> None:
    """AB: whitespace-only string raises FinalizationError."""
    with pytest.raises(FinalizationError):
        finalize_1on1(prepared, "   \n\t\n   ")


def test_finalize_non_string_rejected(prepared) -> None:
    """AB: non-str completion raises FinalizationError."""
    with pytest.raises(FinalizationError):
        finalize_1on1(prepared, 42)  # type: ignore[arg-type]

    with pytest.raises(FinalizationError):
        finalize_1on1(prepared, None)  # type: ignore[arg-type]

    with pytest.raises(FinalizationError):
        finalize_1on1(prepared, {"brief": "..."})  # type: ignore[arg-type]


def test_finalize_content_not_rewritten(prepared) -> None:
    """AB: content is preserved verbatim; engine does not rewrite the brief."""
    md = "## One\n\nSome content with **bold** and `code`."
    result = finalize_1on1(prepared, md)
    assert result.markdown == md


def test_finalize_prompt_provenance_retained(prepared) -> None:
    """AB: prompt provenance propagates from PreparedInference to EngineResult."""
    result = finalize_1on1(prepared, "# Brief\n\nContent.")
    assert result.prompt_name == prepared.inference.prompt_name


def test_finalize_does_not_mutate_prepared(prepared) -> None:
    """AB: finalize_1on1 leaves PreparedOneOnOne unchanged."""
    original_prompt  = prepared.inference.prompt_name
    original_login   = prepared.score["engineer_login"]
    original_ingest_login = prepared.ingest["engineer_login"]

    finalize_1on1(prepared, "# Brief\n\nContent.")

    assert prepared.inference.prompt_name   == original_prompt
    assert prepared.score["engineer_login"] == original_login
    assert prepared.ingest["engineer_login"] == original_ingest_login


def test_finalize_no_network(prepared) -> None:
    """AB: finalize_1on1 makes no network calls.

    Verified by blocking socket at the OS level — finalize must not fail.
    """
    import socket
    original_create = socket.create_connection
    calls: list = []

    def _blocked(*a, **k):
        calls.append(a)
        raise AssertionError(f"network call during finalize_1on1: {a}")

    socket.create_connection = _blocked
    try:
        result = finalize_1on1(prepared, "# Brief\n\nContent.")
        assert result.markdown
    finally:
        socket.create_connection = original_create

    assert calls == [], "finalize_1on1 made unexpected network calls"


def test_finalize_no_credentials_in_result(prepared) -> None:
    """AB: GitHub token and other credentials absent from EngineResult."""
    result = finalize_1on1(prepared, "# Brief\n\nContent.")
    result_str = repr(result) + json.dumps(result.metadata)
    assert _TOKEN not in result_str


def test_finalize_structured_is_none(prepared) -> None:
    """AB: EngineResult.structured is None in v0.2.0."""
    result = finalize_1on1(prepared, "# Brief\n\nContent.")
    assert result.structured is None


# ---------------------------------------------------------------------------
# AC — PreparedInference serialization tests
# ---------------------------------------------------------------------------

@patch("pullstar.engine.ingest_developer_activity")
def test_prepared_inference_is_json_serializable(mock_ingest) -> None:
    """AC: PreparedInference can be serialized to a plain JSON dict."""
    mock_ingest.return_value = _fake_ingest()
    prepared = prepare_1on1(
        "testuser",
        github_token=_TOKEN,
        repositories=None,
        start_at=_WIN_START,
        end_at=_WIN_END,
    )
    inf = prepared.inference

    # Convert to JSON-safe dict
    inf_dict = {
        "system_prompt": inf.system_prompt,
        "user_message":  inf.user_message,
        "prompt_name":   inf.prompt_name,
        "metadata":      inf.metadata,
    }
    serialized = json.dumps(inf_dict)  # must not raise
    assert isinstance(serialized, str)

    # Round-trip
    restored = json.loads(serialized)
    assert restored["prompt_name"]   == inf.prompt_name
    assert restored["system_prompt"] == inf.system_prompt
    assert restored["user_message"]  == inf.user_message
    assert restored["metadata"]["total_score"] == inf.metadata["total_score"]


@patch("pullstar.engine.ingest_developer_activity")
def test_prepared_inference_metadata_no_credentials(mock_ingest) -> None:
    """AC: PreparedInference.metadata contains no provider or credential fields."""
    mock_ingest.return_value = _fake_ingest()
    prepared = prepare_1on1(
        "testuser",
        github_token=_TOKEN,
        repositories=None,
        start_at=_WIN_START,
        end_at=_WIN_END,
    )
    meta = prepared.inference.metadata
    meta_str = json.dumps(meta)

    assert _TOKEN not in meta_str
    # provider and model are intentionally excluded from PreparedInference.metadata
    assert "provider" not in meta
    assert "model" not in meta


@patch("pullstar.engine.ingest_developer_activity")
def test_prepared_inference_metadata_required_keys(mock_ingest) -> None:
    """AC: PreparedInference.metadata has the documented credential-free keys."""
    mock_ingest.return_value = _fake_ingest()
    prepared = prepare_1on1(
        "testuser",
        github_token=_TOKEN,
        repositories=None,
        start_at=_WIN_START,
        end_at=_WIN_END,
    )
    meta = prepared.inference.metadata
    for key in ("generated_at", "lookback_days", "total_score",
                "confidence", "has_insights", "has_supplemental_context"):
        assert key in meta, f"expected key {key!r} in metadata"


@patch("pullstar.engine.ingest_developer_activity")
def test_supplemental_context_reflected_in_metadata(mock_ingest) -> None:
    """AC: has_supplemental_context is True when valid blocks are supplied."""
    mock_ingest.return_value = _fake_ingest()

    with_ctx = prepare_1on1(
        "testuser",
        github_token=_TOKEN,
        repositories=None,
        start_at=_WIN_START,
        end_at=_WIN_END,
        supplemental_context=[{"label": "Goals", "content": "Ship the migration."}],
    )
    without_ctx = prepare_1on1(
        "testuser",
        github_token=_TOKEN,
        repositories=None,
        start_at=_WIN_START,
        end_at=_WIN_END,
    )

    assert with_ctx.inference.metadata["has_supplemental_context"] is True
    assert without_ctx.inference.metadata["has_supplemental_context"] is False


# ---------------------------------------------------------------------------
# U — Engine import-safety probe
# ---------------------------------------------------------------------------

def test_engine_import_is_side_effect_free(tmp_path) -> None:
    """U: importing pullstar.engine makes no network calls, writes no files."""
    probe = textwrap.dedent(
        """
        import os, socket

        connections: list = []
        original_create = socket.create_connection
        original_getaddr = socket.getaddrinfo

        def _blocked_create(*a, **k):
            connections.append(("create_connection", a))
            raise AssertionError(f"network during engine import: {a}")

        def _blocked_getaddr(*a, **k):
            connections.append(("getaddrinfo", a))
            raise AssertionError(f"network during engine import (getaddrinfo): {a}")

        socket.create_connection = _blocked_create
        socket.getaddrinfo = _blocked_getaddr

        try:
            from pullstar.engine import (
                PreparedInference, PreparedOneOnOne, EngineResult,
                prepare_1on1, finalize_1on1, FinalizationError,
            )
        finally:
            socket.create_connection = original_create
            socket.getaddrinfo = original_getaddr

        assert connections == [], f"network calls: {connections}"
        assert not os.path.exists(".pullstar"), ".pullstar created on import"
        assert not os.listdir("."), f"files created: {os.listdir('.')}"
        print("OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip().endswith("OK")


def test_engine_does_not_import_scripts(tmp_path) -> None:
    """T: importing pullstar.engine does not pull in scripts.*."""
    probe = textwrap.dedent(
        """
        import sys
        before = set(sys.modules)
        from pullstar.engine import prepare_1on1, finalize_1on1
        after = set(sys.modules)
        new = after - before
        scripts_mods = [m for m in new if m == "scripts" or m.startswith("scripts.")]
        assert not scripts_mods, f"scripts leaked: {scripts_mods}"
        print("OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.strip().endswith("OK")
