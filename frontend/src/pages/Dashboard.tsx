import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getSummary, getCases } from '../api'

const fmt = (paise: number) => 'Rs' + (paise / 100).toLocaleString('en-IN', { maximumFractionDigits: 0 })
const pct = (n: number) => (n * 100).toFixed(1) + '%'

const STATUS_COLOR: Record<string, string> = {
  RECOVERED: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
  POLICY_APPROVED: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  EXECUTED: 'bg-violet-500/20 text-violet-400 border-violet-500/30',
  STOPPED: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
  ESCALATED: 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30',
  CREATED: 'bg-gray-700/40 text-gray-300 border-gray-600/30',
}

const TYPE_ICON: Record<string, string> = {
  PAYMENT_FAILURE: String.fromCodePoint(0x1F4B3),
  CHECKOUT_ABANDONMENT: String.fromCodePoint(0x1F6D2),
  SUBSCRIPTION_FAILURE: String.fromCodePoint(0x1F504),
  OVERDUE_RECEIVABLE: String.fromCodePoint(0x1F4C4),
}

export default function Dashboard() {
  const [data, setData] = useState<any>(null)
  const [cases, setCases] = useState<any[]>([])

  useEffect(() => {
    getSummary().then(r => setData(r.data))
    getCases({ limit: 10 }).then(r => setCases(r.data.cases || []))
  }, [])

  if (!data) return <div className="flex items-center justify-center h-96 text-gray-500">Loading...</div>

  const ov = data.overview

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-950 to-gray-900">
      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold bg-gradient-to-r from-white to-gray-400 bg-clip-text text-transparent">Revenue Recovery Control Plane</h1>
          <p className="text-gray-500 mt-1">Razorpay AI Buildathon 2026 - Track 03</p>
        </div>

        {/* Hero Stats */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <div className="bg-gradient-to-br from-red-500/10 to-red-600/5 border border-red-500/20 rounded-2xl p-5">
            <div className="text-red-400/70 text-xs uppercase tracking-wider mb-1">Revenue at Risk</div>
            <div className="text-2xl font-bold text-red-400">{fmt(ov.total_at_risk_paise)}</div>
            <div className="text-gray-600 text-xs mt-1">{ov.total_cases} cases</div>
          </div>
          <div className="bg-gradient-to-br from-emerald-500/10 to-emerald-600/5 border border-emerald-500/20 rounded-2xl p-5">
            <div className="text-emerald-400/70 text-xs uppercase tracking-wider mb-1">Gross Recovered</div>
            <div className="text-2xl font-bold text-emerald-400">{fmt(ov.gross_recovered_paise)}</div>
            <div className="text-gray-600 text-xs mt-1">{ov.recovered_cases} cases recovered</div>
          </div>
          <div className="bg-gradient-to-br from-blue-500/10 to-blue-600/5 border border-blue-500/20 rounded-2xl p-5">
            <div className="text-blue-400/70 text-xs uppercase tracking-wider mb-1">Expected Net</div>
            <div className="text-2xl font-bold text-blue-400">{fmt(ov.expected_net_recovery_paise)}</div>
            <div className="text-gray-600 text-xs mt-1">After intervention costs</div>
          </div>
          <div className="bg-gradient-to-br from-violet-500/10 to-violet-600/5 border border-violet-500/20 rounded-2xl p-5">
            <div className="text-violet-400/70 text-xs uppercase tracking-wider mb-1">Recovery Rate</div>
            <div className="text-2xl font-bold text-violet-400">{pct(ov.recovery_rate)}</div>
            <div className="text-gray-600 text-xs mt-1">{ov.recovered_cases}/{ov.total_cases} cases</div>
          </div>
        </div>

        {/* Pipeline Status */}
        <div className="bg-gray-900/50 border border-gray-800 rounded-2xl p-5 mb-8">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wider mb-4">Pipeline Status</h2>
          <div className="flex flex-wrap gap-3">
            {[
              { label: 'Created', value: ov.total_cases - ov.recovered_cases - ov.stopped_cases - ov.escalated_cases, color: 'gray' },
              { label: 'Approved', value: ov.approved_cases || 0, color: 'blue' },
              { label: 'Executed', value: ov.executed_cases || 0, color: 'violet' },
              { label: 'Recovered', value: ov.recovered_cases, color: 'emerald' },
              { label: 'Stopped', value: ov.stopped_cases, color: 'gray' },
              { label: 'Escalated', value: ov.escalated_cases, color: 'yellow' },
            ].map(s => (
              <div key={s.label} className={'flex items-center gap-2 px-4 py-2 rounded-lg border bg-' + s.color + '-500/10 border-' + s.color + '-500/20'}>
                <span className={'text-lg font-bold text-' + s.color + '-400'}>{s.value}</span>
                <span className="text-gray-400 text-sm">{s.label}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-8">
          {/* Revenue Surfaces */}
          {data.by_surface?.length > 0 && (
            <div className="lg:col-span-1 bg-gray-900/50 border border-gray-800 rounded-2xl p-5">
              <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wider mb-4">Revenue Surfaces</h2>
              <div className="space-y-3">
                {data.by_surface.map((s: any) => (
                  <div key={s.surface} className="flex items-center justify-between p-3 bg-gray-800/30 rounded-xl">
                    <div className="flex items-center gap-3">
                      <span className="text-xl">{TYPE_ICON[s.surface] || String.fromCodePoint(0x1F4B0)}</span>
                      <div>
                        <div className="font-medium text-sm text-white">{s.surface.replace(/_/g, ' ')}</div>
                        <div className="text-gray-500 text-xs">{s.count} cases</div>
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="font-bold text-emerald-400">{fmt(s.at_risk_paise)}</div>
                      <div className="text-gray-500 text-xs">{s.recovered} recovered</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Recent Cases */}
          <div className="lg:col-span-2 bg-gray-900/50 border border-gray-800 rounded-2xl p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wider">Recent Cases</h2>
              <Link to="/cases" className="text-xs text-emerald-400 hover:text-emerald-300 transition-colors">View all</Link>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="text-gray-500 text-xs border-b border-gray-800">
                    <th className="text-left py-2 font-medium">Case</th>
                    <th className="text-left py-2 font-medium">Type</th>
                    <th className="text-right py-2 font-medium">Amount</th>
                    <th className="text-left py-2 font-medium">Action</th>
                    <th className="text-left py-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {cases.slice(0, 8).map((c: any) => (
                    <tr key={c.id} className="border-b border-gray-800/50 hover:bg-gray-800/20 transition-colors">
                      <td className="py-3">
                        <Link to={'/cases/' + c.id} className="text-emerald-400 hover:text-emerald-300 font-mono text-xs">{c.case_ref}</Link>
                      </td>
                      <td className="py-3">
                        <span className="text-gray-400 text-xs">{(TYPE_ICON[c.case_type] || '') + ' ' + (c.case_type?.replace(/_/g, ' ') || '')}</span>
                      </td>
                      <td className="py-3 text-right font-medium text-white">{fmt(c.amount || 0)}</td>
                      <td className="py-3 text-xs text-gray-300">{c.recommended_action || '-'}</td>
                      <td className="py-3">
                        <span className={'text-xs px-2 py-1 rounded-lg border ' + (STATUS_COLOR[c.status] || 'bg-gray-700 text-gray-300')}>{c.status}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
