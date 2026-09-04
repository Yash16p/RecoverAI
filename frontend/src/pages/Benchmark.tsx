import { useEffect, useState } from 'react'
import { getBenchmark } from '../api'

const fmt = (p: number) => p != null ? `₹${Math.round(p/100).toLocaleString('en-IN')}` : '—'
const pct = (n: number) => n != null ? `${(n*100).toFixed(1)}%` : '—'
const lift = (n: number) => n != null ? `${n > 0 ? '+' : ''}${n.toFixed(1)}%` : '—'

export default function Benchmark() {
  const [data, setData] = useState<any>(null)

  useEffect(() => { getBenchmark().then(r => setData(r.data.benchmark)) }, [])

  if (!data) return <div className="p-8 text-gray-500">Loading…</div>

  const { fixed_retry: fr, oracle: or_, recoverai: ra } = data

  const rows = [
    { label: 'Recovery rate',        fr: pct(fr?.recovery_rate),  or: pct(or_?.recovery_rate),  ra: pct(ra?.recovery_rate) },
    { label: 'Gross recovered',       fr: fmt(fr?.gross_recovery_paise), or: fmt(or_?.gross_recovery_paise), ra: fmt(ra?.gross_recovery_paise) },
    { label: 'Intervention cost',     fr: fmt(fr?.total_cost_paise), or: fmt(or_?.total_cost_paise), ra: fmt(ra?.total_cost_paise) },
    { label: 'Net recovered',         fr: fmt(fr?.net_recovery_paise), or: fmt(or_?.net_recovery_paise), ra: fmt(ra?.net_recovery_paise) },
    { label: 'Natural recovery',      fr: fmt(fr?.natural_recovery_paise), or: fmt(or_?.natural_recovery_paise), ra: fmt(ra?.natural_recovery_paise) },
    { label: 'Incremental net',       fr: fmt(fr?.incremental_net_paise), or: fmt(or_?.incremental_net_paise), ra: fmt(ra?.incremental_net_paise) },
    { label: 'Incremental lift %',    fr: lift(fr?.incremental_lift_pct), or: lift(or_?.incremental_lift_pct), ra: lift(ra?.incremental_lift_pct) },
  ]

  return (
    <div className="p-8 max-w-4xl mx-auto">
      <h1 className="text-2xl font-bold mb-2">Benchmark Comparison</h1>
      <p className="text-gray-400 text-sm mb-8">
        100 held-out evaluation cases · seed=42 · ₹5.3L at risk
      </p>

      <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden mb-8">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-800 text-gray-400 text-xs">
              <th className="px-5 py-3 text-left">Metric</th>
              <th className="px-5 py-3 text-right">Natural (do nothing)</th>
              <th className="px-5 py-3 text-right">Fixed Retry</th>
              <th className="px-5 py-3 text-right text-emerald-400">RecoverAI</th>
              <th className="px-5 py-3 text-right text-gray-600">Oracle ceiling</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.label} className="border-b border-gray-800/50">
                <td className="px-5 py-3 text-gray-400">{r.label}</td>
                <td className="px-5 py-3 text-right text-gray-500">—</td>
                <td className="px-5 py-3 text-right text-gray-300">{r.fr}</td>
                <td className="px-5 py-3 text-right font-bold text-emerald-400">{r.ra}</td>
                <td className="px-5 py-3 text-right text-gray-600">{r.or}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {ra && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-5">
          <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wide mb-3">RecoverAI Action Distribution</h2>
          <div className="flex flex-wrap gap-3">
            {Object.entries(ra.action_distribution || {}).map(([action, count]) => (
              <div key={action} className="bg-gray-800 rounded-lg px-4 py-2 text-center">
                <div className="font-bold text-lg">{count as number}</div>
                <div className="text-gray-400 text-xs">{action.replace('_', ' ')}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
