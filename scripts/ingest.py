"""
ingest.py — GitHub data fetcher for PullStar 1-on-1

Usage:
    python scripts/ingest.py --login jsmith
    python scripts/ingest.py --login jsmith --days 14 --output-dir .pullstar
    python scripts/ingest.py --login jsmith --pr_insights

Phase 2: Adds optional --pr_insights flag for detailed PR discussion context.
         When enabled, each authored PR is enriched with three new fields:
           - reviews_received_detail  (list of compact review objects)
           - comments_detail          (list of compact issue comment objects)
           - discussion_summary_stats (aggregate counts)
         Default mode (no flag) is unchanged.
"""

import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from github import Github, GithubException, RateLimitExceededException

# Load .env from repo root regardless of where the script is invoked from
load_dotenv(Path(__file__).parent.parent / ".env")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fail(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def as_utc(dt: datetime | None) -> datetime | None:
    """Return dt as a timezone-aware UTC datetime, or None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def normalize_review_state(state: str) -> str:
    """Map GitHub review states to the three values in our schema."""
    mapping = {
        "APPROVED": "approved",
        "CHANGES_REQUESTED": "changes_requested",
    }
    return mapping.get(state.upper(), "commented")  # COMMENTED, DISMISSED -> commented


def week_index(dt: datetime, since_dt: datetime) -> int:
    """Return which 0-based week bucket dt falls into relative to since_dt."""
    return int((dt - since_dt).total_seconds() // (7 * 86400))


def _truncate(text: str, limit: int) -> str:
    """Collapse whitespace and truncate to limit chars for a compact excerpt."""
    return " ".join(text.split())[:limit]


# ---------------------------------------------------------------------------
# PR insights collector (--pr_insights flag only)
# ---------------------------------------------------------------------------
#
# Caps to control runtime and rate-limit footprint.
# Each PR costs 3 core API calls: get_pull + get_reviews + get_issue_comments.
# At _INSIGHTS_PR_CAP=20, worst case is 60 additional calls against the 5000/hr limit.
#
_INSIGHTS_PR_CAP       = 20   # max authored PRs to enrich with insight detail
_INSIGHTS_MAX_REVIEWS  = 10   # max review objects stored per PR
_INSIGHTS_MAX_COMMENTS = 10   # max issue comment objects stored per PR
_INSIGHTS_EXCERPT_LEN  = 600  # chars per body excerpt (reviews and comments)


def fetch_pr_details(pr_data: dict, g: Github) -> dict | None:
    """
    Fetch review and comment detail for one authored PR.

    Returns a dict of three insight fields to merge into the PR record:
      - reviews_received_detail:  list of compact review objects (up to _INSIGHTS_MAX_REVIEWS)
      - comments_detail:          list of compact issue comment objects (up to _INSIGHTS_MAX_COMMENTS)
      - discussion_summary_stats: {review_count, comment_count,
                                   changes_requested_count, approved_count}

    Returns None if the PR object itself cannot be fetched.
    Per-section failures (reviews or comments) are logged and produce empty lists.

    API calls: 1 (get_pull) + 1 (get_reviews) + 1 (get_issue + get_comments) = 3.
    """
    repo_name = pr_data["repository"]
    pr_number = pr_data["number"]

    try:
        repo = g.get_repo(repo_name)
        pr   = repo.get_pull(pr_number)
    except GithubException as exc:
        print(f"  warning: could not fetch PR #{pr_number} ({repo_name}) — {exc}",
              file=sys.stderr)
        return None

    # --- Reviews received ---
    reviews_detail: list[dict] = []
    changes_requested_count = 0
    approved_count          = 0

    try:
        print(f"  fetching review details for PR #{pr_number} ({repo_name})...")
        for review in pr.get_reviews():
            if len(reviews_detail) >= _INSIGHTS_MAX_REVIEWS:
                break
            state = normalize_review_state(review.state or "COMMENTED")
            if state == "changes_requested":
                changes_requested_count += 1
            if state == "approved":
                approved_count += 1
            body      = (review.body or "").strip()
            submitted = as_utc(review.submitted_at)
            reviews_detail.append({
                "reviewer_login": review.user.login if review.user else None,
                "state":          state,
                "submitted_at":   submitted.isoformat() if submitted else None,
                "body_length":    len(body),
                "body_excerpt":   _truncate(body, _INSIGHTS_EXCERPT_LEN) if body else "",
            })
    except GithubException as exc:
        print(f"  warning: reviews fetch failed for PR #{pr_number} — {exc}",
              file=sys.stderr)

    # --- Issue comments (top-level PR conversation, not inline code comments) ---
    comments_detail: list[dict] = []

    try:
        print(f"  fetching comments for PR #{pr_number} ({repo_name})...")
        for comment in repo.get_issue(pr_number).get_comments():
            if len(comments_detail) >= _INSIGHTS_MAX_COMMENTS:
                break
            body    = (comment.body or "").strip()
            created = as_utc(comment.created_at)
            comments_detail.append({
                "author_login": comment.user.login if comment.user else None,
                "created_at":   created.isoformat() if created else None,
                "body_length":  len(body),
                "body_excerpt": _truncate(body, _INSIGHTS_EXCERPT_LEN) if body else "",
                "comment_type": "issue_comment",
            })
    except GithubException as exc:
        print(f"  warning: comments fetch failed for PR #{pr_number} — {exc}",
              file=sys.stderr)

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
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch GitHub activity for a PullStar 1-on-1 brief."
    )
    parser.add_argument("--login",       required=True, help="GitHub username of the engineer")
    parser.add_argument("--days",        type=int, default=30, help="Lookback window in days (default: 30)")
    parser.add_argument("--output-dir",  default=".pullstar", help="Output directory (default: .pullstar)")
    parser.add_argument(
        "--pr_insights",
        action="store_true",
        default=False,
        help=(
            "Collect lightweight PR discussion context (reviews, revision cycles, "
            "comment counts) for the most recent authored PRs. "
            f"Adds 2 API calls per PR, capped at {_INSIGHTS_PR_CAP} PRs."
        ),
    )
    args = parser.parse_args()

    # -- Environment ---------------------------------------------------------
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        fail(
            "GITHUB_TOKEN is not set.\n"
            "  Create a personal access token at: https://github.com/settings/tokens\n"
            "  Required scopes: repo (or read:org + repo for org-scoped search)\n"
            "  Then add it to your .env file:  GITHUB_TOKEN=ghp_..."
        )
    org_name = os.getenv("GITHUB_ORG", "").strip()
    login    = args.login.strip()
    days     = args.days

    output_dir = Path(args.output_dir)
    output_dir.mkdir(exist_ok=True)

    since_dt   = datetime.now(timezone.utc) - timedelta(days=days)
    since_iso  = since_dt.strftime("%Y-%m-%d")
    total_weeks = math.ceil(days / 7)

    g = Github(token)

    # -- Look up engineer display name (best-effort) -------------------------
    engineer_name: str | None = None
    try:
        user = g.get_user(login)
        engineer_name = user.name
    except GithubException:
        pass

    # -- Fetch PRs authored --------------------------------------------------
    print(f"> Fetching pull requests for {login} (last {days} days)...")

    pr_query = f"author:{login} is:pr created:>={since_iso}"
    if org_name:
        pr_query += f" org:{org_name}"

    print(f"  query: {pr_query}")
    prs_authored = []
    try:
        pr_results = g.search_issues(pr_query, sort="created", order="desc")
        for issue in pr_results:
            if len(prs_authored) >= 100:
                print("  (capped at 100 PRs)")
                break
            try:
                repo = g.get_repo(issue.repository.full_name)
                pr   = repo.get_pull(issue.number)
            except GithubException as exc:
                print(f"  warning: skipped PR #{issue.number} — {exc}", file=sys.stderr)
                continue

            if pr.merged:
                status = "merged"
            elif pr.state == "closed":
                status = "closed"
            else:
                status = "open"

            prs_authored.append({
                "number":               pr.number,
                "title":                pr.title,
                "url":                  pr.html_url,
                "repository":           issue.repository.full_name,
                "status":               status,
                "created_at":           pr.created_at.isoformat(),
                "merged_at":            pr.merged_at.isoformat() if pr.merged_at else None,
                "lines_added":          pr.additions or 0,
                "lines_deleted":        pr.deletions or 0,
                "files_changed":        pr.changed_files or 0,
                "commit_count":         pr.commits or 0,
                "description_length":   len(pr.body or ""),
                "label_names":          [lbl.name for lbl in pr.labels],
                "base_branch":          pr.base.ref,
                "reviews_received_count": 0,   # populated below when --pr_insights is set
            })

    except RateLimitExceededException:
        reset = g.get_rate_limit().search.reset
        fail(f"GitHub rate limit hit fetching PRs. Resets at {reset.isoformat()} UTC.")
    except GithubException as exc:
        if exc.status == 422:
            fail(
                f"GitHub rejected the PR search query (422 Validation Failed).\n"
                f"  This usually means the engineer login '{login}' is not visible to your token.\n"
                f"  Fine-grained PATs cannot search across arbitrary users — use a classic PAT instead.\n"
                f"  Create one at: https://github.com/settings/tokens (select 'repo' scope)\n"
                f"  Full error: {exc.data.get('errors', exc.data)}"
            )
        fail(f"GitHub API error fetching PRs (status {exc.status}): {exc.data}")

    print(f"> Found {len(prs_authored)} PRs authored")

    # -- Fetch reviews given -------------------------------------------------
    print("> Fetching reviews given...")

    review_query = f"reviewed-by:{login} is:pr updated:>={since_iso}"
    if org_name:
        review_query += f" org:{org_name}"

    print(f"  query: {review_query}")
    reviews_given = []
    try:
        reviewed_results = g.search_issues(review_query, sort="updated", order="desc")
        prs_checked = 0
        for issue in reviewed_results:
            if prs_checked >= 100 or len(reviews_given) >= 100:
                break
            prs_checked += 1
            try:
                repo = g.get_repo(issue.repository.full_name)
                pr   = repo.get_pull(issue.number)
                for review in pr.get_reviews():
                    if not review.user or review.user.login != login:
                        continue
                    submitted = as_utc(review.submitted_at)
                    if submitted is None or submitted < since_dt:
                        continue
                    reviews_given.append({
                        "repository":  issue.repository.full_name,
                        "pr_number":   pr.number,
                        "pr_title":    pr.title,
                        "state":       normalize_review_state(review.state or "COMMENTED"),
                        "submitted_at": submitted.isoformat(),
                        "body_length":  len(review.body or ""),
                    })
            except GithubException as exc:
                print(f"  warning: skipped review PR #{issue.number} — {exc}", file=sys.stderr)
                continue

    except RateLimitExceededException:
        reset = g.get_rate_limit().search.reset
        fail(f"GitHub rate limit hit fetching reviews. Resets at {reset.isoformat()} UTC.")
    except GithubException as exc:
        fail(f"GitHub API error fetching reviews (status {exc.status}): {exc.data}")

    print(f"> Found {len(reviews_given)} reviews given")

    # -- PR insights (optional) ----------------------------------------------
    # When --pr_insights is set, each authored PR (up to _INSIGHTS_PR_CAP) is
    # enriched IN PLACE with reviews_received_detail, comments_detail, and
    # discussion_summary_stats.  No separate top-level key is added.
    if args.pr_insights:
        to_enrich = prs_authored[:_INSIGHTS_PR_CAP]
        print(f"> Fetching PR insights for {len(to_enrich)} PRs "
              f"(capped at {_INSIGHTS_PR_CAP}, ~3 API calls each)...")
        enriched = 0
        for pr_data in to_enrich:
            details = fetch_pr_details(pr_data, g)
            if details:
                pr_data.update(details)
                # Back-fill reviews_received_count from the fetched review count
                pr_data["reviews_received_count"] = (
                    details["discussion_summary_stats"]["review_count"]
                )
                enriched += 1
        print(f"> PR insights written for {enriched} of {len(to_enrich)} PRs")

    # -- Compute summary stats -----------------------------------------------
    total_lines_added   = sum(p["lines_added"]   for p in prs_authored)
    total_lines_deleted = sum(p["lines_deleted"] for p in prs_authored)
    total_lines = total_lines_added + total_lines_deleted
    n_prs = len(prs_authored)

    # Active week = at least 1 PR created or 1 review submitted in that calendar week
    active_week_set: set[int] = set()
    for pr in prs_authored:
        created = as_utc(datetime.fromisoformat(pr["created_at"]))
        w = week_index(created, since_dt)
        if 0 <= w < total_weeks:
            active_week_set.add(w)
    for review in reviews_given:
        submitted = as_utc(datetime.fromisoformat(review["submitted_at"]))
        w = week_index(submitted, since_dt)
        if 0 <= w < total_weeks:
            active_week_set.add(w)

    summary_stats = {
        "total_prs_authored":    n_prs,
        "prs_merged":            sum(1 for p in prs_authored if p["status"] == "merged"),
        "prs_open":              sum(1 for p in prs_authored if p["status"] == "open"),
        "prs_closed_unmerged":   sum(1 for p in prs_authored if p["status"] == "closed"),
        "total_reviews_given":   len(reviews_given),
        "total_reviews_received": sum(p["reviews_received_count"] for p in prs_authored),
        "total_lines_added":     total_lines_added,
        "total_lines_deleted":   total_lines_deleted,
        "avg_pr_size_lines":     round(total_lines / n_prs, 1) if n_prs else 0.0,
        "active_weeks":          len(active_week_set),
        "total_weeks":           total_weeks,
    }

    # -- Build output --------------------------------------------------------
    # PR insight fields (reviews_received_detail, comments_detail,
    # discussion_summary_stats) are embedded directly in prs_authored entries
    # when --pr_insights is used — no separate top-level key needed.
    profile: dict = {
        "engineer_login":  login,
        "engineer_name":   engineer_name,
        "org":             org_name,
        "lookback_days":   days,
        "ingested_at":     datetime.now(timezone.utc).isoformat(),
        "prs_authored":    prs_authored,
        "reviews_given":   reviews_given,
        "reviews_received": [],
        "summary_stats":   summary_stats,
    }

    output_path = output_dir / f"ingest_{login}.json"
    tmp_path    = output_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(profile, indent=2, default=str), encoding="utf-8")
    tmp_path.replace(output_path)   # replace() works on Windows; rename() does not

    enriched_count = sum(1 for p in prs_authored if "discussion_summary_stats" in p)
    insights_note  = f" + insights for {enriched_count} PRs" if args.pr_insights else ""
    print(f"> Written to {output_path}{insights_note}")


if __name__ == "__main__":
    main()
