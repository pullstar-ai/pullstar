"""
pullstar.engine — Inference-neutral engine API for PullStar 1-on-1

Public API
----------
    prepare_1on1(...)  → PreparedOneOnOne
    finalize_1on1(...) → EngineResult

    PreparedInference  — provider-neutral inference handoff
    PreparedOneOnOne   — deterministic analysis state + inference payload
    EngineResult       — finalized engine result
    FinalizationError  — raised when the completion is invalid

Design contract
---------------
    - Caller completely owns inference: provider, model, credentials, retries.
    - The engine prepares deterministic input and validates the final result.
    - GitHub token is used only inside prepare_1on1 for ingestion; it is never
      stored in any returned object.
    - No filesystem artifacts are required or written.
    - No provider SDKs are imported.
    - Import-safe: no network access, no credential reads, no file I/O, no
      argv parsing at import time.

Fundamental flow
----------------
    prepare_1on1(...)
          ↓
    PreparedOneOnOne
        └── PreparedInference     ← pass to external inference
                ↓
        ───── inference boundary ─────
                ↓
          external model / agent / gateway
                ↓
             completion  (str)
                ↓
    finalize_1on1(prepared, completion)
          ↓
    EngineResult
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import NamedTuple

from pullstar.ingest import ingest_developer_activity
from pullstar.prompting import build_llm_input_payload
from pullstar.resources import resolve_brief_prompt
from pullstar.scoring import score_profile


# ---------------------------------------------------------------------------
# Public exception
# ---------------------------------------------------------------------------


class FinalizationError(ValueError):
    """Raised when the inference completion cannot be accepted.

    Conditions: completion is not a str, or is empty / whitespace-only.
    """


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class PreparedInference(NamedTuple):
    """Provider-neutral inference handoff.

    Contains everything an external inference caller needs to run the PullStar
    model request without reconstructing PullStar prompt behavior.  No
    credentials are included.

    Fields
    ------
    system_prompt : Complete PullStar system prompt text.
    user_message  : Complete, engineer-specific user-turn text.
    prompt_name   : Prompt identity for provenance (e.g. ``"brief_v2"``).
    metadata      : Credential-free, provider-neutral inference metadata.
                    Keys: generated_at, lookback_days, total_score, confidence,
                    has_insights, has_supplemental_context.
    """

    system_prompt: str
    user_message: str
    prompt_name: str
    metadata: dict


class PreparedOneOnOne(NamedTuple):
    """Deterministic PullStar analysis state plus the inference payload.

    ``inference`` is the small, transport-friendly object passed to external
    inference.  ``ingest`` and ``score`` retain the full deterministic analysis
    state for application-layer persistence, audit, or display.

    No GitHub credentials appear in any field.

    Fields
    ------
    inference : Provider-neutral inference handoff.
    ingest    : Full IngestedProfile dict (no credentials).
    score     : Full ScoredProfile dict.
    """

    inference: PreparedInference
    ingest: dict
    score: dict


class EngineResult(NamedTuple):
    """Finalized PullStar engine result.

    Fields
    ------
    markdown    : Inference completion as Markdown, strip()-normalized.
    prompt_name : Prompt identity carried from the inference payload.
    structured  : Always ``None`` in v0.2.0; reserved for future structured output.
    metadata    : Credential-free result metadata (finalized_at, engineer_login,
                  total_score, confidence).
    """

    markdown: str
    prompt_name: str
    structured: dict | None
    metadata: dict


# ---------------------------------------------------------------------------
# Engine-level default prompt
# ---------------------------------------------------------------------------

#: v0.2 engine default prompt.  Distinct from the lower-level resource default
#: (``brief_v1``) so the engine behavior can advance independently.
_ENGINE_DEFAULT_PROMPT = "brief_v2"


# ---------------------------------------------------------------------------
# prepare_1on1
# ---------------------------------------------------------------------------


def prepare_1on1(
    developer_login: str,
    *,
    github_token: str,
    repositories: list[str] | None = None,
    start_at: datetime,
    end_at: datetime,
    supplemental_context: list[dict] | None = None,
    prompt_spec: str | None = None,
) -> PreparedOneOnOne:
    """Prepare a deterministic PullStar 1-on-1 analysis for external inference.

    Runs the complete deterministic sequence (ingest → score → prompt selection
    → payload assembly) and returns a :class:`PreparedOneOnOne` ready for an
    external inference call.

    The GitHub token is used only during the ingest step and is never retained
    in the returned object or any nested structure.

    Parameters
    ----------
    developer_login     : GitHub username to analyse.
    github_token        : GitHub personal access token.  Used during ingest
                          and then discarded — never included in the return
                          value.
    repositories        : Explicit list of ``owner/repo`` strings.  When given,
                          evidence is scoped to exactly these repositories using
                          OSS-20 per-repo OR-search semantics (one search per
                          repo, results merged and deduped).  When ``None``,
                          activity is fetched broadly (optionally scoped by org
                          inside the ingestion layer).
    start_at            : Exact UTC-aware analysis-window start (inclusive).
                          Passed unchanged to OSS-20 ingestion.
    end_at              : Exact UTC-aware analysis-window end (inclusive).
                          Passed unchanged to OSS-20 ingestion.
    supplemental_context: Optional list of :class:`~pullstar.models.SupplementalContextBlock`
                          dicts.  Non-empty blocks are appended to the user
                          message as a generic ``SUPPLEMENTAL CONTEXT`` section.
                          The engine does not interpret block labels or know the
                          context source.
    prompt_spec         : Packaged prompt version (``"brief_v1"``, ``"brief_v2"``)
                          or path to a custom prompt file.  When ``None``,
                          defaults to ``"brief_v2"`` — the v0.2 engine default.
                          Pass ``"brief_v1"`` explicitly to reproduce legacy
                          prompt behaviour.

    Returns
    -------
    PreparedOneOnOne
        Deterministic analysis state plus provider-neutral inference payload.
        Pass ``prepared.inference`` to external inference, then call
        :func:`finalize_1on1`.

    Raises
    ------
    pullstar.ingest.PullStarIngestError
        On GitHub API / network failures during ingest.
    pullstar.ingest.PullStarRateLimitError
        When the GitHub API rate limit is exceeded.
    FileNotFoundError
        When ``prompt_spec`` names neither a packaged prompt nor an existing
        file path.
    """
    # ── 1. Ingest ────────────────────────────────────────────────────────────
    # Token used here only; not referenced or stored from this point on.
    ingest = ingest_developer_activity(
        developer_login,
        token=github_token,
        repositories=repositories,
        start_at=start_at,
        end_at=end_at,
    )

    # ── 2. Score ─────────────────────────────────────────────────────────────
    score = score_profile(ingest)

    # ── 3. Resolve prompt ────────────────────────────────────────────────────
    # Engine default is brief_v2.  Lower-level resolve_brief_prompt(None) still
    # returns brief_v1 for backward compat; we make the engine choice explicit.
    effective_spec = prompt_spec if prompt_spec is not None else _ENGINE_DEFAULT_PROMPT
    prompt = resolve_brief_prompt(effective_spec)

    # ── 4. Build credential-free LLM payload ─────────────────────────────────
    payload = build_llm_input_payload(
        score,
        ingest,
        prompt.text,
        model=None,
        provider=None,
        mode="engine",
        prompt_name=prompt.name,
        supplemental_context=supplemental_context,
    )

    # ── 5. Extract provider-neutral inference metadata ────────────────────────
    raw_meta = payload.get("metadata", {})
    inference_metadata: dict = {
        "generated_at":             raw_meta.get("generated_at"),
        "lookback_days":            raw_meta.get("lookback_days"),
        "total_score":              raw_meta.get("total_score"),
        "confidence":               raw_meta.get("confidence"),
        "has_insights":             raw_meta.get("has_insights"),
        "has_supplemental_context": raw_meta.get("has_supplemental_context"),
    }
    # provider and model are intentionally omitted — the engine is inference-neutral.

    # ── 6. Assemble and return ────────────────────────────────────────────────
    inference = PreparedInference(
        system_prompt=payload["system"],
        user_message=payload["user"],
        prompt_name=prompt.name,
        metadata=inference_metadata,
    )

    return PreparedOneOnOne(
        inference=inference,
        ingest=ingest,
        score=score,
    )


# ---------------------------------------------------------------------------
# finalize_1on1
# ---------------------------------------------------------------------------


def finalize_1on1(
    prepared: PreparedOneOnOne,
    completion: str,
) -> EngineResult:
    """Finalize a PullStar 1-on-1 after external inference.

    Validates the completion and packages it as an :class:`EngineResult`.

    Normalization: ``completion`` is ``strip()``-normalized — leading and
    trailing whitespace is removed.  Content is otherwise preserved verbatim.

    Makes no network calls.  Does not mutate ``prepared``.

    Parameters
    ----------
    prepared   : The :class:`PreparedOneOnOne` returned by :func:`prepare_1on1`.
    completion : Raw inference completion (a non-empty Markdown brief).

    Returns
    -------
    EngineResult

    Raises
    ------
    FinalizationError
        If ``completion`` is not a ``str``, or is empty / whitespace-only.
    """
    if not isinstance(completion, str):
        raise FinalizationError(
            f"completion must be str, got {type(completion).__name__!r}."
        )
    markdown = completion.strip()
    if not markdown:
        raise FinalizationError(
            "completion is empty or whitespace-only — "
            "inference must return a non-empty Markdown brief."
        )

    return EngineResult(
        markdown=markdown,
        prompt_name=prepared.inference.prompt_name,
        structured=None,
        metadata={
            "finalized_at":   datetime.now(timezone.utc).isoformat(),
            "engineer_login": prepared.score["engineer_login"],
            "total_score":    prepared.score["total_score"],
            "confidence":     prepared.score["confidence"],
        },
    )
