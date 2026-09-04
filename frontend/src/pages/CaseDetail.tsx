import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { getCase, getTimeline } from '../api'

const fmt = (p: number) => `₹${(p/100).toLocaleString('en-IN')}`

export default function CaseDetail() {
  const { id } = useParams<{ id: string }>()
  const [data, setData]         = useState<any>(null)
  const [timeline, setTimeline] = useState<any>(null)

  useEffect(() => {
    if (!id) return
    getCase(Number(id)).then(r => setData(r.data))
    getTimeline(Number(id)).then(r => setTimeline(r.data))
  }, [id])

  if (!data) return <div className="p-8 text-gray-500">Loading…</div>

  const c = data.case
  const attempts = data.attempts || []

  return (
    <div className="p-8 max-w-5xl mx-auto">
      <Link to="/cases" className="text-gray-500 hover:text-gray-300 text-sm mb-4 block">← Cases</Link>

      {/* Header */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 mb-6">
        <div className="flex items-start justify-between">
          <div>
            <div className="font-mono text-xl font-bold text-emerald-400">{c.case_ref}</div>
            <div className="text-gray-400 text-sm mt-1">{c.case_type?.replace('_', ' ')} · {fmt(c.amount || 0)}</div>
            <div className="text-gray-500 text-xs mt-1">Failure: {c.failure_reason}</div>
          </div>
          <span className="text-sm px-3 py-1 rounded-full bg-emerald-500/20 text-emerald-400 font-medium">
            {c.status}
          </span>
        </div>

        {/* Economic snapshot */}
        <div className="grid grid-cols-4 gap-4 mt-5 pt-5 border-t border-gray-800">
          <div>
            <div className="text-gray-500 text-xs">P(no-action)</div>
            <div className="font-bold text-lg">{c.p0 ? `${(c.p0*100).toFixed(0)}%` : '—'}</div>
          </div>
          <div>
            <div className="text-gray-500 text-xs">P({c.recommended_action})</div>
            <div className="font-bold text-lg text-emerald-400">
              {c.recommended_action_pa ? `${(c.recommended_action_pa*100).toFixed(0)}%` : '—'}
            </div>
          </div>
          <div>
            <div className="text-gray-500 text-xs">Expected Net</div>
            <div className="font-bold text-lg text-emerald-400">
              {c.expected_net_recovery ? fmt(c.expected_net_recovery) : '—'}
            </div>
          </div>
          <div>
            <div className="text-gray-500 text-xs">Attempts</div>
            <div className="font-bold text-lg">{c.attempt_count}</div>
          </div>
        </div>
      </div>

      {/* Attempts */}
      {attempts.length > 0 && (
        <div className="mb-6">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">Recovery Attempts</h2>
          {attempts.map((a: any) => {
            const trace = a.orchestrator_trace || {}
            return (
              <div key={a.id} className="bg-gray-900 border border-gray-800 rounded-xl p-5 mb-3">
                <div className="flex items-center justify-between mb-3">
                  <span className="font-medium">Attempt #{a.attempt_number} — {a.action}</span>
                  <div className="flex gap-2 text-xs">
                    <span className={`px-2 py-0.5 rounded-full ${a.policy_decision === 'APPROVED' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'}`}>
                      {a.policy_decision}
                    </span>
                    <span className="px-2 py-0.5 rounded-full bg-gray-700 text-gray-300">
                      {a.execution_status}
                    </span>
                    {a.outcome && (
                      <span className={`px-2 py-0.5 rounded-full ${a.outcome === 'PAID' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-red-500/20 text-red-400'}`}>
                        {a.outcome}
                      </span>
                    )}
                  </div>
                </div>

                <div className="grid grid-cols-3 gap-3 text-xs mb-3">
                  <div><span className="text-gray-500">P0:</span> {a.p0_at_decision ? `${(a.p0_at_decision*100).toFixed(0)}%` : '—'}</div>
                  <div><span className="text-gray-500">Pa:</span> {a.pa_at_decision ? `${(a.pa_at_decision*100).toFixed(0)}%` : '—'}</div>
                  <div><span className="text-gray-500">Net:</span> {a.expected_net_recovery_at_decision ? fmt(a.expected_net_recovery_at_decision) : '—'}</div>
                </div>

                {trace.context_summary && (
                  <div className="text-gray-400 text-xs mb-2">
                    <span className="text-gray-600">Diagnosis:</span> {trace.context_summary}
                  </div>
                )}
                {trace.llm_reasoning && (
                  <div className="text-gray-300 text-xs bg-gray-800 rounded-lg p-3">
                    <span className="text-gray-500">LLM reasoning:</span> {trace.llm_reasoning}
                  </div>
                )}
                {trace.blocked_actions?.length > 0 && (
                  <div className="mt-2 text-xs">
                    {trace.blocked_actions.map((b: any) => (
                      <span key={b.action} className="mr-2 px-2 py-0.5 rounded bg-red-900/30 text-red-400">
                        {b.action} blocked: {b.reason}
                      </span>
                    ))}
                  </div>
                )}
                {a.razorpay_reference && (
                  <div className="mt-2 text-xs text-gray-500">
                    Razorpay: <span className="font-mono text-gray-300">{a.razorpay_reference}</span>
                  </div>
                )}
                {a.freshness_checked_at && (
                  <div className="mt-1 text-xs text-gray-600">
                    Freshness check: {a.freshness_passed ? '✔ passed' : '✘ failed'}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Timeline */}
      {timeline && (
        <div>
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">Audit Timeline</h2>
          <div className="relative pl-4 border-l border-gray-800">
            {timeline.timeline?.map((ev: any, i: number) => (
              <div key={i} className="mb-4 relative">
                <div className="absolute -left-[17px] w-2 h-2 rounded-full bg-gray-600 mt-1" />
                <div className="text-gray-500 text-xs mb-0.5">{ev.ts?.replace('T', ' ').slice(0, 19)}</div>
                <div className="font-medium text-xs text-emerald-400">{ev.event}</div>
                <div className="text-gray-400 text-xs mt-0.5">
                  {typeof ev.detail === 'string' ? ev.detail : JSON.stringify(ev.detail).slice(0, 200)}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
