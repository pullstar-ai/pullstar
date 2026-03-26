import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import type { DimensionScore, Flag, SkillOutput } from './types/pullstar'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DIM_ORDER: Array<[keyof SkillOutput['scored_profile']['dimensions'], string]> = [
  ['velocity',             'Velocity'],
  ['pr_quality',           'PR Quality'],
  ['review_participation', 'Review Participation'],
  ['collaboration',        'Collaboration'],
  ['consistency',          'Consistency'],
]

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function barColor(pct: number) {
  if (pct >= 75) return 'bg-emerald-500'
  if (pct >= 50) return 'bg-amber-400'
  return 'bg-red-400'
}

function totalColor(pct: number) {
  if (pct >= 75) return 'text-emerald-600'
  if (pct >= 50) return 'text-amber-500'
  return 'text-red-500'
}

function confBadge(c: 'high' | 'medium' | 'low') {
  if (c === 'high')   return 'bg-emerald-50 text-emerald-700 ring-emerald-200'
  if (c === 'medium') return 'bg-amber-50 text-amber-700 ring-amber-200'
  return 'bg-orange-50 text-orange-700 ring-orange-200'
}

function flagDot(severity: Flag['severity']) {
  if (severity === 'notable') return 'bg-red-400'
  if (severity === 'caution') return 'bg-amber-400'
  return 'bg-sky-400'
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
  })
}

// ---------------------------------------------------------------------------
// ScoreBar — one dimension row
// ---------------------------------------------------------------------------

function ScoreBar({ label, dim }: { label: string; dim: DimensionScore }) {
  const pct = Math.round((dim.score / dim.max) * 100)
  return (
    <div className="py-3 border-b border-gray-50 last:border-0">
      <div
        className="grid items-center gap-3"
        style={{ gridTemplateColumns: '10.5rem 1fr 3.25rem 4.5rem' }}
      >
        <span className="text-sm text-gray-600 truncate">{label}</span>
        <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
          <div
            className={`h-full ${barColor(pct)} rounded-full`}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="text-right text-sm font-medium text-gray-700 tabular-nums">
          {dim.score}/{dim.max}
        </span>
        <span
          className={`text-center text-xs px-1.5 py-0.5 rounded-full ring-1 font-medium ${confBadge(dim.confidence)}`}
        >
          {dim.confidence}
        </span>
      </div>
      {dim.signals[0] && (
        <p
          className="mt-1 text-xs text-gray-400 leading-relaxed"
          style={{ paddingLeft: 'calc(10.5rem + 0.75rem)' }}
        >
          {dim.signals[0]}
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Empty / error / loading states
// ---------------------------------------------------------------------------

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center">
      <div className="text-center max-w-sm px-4">{children}</div>
    </div>
  )
}

function EmptyState() {
  return (
    <Shell>
      <h1 className="text-xl font-semibold text-gray-800 mb-3">PullStar 1-on-1</h1>
      <p className="text-sm text-gray-500 mb-2">
        Add a <code className="bg-gray-100 px-1 rounded">?login=</code> query param to load a brief.
      </p>
      <p className="text-sm text-gray-400 mb-5">
        Example: <code className="bg-gray-100 px-1 rounded">?login=jsmith</code>
      </p>
      <p className="text-xs text-gray-400">
        Run{' '}
        <code className="bg-gray-100 px-1 rounded">
          python scripts/generate_brief.py --login jsmith
        </code>{' '}
        first to generate the output file.
      </p>
    </Shell>
  )
}

function LoadingState({ login }: { login: string }) {
  return (
    <Shell>
      <p className="text-gray-500 text-sm">
        Loading brief for <strong>{login}</strong>…
      </p>
    </Shell>
  )
}

function ErrorState({ login, message }: { login: string; message: string }) {
  return (
    <Shell>
      <h1 className="text-xl font-semibold text-gray-800 mb-2">Brief not found</h1>
      <p className="text-sm text-gray-500 mb-3">
        No output file found for{' '}
        <code className="bg-gray-100 px-1 rounded">{login}</code>.
      </p>
      <p className="text-xs text-gray-400 mb-3">
        Run:{' '}
        <code className="bg-gray-100 px-1 rounded">
          python scripts/generate_brief.py --login {login}
        </code>
      </p>
      {message && <p className="text-xs text-red-400">{message}</p>}
    </Shell>
  )
}

// ---------------------------------------------------------------------------
// Main app
// ---------------------------------------------------------------------------

export default function App() {
  const login = new URLSearchParams(window.location.search).get('login')
  const [status, setStatus] = useState<'idle' | 'loading' | 'ok' | 'error'>(
    login ? 'loading' : 'idle',
  )
  const [data, setData] = useState<SkillOutput | null>(null)
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    if (!login) return
    setStatus('loading')
    fetch(`/api/pullstar/output_${login}.json`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json() as Promise<SkillOutput>
      })
      .then(json => { setData(json); setStatus('ok') })
      .catch(err => { setErrorMsg(String(err.message)); setStatus('error') })
  }, [login])

  if (status === 'idle')    return <EmptyState />
  if (status === 'loading') return <LoadingState login={login!} />
  if (status === 'error' || !data) return <ErrorState login={login ?? ''} message={errorMsg} />

  const { engineer_login, engineer_name, org, lookback_days, scored_profile, brief, generated_at } = data
  const { dimensions, total_score, confidence, data_volume_note, flags } = scored_profile
  const totalMax = 100
  const tPct     = Math.round((total_score / totalMax) * 100)

  // Build meta line: @login · org · Last N days
  const metaParts = [
    engineer_name ? `@${engineer_login}` : null,
    org || null,
    `Last ${lookback_days} days`,
  ].filter(Boolean)

  return (
    <div className="min-h-screen bg-gray-50">

      {/* Top bar */}
      <div className="border-b border-gray-200 bg-white">
        <div className="max-w-3xl mx-auto px-6 py-3 flex items-center justify-between">
          <span className="text-xs font-bold tracking-widest text-gray-400 uppercase">
            PullStar 1-on-1
          </span>
          <span className="text-xs text-gray-400">Generated {fmtDate(generated_at)}</span>
        </div>
      </div>

      <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">

        {/* Engineer identity */}
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">
            {engineer_name ?? engineer_login}
          </h1>
          <p className="mt-0.5 text-sm text-gray-500">{metaParts.join(' · ')}</p>
        </div>

        {/* Scoring card */}
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">

          {/* Total score header */}
          <div className="px-6 py-5 border-b border-gray-100 flex items-center justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-1.5">
                Overall Score
              </p>
              <div className="flex items-baseline gap-1.5">
                <span className={`text-4xl font-bold tabular-nums ${totalColor(tPct)}`}>
                  {total_score}
                </span>
                <span className="text-lg text-gray-300">/</span>
                <span className="text-lg text-gray-400">{totalMax}</span>
              </div>
            </div>
            <span
              className={`text-sm px-3 py-1 rounded-full ring-1 font-medium ${confBadge(confidence)}`}
            >
              {confidence} confidence
            </span>
          </div>

          {/* Dimension bars */}
          <div className="px-6 pt-1 pb-2">
            {DIM_ORDER.map(([key, label]) => {
              const dim = dimensions[key]
              return dim ? <ScoreBar key={key} label={label} dim={dim} /> : null
            })}
          </div>

          {/* Data note + flags */}
          {(data_volume_note || flags.length > 0) && (
            <div className="px-6 pb-5 pt-3 border-t border-gray-100 space-y-2.5">
              {data_volume_note && (
                <p className="text-xs text-orange-600 bg-orange-50 rounded-md px-3 py-2">
                  {data_volume_note}
                </p>
              )}
              {flags.map((f, i) => (
                <div key={i} className="flex gap-2.5 items-start">
                  <span className={`mt-1.5 w-1.5 h-1.5 rounded-full shrink-0 ${flagDot(f.severity)}`} />
                  <p className="text-xs text-gray-600 leading-relaxed">{f.message}</p>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Manager brief */}
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-100">
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
              Manager Brief
            </p>
          </div>
          <div className="px-6 py-6">
            <div className="brief-content">
              <ReactMarkdown>{brief}</ReactMarkdown>
            </div>
          </div>
        </div>

      </div>
    </div>
  )
}
