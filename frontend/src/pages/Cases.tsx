import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getCases } from '../api'

const fmt = (p: number) => `₹${(p/100).toLocaleString('en-IN')}`

const STATUS_COLOR: Record<string, string> = {
  RECOVERED:       'bg-emerald-500/20 text-emerald-400',
  POLICY_APPROVED: 'bg-blue-500/20 text-blue-400',
  EXECUTED:        'bg-violet-500/20 text-violet-400',
  STOPPED:         'bg-gray-500/20 text-gray-400',
  ESCALATED:       'bg-yellow-500/20 text-yellow-400',
  CREATED:         'bg-gray-700/40 text-gray-300',
}

export default function Cases() {
  const [data, setData] = useState<any>(null)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState('')

  useEffect(() => {
    getCases({ page, limit: 20, status: status || undefined })
      .then(r => setData(r.data))
  }, [page, status])

  return (
    <div className="p-8 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Recovery Cases</h1>
        <select
          value={status}
          onChange={e => { setStatus(e.target.value); setPage(1) }}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-sm"
        >
          <option value="">All statuses</option>
          {['CREATED','POLICY_APPROVED','EXECUTED','RECOVERED','STOPPED','ESCALATED'].map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>

      {!data ? (
        <div className="text-gray-500">Loading…</div>
      ) : (
        <>
          <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden mb-4">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-gray-500 text-xs">
                  <th className="px-4 py-3 text-left">Ref</th>
                  <th className="px-4 py-3 text-left">Type</th>
                  <th className="px-4 py-3 text-right">Amount</th>
                  <th className="px-4 py-3 text-left">Failure</th>
                  <th className="px-4 py-3 text-left">Action</th>
                  <th className="px-4 py-3 text-right">P0</th>
                  <th className="px-4 py-3 text-right">Net Rec.</th>
                  <th className="px-4 py-3 text-left">Status</th>
                  <th className="px-4 py-3 text-right">Attempts</th>
                </tr>
              </thead>
              <tbody>
                {data.cases.map((c: any) => (
                  <tr key={c.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="px-4 py-3">
                      <Link to={`/cases/${c.id}`} className="text-emerald-400 hover:underline font-mono text-xs">
                        {c.case_ref}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-gray-400 text-xs">{c.case_type?.replace('_', ' ')}</td>
                    <td className="px-4 py-3 text-right font-medium">{fmt(c.amount || 0)}</td>
                    <td className="px-4 py-3 text-gray-400 text-xs">{c.failure_reason || '—'}</td>
                    <td className="px-4 py-3 text-xs">{c.recommended_action || '—'}</td>
                    <td className="px-4 py-3 text-right text-gray-400 text-xs">
                      {c.p0 ? `${(c.p0 * 100).toFixed(0)}%` : '—'}
                    </td>
                    <td className="px-4 py-3 text-right text-emerald-400 text-xs font-medium">
                      {c.expected_net_recovery ? fmt(c.expected_net_recovery) : '—'}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`text-xs px-2 py-0.5 rounded-full ${STATUS_COLOR[c.status] || 'bg-gray-700 text-gray-300'}`}>
                        {c.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right text-gray-400 text-xs">{c.attempt_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-between text-sm text-gray-500">
            <span>{data.total} total cases</span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page === 1}
                className="px-3 py-1 rounded bg-gray-800 disabled:opacity-40"
              >← Prev</button>
              <span className="px-3 py-1">Page {page}</span>
              <button
                onClick={() => setPage(p => p + 1)}
                disabled={page * 20 >= data.total}
                className="px-3 py-1 rounded bg-gray-800 disabled:opacity-40"
              >Next →</button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
