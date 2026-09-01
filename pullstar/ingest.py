"""
pullstar.ingest — Library GitHub developer-activity ingestion for PullStar 1-on-1

Public API:
    ingest_developer_activity(login, *, token, ...) -> IngestedProfile dict
    PullStarIngestError      — base class for all ingest API/network failures
    PullStarRateLimitError   — GitHub rate limit exceeded (subclass of above)

Design contract:
    - Returns IngestedProfile dict in memory; no file I/O of any kind.
    - Caller supplies token; this module never reads .env or credential files.
    - repositories=[...] whitelists repos: one search issued per repo (OR semantics),
      results merged/deduped, then post-filtered against the explicit whitelist.
    - One deterministic time window is resolved before any GitHub call; both
      authored and reviewed activity use the same window_start/window_end.
    - Time qualifiers use full ISO-8601 datetime ranges (created:START..END /
      updated:START..END); results are post-filtered to exact UTC boundaries.
    - Import-safe: no network, no file I/O, no argv parsing at import time.
    - Does not mutate global socket defaults.

Intentional differences from scripts/ingest.py:
    - No credential resolver (no .env / ~/.pullstar/credentials reads).
    - No file writes; IngestedProfile is returned in memory.
    - window_end is captured once at function entry and reused as ingested_at.
    - No socket.setdefaulttimeout(); request timeouts are set explicitly.
    - repositories= uses per-repo searches merged with dedup (OR semantics),
      not multiple repo: qualifiers in one query (which would be AND semantics).
    - org= is ignored when repositories= is provided.
    - Raises PullStarIngestError / PullStarRateLimitError, not RuntimeError.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import requests
from github import Github, GithubException, RateLimitExceededException


# ---------------------------------------------------------------------------
# Public exception types
# ---------------------------------------------------------------------------

class PullStarIngestError(Exception):
    """Raised on API or network failures during ingest.

    Base class for all ingest errors. Tokens are never embedded in the
    exception message.
    """


class PullStarRateLimitError(PullStarIngestError):
    """Raised when the GitHub API rate limit is exceeded."""


# ---------------------------------------------------------------------------
# Caps and limits (same defaults as legacy CLI)
# ---------------------------------------------------------------------------

_INSIGHTS_PR_CAP       = 20
_INSIGHTS_MAX_REVIEWS  = 10
_INSIGHTS_MAX_COMMENTS = 10
_INSIGHTS_EXCERPT_LEN  = 600
_MAX_SEARCH_RESULTS    = 50
_SEARCH_PAGE_SIZE      = 30

# ---------------------------------------------------------------------------
# GraphQL transport
# ---------------------------------------------------------------------------

_GRAPHQL_URL = "https://api.github.com/graphql"


def _gql(token: str, query: str, variables: dict) -> dict:
    """POST one GraphQL request. Returns the data dict or raises on errors.

    Raises PullStarRateLimitError for rate-limit responses.
    Raises PullStarIngestError for authentication failures, HTTP errors, or
    malformed GraphQL error responses. Token is never included in exception text.
    """
    try:
        resp = requests.post(
            _GRAPHQL_URL,
            json={"query": query, "variables": variables},
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
    except requests.exceptions.Timeout:
        raise PullStarIngestError("GitHub GraphQL request timed out.")
    except requests.exceptions.RequestException as exc:
        raise PullStarIngestError(f"GitHub GraphQL request failed: {exc}") from exc

    if resp.status_code in (401, 403):
        raise PullStarIngestError(
            "GitHub API authentication failed — check the supplied token."
        )
    if not resp.ok:
        raise PullStarIngestError(f"GitHub API HTTP error {resp.status_code}.")
    result = resp.json()
    if "errors" in result:
        for err in result["errors"]:
            if err.get("type") == "RATE_LIMITED":
                raise PullStarRateLimitError(
                    err.get("message", "GitHub GraphQL rate limit exceeded.")
                )
        raise PullStarIngestError(
            f"GitHub GraphQL error: {len(result['errors'])} error(s) returned."
        )
    return result.get("data", {})


# ---------------------------------------------------------------------------
# GraphQL query strings
# ---------------------------------------------------------------------------

_USER_QUERY = """
query($login: String!) {
  user(login: $login) { name }
}
"""

_AUTHORED_PRS_QUERY = """
query($query: String!, $first: Int!, $cursor: String) {
  search(query: $query, type: ISSUE, first: $first, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number title url state
        createdAt mergedAt
        additions deletions changedFiles
        commits { totalCount }
        body
        labels(first: 10) { nodes { name } }
        baseRefName
        repository { nameWithOwner }
        reviews { totalCount }
      }
    }
  }
}
"""

_AUTHORED_PRS_INSIGHTS_QUERY = """
query($query: String!, $first: Int!, $cursor: String) {
  search(query: $query, type: ISSUE, first: $first, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number title url state
        createdAt mergedAt
        additions deletions changedFiles
        commits { totalCount }
        body
        labels(first: 10) { nodes { name } }
        baseRefName
        repository { nameWithOwner }
        reviews(first: 10) {
          totalCount
          nodes { state body author { login } submittedAt }
        }
        comments(first: 10) {
          nodes { body author { login } createdAt }
        }
      }
    }
  }
}
"""

_REVIEWS_GIVEN_QUERY = """
query($query: String!, $first: Int!, $cursor: String) {
  search(query: $query, type: ISSUE, first: $first, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number title
        repository { nameWithOwner }
        reviews(first: 50) {
          nodes { state body author { login } submittedAt }
        }
      }
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_review_state(state: str) -> str:
    mapping = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes_requested"}
    return mapping.get(state.upper(), "commented")


def _week_index(dt: datetime, since_dt: datetime) -> int:
    return int((dt - since_dt).total_seconds() // (7 * 86400))


def _truncate(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


def _parse_ts(s: str | None) -> str | None:
    return s.replace("Z", "+00:00") if s else None


def _fmt_ts(dt: datetime) -> str:
    """Format a UTC datetime as an ISO-8601 string for GitHub search qualifiers."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _within_window(ts_str: str | None, start: datetime, end: datetime) -> bool:
    """Return True iff ts_str parses to a UTC datetime in [start, end] (inclusive)."""
    if not ts_str:
        return False
    try:
        dt = _as_utc(datetime.fromisoformat(ts_str))
    except ValueError:
        return False
    return start <= dt <= end


# ---------------------------------------------------------------------------
# Query string builders  (no repository qualifiers — handled by per-repo loop)
# ---------------------------------------------------------------------------

def _pr_query(login: str, since_iso: str, until_iso: str, org: str | None) -> str:
    """Base authored-PR search query. Omits repo scope; caller appends repo: if needed."""
    q = f"author:{login} is:pr created:{since_iso}..{until_iso}"
    if org:
        q += f" org:{org}"
    return q


def _review_query(login: str, since_iso: str, org: str | None) -> str:
    """Base reviewed-PR candidate query. Omits repo scope; caller appends repo: if needed.

    Uses updated:>=START with NO upper bound.

    Rationale: a review submitted inside the window may live on a PR that is
    subsequently updated after end_at. An upper-bounded updated:START..END
    would exclude that PR from the candidate set, silently dropping a valid
    review event. The lower bound is safe — a review submitted at or after
    window_start necessarily caused/coincided with a PR update at or after
    window_start. The actual review-event timestamp (submitted_at) is the
    authoritative inclusion gate; see _within_window().

    Pagination note: results include PRs updated after end_at. With GitHub's
    default sort (updated desc) these appear first, consuming the max_results
    budget before in-window PRs. This is a known architectural limitation of
    the current single-pass search design; callers requiring completeness
    should set max_results to a value large enough to cover the expected
    candidate pool.
    """
    q = f"reviewed-by:{login} is:pr updated:>={since_iso}"
    if org:
        q += f" org:{org}"
    return q


# ---------------------------------------------------------------------------
# Deduplication helpers
# ---------------------------------------------------------------------------

def _dedup_prs(prs: list[dict]) -> list[dict]:
    """Deduplicate PR list by (repository, number), preserving insertion order."""
    seen: set[tuple[str, int]] = set()
    result: list[dict] = []
    for pr in prs:
        key = (pr["repository"], pr["number"])
        if key not in seen:
            seen.add(key)
            result.append(pr)
    return result


def _dedup_reviews(reviews: list[dict]) -> list[dict]:
    """Deduplicate reviews by (repository, pr_number, submitted_at), preserving order."""
    seen: set[tuple[str, int, str]] = set()
    result: list[dict] = []
    for r in reviews:
        key = (r["repository"], r["pr_number"], r["submitted_at"])
        if key not in seen:
            seen.add(key)
            result.append(r)
    return result


# ---------------------------------------------------------------------------
# GraphQL search helpers (paginate one query string, return normalized dicts)
# ---------------------------------------------------------------------------

def _gql_search_prs(
    token: str,
    search_q: str,
    max_results: int,
    pr_insights: bool,
) -> list[dict]:
    """Execute a paginated GraphQL PR search; return normalised PR dicts."""
    prs: list[dict] = []
    prs_checked = 0
    cursor: str | None = None
    gql_query = _AUTHORED_PRS_INSIGHTS_QUERY if pr_insights else _AUTHORED_PRS_QUERY

    while prs_checked < max_results:
        batch = min(_SEARCH_PAGE_SIZE, max_results - prs_checked)
        data  = _gql(token, gql_query, {"query": search_q, "first": batch, "cursor": cursor})
        search = data["search"]

        for node in search["nodes"]:
            prs_checked += 1
            if not node or "number" not in node:
                continue

            gql_state = node["state"]
            status = (
                "merged" if gql_state == "MERGED"
                else ("closed" if gql_state == "CLOSED" else "open")
            )
            reviews_node  = node["reviews"]
            reviews_count = reviews_node["totalCount"]

            pr_entry: dict = {
                "number":                 node["number"],
                "title":                  node["title"],
                "url":                    node["url"],
                "repository":             node["repository"]["nameWithOwner"],
                "status":                 status,
                "created_at":             _parse_ts(node["createdAt"]),
                "merged_at":              _parse_ts(node.get("mergedAt")),
                "lines_added":            node["additions"] or 0,
                "lines_deleted":          node["deletions"] or 0,
                "files_changed":          node["changedFiles"] or 0,
                "commit_count":           node["commits"]["totalCount"] or 0,
                "description_length":     len(node.get("body") or ""),
                "label_names":            [lbl["name"] for lbl in node["labels"]["nodes"]],
                "base_branch":            node["baseRefName"],
                "reviews_received_count": reviews_count,
            }

            if pr_insights:
                review_nodes  = (reviews_node.get("nodes") or [])[:_INSIGHTS_MAX_REVIEWS]
                comment_nodes = (node.get("comments", {}).get("nodes") or [])[:_INSIGHTS_MAX_COMMENTS]
                reviews_detail: list[dict] = []
                cr_count = 0
                ap_count = 0
                for r in review_nodes:
                    st = _normalize_review_state(r.get("state") or "COMMENTED")
                    if st == "changes_requested":
                        cr_count += 1
                    if st == "approved":
                        ap_count += 1
                    body = (r.get("body") or "").strip()
                    reviews_detail.append({
                        "reviewer_login": (r.get("author") or {}).get("login"),
                        "state":          st,
                        "submitted_at":   _parse_ts(r.get("submittedAt")),
                        "body_length":    len(body),
                        "body_excerpt":   _truncate(body, _INSIGHTS_EXCERPT_LEN) if body else "",
                    })
                comments_detail: list[dict] = []
                for c in comment_nodes:
                    body = (c.get("body") or "").strip()
                    comments_detail.append({
                        "author_login": (c.get("author") or {}).get("login"),
                        "created_at":   _parse_ts(c.get("createdAt")),
                        "body_length":  len(body),
                        "body_excerpt": _truncate(body, _INSIGHTS_EXCERPT_LEN) if body else "",
                        "comment_type": "issue_comment",
                    })
                pr_entry.update({
                    "reviews_received_detail":  reviews_detail,
                    "comments_detail":          comments_detail,
                    "discussion_summary_stats": {
                        "review_count":            len(reviews_detail),
                        "comment_count":           len(comments_detail),
                        "changes_requested_count": cr_count,
                        "approved_count":          ap_count,
                    },
                })
                pr_entry["reviews_received_count"] = len(reviews_detail)

            prs.append(pr_entry)

        if not search["pageInfo"]["hasNextPage"] or prs_checked >= max_results:
            break
        cursor = search["pageInfo"]["endCursor"]

    return prs


def _gql_search_reviews(
    token: str,
    search_q: str,
    login: str,
    window_start: datetime,
    window_end: datetime,
    max_results: int,
) -> list[dict]:
    """Execute a paginated GraphQL review search; return normalised review dicts.

    Filters to reviews submitted by `login` within [window_start, window_end].
    """
    reviews: list[dict] = []
    prs_checked = 0
    cursor: str | None = None

    while prs_checked < max_results:
        batch  = min(_SEARCH_PAGE_SIZE, max_results - prs_checked)
        data   = _gql(token, _REVIEWS_GIVEN_QUERY, {"query": search_q, "first": batch, "cursor": cursor})
        search = data["search"]

        for node in search["nodes"]:
            prs_checked += 1
            if not node or "number" not in node:
                continue
            repo_name = node["repository"]["nameWithOwner"]
            for review in (node["reviews"]["nodes"] or []):
                if not review.get("author") or review["author"].get("login") != login:
                    continue
                submitted_ts = _parse_ts(review.get("submittedAt"))
                if not submitted_ts:
                    continue
                if not _within_window(submitted_ts, window_start, window_end):
                    continue
                reviews.append({
                    "repository":   repo_name,
                    "pr_number":    node["number"],
                    "pr_title":     node["title"],
                    "state":        _normalize_review_state(review.get("state") or "COMMENTED"),
                    "submitted_at": submitted_ts,
                    "body_length":  len(review.get("body") or ""),
                })

        if not search["pageInfo"]["hasNextPage"] or prs_checked >= max_results:
            break
        cursor = search["pageInfo"]["endCursor"]

    return reviews


# ---------------------------------------------------------------------------
# REST PR insights collector
# ---------------------------------------------------------------------------

def _fetch_pr_details_rest(pr_data: dict, g: Github) -> dict | None:
    repo_name = pr_data["repository"]
    pr_number = pr_data["number"]

    try:
        repo = g.get_repo(repo_name)
        pr   = repo.get_pull(pr_number)
    except GithubException:
        return None

    reviews_detail: list[dict] = []
    changes_requested_count    = 0
    approved_count             = 0

    try:
        for review in pr.get_reviews():
            if len(reviews_detail) >= _INSIGHTS_MAX_REVIEWS:
                break
            state = _normalize_review_state(review.state or "COMMENTED")
            if state == "changes_requested":
                changes_requested_count += 1
            if state == "approved":
                approved_count += 1
            body      = (review.body or "").strip()
            submitted = _as_utc(review.submitted_at) if review.submitted_at else None
            reviews_detail.append({
                "reviewer_login": review.user.login if review.user else None,
                "state":          state,
                "submitted_at":   submitted.isoformat() if submitted else None,
                "body_length":    len(body),
                "body_excerpt":   _truncate(body, _INSIGHTS_EXCERPT_LEN) if body else "",
            })
    except GithubException:
        pass

    comments_detail: list[dict] = []
    try:
        for comment in repo.get_issue(pr_number).get_comments():
            if len(comments_detail) >= _INSIGHTS_MAX_COMMENTS:
                break
            body    = (comment.body or "").strip()
            created = _as_utc(comment.created_at) if comment.created_at else None
            comments_detail.append({
                "author_login": comment.user.login if comment.user else None,
                "created_at":   created.isoformat() if created else None,
                "body_length":  len(body),
                "body_excerpt": _truncate(body, _INSIGHTS_EXCERPT_LEN) if body else "",
                "comment_type": "issue_comment",
            })
    except GithubException:
        pass

    return {
        "reviews_received_detail":  reviews_detail,
        "comments_detail":          comments_detail,
        "discussion_summary_stats": {
            "review_count":            len(reviews_detail),
            "comment_count":           len(comments_detail),
            "changes_requested_count": changes_requested_count,
            "approved_count":          approved_count,
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ingest_developer_activity(
    login: str,
    *,
    token: str | None = None,
    repositories: list[str] | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    days: int | None = None,
    org: str | None = None,
    pr_insights: bool = False,
    api_mode: str = "graphql",
    max_results: int = _MAX_SEARCH_RESULTS,
) -> dict:
    """
    Fetch one developer's GitHub activity and return an IngestedProfile dict.

    Parameters
    ----------
    login        : GitHub username (required).
    token        : GitHub PAT. Required for GraphQL; optional for REST
                   (unauthenticated allows 60 req/hr).
    repositories : Whitelist of "owner/repo" strings. When provided, one
                   search is issued per repo (OR semantics) and results are
                   merged and deduplicated. org= is ignored when set.
                   When None, activity is fetched broadly (optionally scoped
                   to org=).
    start_at     : Explicit UTC-aware start of the analysis window (inclusive).
    end_at       : Explicit UTC-aware end of the analysis window (inclusive).
    days         : Lookback relative to now. Mutually exclusive with
                   start_at/end_at. Defaults to 30 when neither is given.
    org          : Restrict to this GitHub org (ignored when repositories= set).
    pr_insights  : Collect detailed review/comment context per PR.
    api_mode     : "graphql" (default, requires token) or "rest".
    max_results  : Maximum search results per query per repo (default 50).

    Returns
    -------
    IngestedProfile dict (same schema as scripts/ingest.py).
    No file is written. No credential sources are consulted.

    Raises
    ------
    ValueError             : Invalid or ambiguous time-window specification.
    PullStarRateLimitError : GitHub rate limit exceeded.
    PullStarIngestError    : Any other GitHub API or network failure.
    """
    # -- Time window: one deterministic resolution before any GitHub call -----
    has_explicit_window = start_at is not None or end_at is not None
    if has_explicit_window and days is not None:
        raise ValueError("Specify either days or (start_at + end_at), not both.")
    if (start_at is None) != (end_at is None):
        raise ValueError("start_at and end_at must both be provided or both omitted.")

    if start_at is not None:
        window_start   = _as_utc(start_at)
        window_end     = _as_utc(end_at)  # type: ignore[arg-type]
        effective_days = max(1, (window_end - window_start).days)
    else:
        effective_days = days if days is not None else 30
        window_end     = datetime.now(timezone.utc)
        window_start   = window_end - timedelta(days=effective_days)

    since_iso   = _fmt_ts(window_start)
    until_iso   = _fmt_ts(window_end)
    total_weeks = math.ceil(effective_days / 7)

    # -- Determine effective API mode ----------------------------------------
    use_graphql = (api_mode == "graphql") and (token is not None)

    g: Github | None = None
    if not use_graphql:
        g = (
            Github(token, timeout=60, per_page=_SEARCH_PAGE_SIZE)
            if token
            else Github(timeout=60, per_page=_SEARCH_PAGE_SIZE)
        )

    login = login.strip()

    # -- Effective org scope (ignored when repositories= provided) -----------
    effective_org = None if repositories else org

    # -- Build base query strings (no per-repo qualifiers yet) ---------------
    authored_q_base = _pr_query(login, since_iso, until_iso, effective_org)
    reviewed_q_base = _review_query(login, since_iso, effective_org)

    # -- Look up display name (best-effort, never fatal) ---------------------
    engineer_name: str | None = None
    if use_graphql:
        assert token
        try:
            data = _gql(token, _USER_QUERY, {"login": login})
            engineer_name = (data.get("user") or {}).get("name")
        except PullStarIngestError:
            pass
    else:
        assert g is not None
        try:
            engineer_name = g.get_user(login).name
        except GithubException:
            pass

    # -- Fetch PRs authored --------------------------------------------------
    prs_authored: list[dict] = []

    if use_graphql:
        assert token
        if repositories:
            raw: list[dict] = []
            for repo in repositories:
                q = f"{authored_q_base} repo:{repo}"
                raw.extend(_gql_search_prs(token, q, max_results, pr_insights))
            prs_authored = _dedup_prs(raw)
        else:
            prs_authored = _gql_search_prs(token, authored_q_base, max_results, pr_insights)

        # Trim insights fields from PRs beyond the insights cap
        if pr_insights:
            for pr in prs_authored[_INSIGHTS_PR_CAP:]:
                pr.pop("reviews_received_detail", None)
                pr.pop("comments_detail", None)
                pr.pop("discussion_summary_stats", None)

    else:  # REST
        assert g is not None
        repo_queries = (
            [f"{authored_q_base} repo:{r}" for r in repositories]
            if repositories
            else [authored_q_base]
        )
        raw_rest: list[dict] = []
        try:
            for q in repo_queries:
                total_fetched = 0
                for issue in g.search_issues(q, sort="created", order="desc"):
                    total_fetched += 1
                    if total_fetched > max_results:
                        break
                    try:
                        repo_obj = g.get_repo(issue.repository.full_name)
                        pr       = repo_obj.get_pull(issue.number)
                    except GithubException:
                        continue
                    status = "merged" if pr.merged else ("closed" if pr.state == "closed" else "open")
                    raw_rest.append({
                        "number":                 pr.number,
                        "title":                  pr.title,
                        "url":                    pr.html_url,
                        "repository":             issue.repository.full_name,
                        "status":                 status,
                        "created_at":             _as_utc(pr.created_at).isoformat() if pr.created_at else None,
                        "merged_at":              _as_utc(pr.merged_at).isoformat() if pr.merged_at else None,
                        "lines_added":            pr.additions or 0,
                        "lines_deleted":          pr.deletions or 0,
                        "files_changed":          pr.changed_files or 0,
                        "commit_count":           pr.commits or 0,
                        "description_length":     len(pr.body or ""),
                        "label_names":            [lbl.name for lbl in pr.labels],
                        "base_branch":            pr.base.ref,
                        "reviews_received_count": 0,
                    })
        except RateLimitExceededException as exc:
            raise PullStarRateLimitError("GitHub rate limit hit fetching PRs.") from exc
        except GithubException as exc:
            raise PullStarIngestError(f"GitHub API error fetching PRs.") from exc
        prs_authored = _dedup_prs(raw_rest)

    # -- Fetch reviews given -------------------------------------------------
    reviews_given: list[dict] = []

    if use_graphql:
        assert token
        if repositories:
            raw_rev: list[dict] = []
            for repo in repositories:
                q = f"{reviewed_q_base} repo:{repo}"
                raw_rev.extend(
                    _gql_search_reviews(token, q, login, window_start, window_end, max_results)
                )
            reviews_given = _dedup_reviews(raw_rev)
        else:
            reviews_given = _gql_search_reviews(
                token, reviewed_q_base, login, window_start, window_end, max_results
            )

    else:  # REST
        assert g is not None
        repo_queries_rev = (
            [f"{reviewed_q_base} repo:{r}" for r in repositories]
            if repositories
            else [reviewed_q_base]
        )
        raw_rev_rest: list[dict] = []
        try:
            for q in repo_queries_rev:
                total_fetched = 0
                for issue in g.search_issues(q, sort="updated", order="desc"):
                    total_fetched += 1
                    if total_fetched > max_results:
                        break
                    try:
                        repo_obj = g.get_repo(issue.repository.full_name)
                        pr       = repo_obj.get_pull(issue.number)
                        for review in pr.get_reviews():
                            if not review.user or review.user.login != login:
                                continue
                            submitted = _as_utc(review.submitted_at) if review.submitted_at else None
                            if submitted is None:
                                continue
                            submitted_iso = submitted.isoformat()
                            if not _within_window(submitted_iso, window_start, window_end):
                                continue
                            raw_rev_rest.append({
                                "repository":   issue.repository.full_name,
                                "pr_number":    pr.number,
                                "pr_title":     pr.title,
                                "state":        _normalize_review_state(review.state or "COMMENTED"),
                                "submitted_at": submitted_iso,
                                "body_length":  len(review.body or ""),
                            })
                    except GithubException:
                        continue
        except RateLimitExceededException as exc:
            raise PullStarRateLimitError("GitHub rate limit hit fetching reviews.") from exc
        except GithubException as exc:
            raise PullStarIngestError("GitHub API error fetching reviews.") from exc
        reviews_given = _dedup_reviews(raw_rev_rest)

    # -- REST PR insights enrichment -----------------------------------------
    if pr_insights and not use_graphql and g is not None:
        for pr_data in prs_authored[:_INSIGHTS_PR_CAP]:
            details = _fetch_pr_details_rest(pr_data, g)
            if details:
                pr_data.update(details)
                pr_data["reviews_received_count"] = (
                    details["discussion_summary_stats"]["review_count"]
                )

    # -- Exact-window post-filter (defence in depth for both modes) ----------
    # Authored PRs: filter by created_at (the field PullStar's authored semantics track)
    prs_authored = [
        p for p in prs_authored
        if _within_window(p.get("created_at"), window_start, window_end)
    ]
    # Reviews given: filtered inside _gql_search_reviews; REST path filters above.
    # Apply one final pass to catch any edge cases from either path.
    reviews_given = [
        r for r in reviews_given
        if _within_window(r.get("submitted_at"), window_start, window_end)
    ]

    # -- Repository whitelist post-filter (defence in depth) -----------------
    if repositories is not None:
        repo_set      = set(repositories)
        prs_authored  = [p for p in prs_authored  if p["repository"] in repo_set]
        reviews_given = [r for r in reviews_given if r["repository"] in repo_set]

    # -- Summary stats -------------------------------------------------------
    total_lines_added   = sum(p["lines_added"]   for p in prs_authored)
    total_lines_deleted = sum(p["lines_deleted"] for p in prs_authored)
    n_prs = len(prs_authored)

    active_week_set: set[int] = set()
    for pr in prs_authored:
        if pr.get("created_at"):
            w = _week_index(_as_utc(datetime.fromisoformat(pr["created_at"])), window_start)
            if 0 <= w < total_weeks:
                active_week_set.add(w)
    for review in reviews_given:
        w = _week_index(_as_utc(datetime.fromisoformat(review["submitted_at"])), window_start)
        if 0 <= w < total_weeks:
            active_week_set.add(w)

    total_lines = total_lines_added + total_lines_deleted
    summary_stats = {
        "total_prs_authored":     n_prs,
        "prs_merged":             sum(1 for p in prs_authored if p["status"] == "merged"),
        "prs_open":               sum(1 for p in prs_authored if p["status"] == "open"),
        "prs_closed_unmerged":    sum(1 for p in prs_authored if p["status"] == "closed"),
        "total_reviews_given":    len(reviews_given),
        "total_reviews_received": sum(p["reviews_received_count"] for p in prs_authored),
        "total_lines_added":      total_lines_added,
        "total_lines_deleted":    total_lines_deleted,
        "avg_pr_size_lines":      round(total_lines / n_prs, 1) if n_prs else 0.0,
        "active_weeks":           len(active_week_set),
        "total_weeks":            total_weeks,
    }

    return {
        "engineer_login":  login,
        "engineer_name":   engineer_name,
        "org":             org or "",
        "lookback_days":   effective_days,
        "ingested_at":     window_end.isoformat(),
        "prs_authored":    prs_authored,
        "reviews_given":   reviews_given,
        "reviews_received": [],
        "summary_stats":   summary_stats,
    }
