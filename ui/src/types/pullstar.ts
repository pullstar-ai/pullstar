// pullstar.ts — TypeScript interfaces for PullStar 1-on-1

// ---------------------------------------------------------------------------
// Ingestion primitives
// ---------------------------------------------------------------------------

export interface PullRequest {
  number: number
  title: string
  url: string
  repository: string
  status: 'open' | 'merged' | 'closed'
  created_at: string        // ISO 8601
  merged_at: string | null  // ISO 8601
  lines_added: number
  lines_deleted: number
  files_changed: number
  commit_count: number
  description_length: number  // char count only — no raw content
  label_names: string[]
  base_branch: string
  reviews_received_count: number
}

export interface ReviewGiven {
  repository: string
  pr_number: number
  pr_title: string
  state: 'approved' | 'changes_requested' | 'commented'
  submitted_at: string  // ISO 8601
  body_length: number   // char count only — no raw content
}

export interface ReviewReceived {
  reviewer_login: string
  pr_number: number
  state: 'approved' | 'changes_requested' | 'commented'
  submitted_at: string  // ISO 8601
  body_length: number   // char count only — no raw content
}

// ---------------------------------------------------------------------------
// IngestedProfile — output of ingest.py
// ---------------------------------------------------------------------------

export interface SummaryStats {
  total_prs_authored: number
  prs_merged: number
  prs_open: number
  prs_closed_unmerged: number
  total_reviews_given: number
  total_reviews_received: number
  total_lines_added: number
  total_lines_deleted: number
  avg_pr_size_lines: number
  active_weeks: number
  total_weeks: number
}

export interface IngestedProfile {
  engineer_login: string
  engineer_name: string | null
  org: string
  lookback_days: number
  ingested_at: string  // ISO 8601
  prs_authored: PullRequest[]
  reviews_given: ReviewGiven[]
  reviews_received: ReviewReceived[]
  summary_stats: SummaryStats
}

// ---------------------------------------------------------------------------
// ScoredProfile — output of score.py
// Phase 1: velocity + pr_quality only
// ---------------------------------------------------------------------------

export interface DimensionScore {
  score: number
  max: number                         // always 20
  confidence: 'high' | 'medium' | 'low'
  signals: string[]                   // 1–3 human-readable observations
  flags: string[]                     // 0–2 anomaly strings
}

export interface Flag {
  severity: 'info' | 'caution' | 'notable'
  dimension: string
  message: string
}

export interface ScoredProfile {
  engineer_login: string
  scored_at: string       // ISO 8601
  lookback_days: number
  dimensions: {
    velocity:             DimensionScore
    pr_quality:           DimensionScore
    review_participation: DimensionScore
    collaboration:        DimensionScore
    consistency:          DimensionScore
  }
  total_score: number                 // 0–100
  confidence: 'high' | 'medium' | 'low'
  data_volume_note: string | null     // set when total_prs_authored < 5
  flags: Flag[]
}

// ---------------------------------------------------------------------------
// SkillOutput — the file the UI reads (.pullstar/output_{login}.json)
// ---------------------------------------------------------------------------

export interface SkillOutput {
  version: '1.0'
  generated_at: string    // ISO 8601
  engineer_login: string
  engineer_name: string | null
  org: string
  lookback_days: number
  scored_profile: ScoredProfile
  brief: string           // full markdown brief from AI
}
