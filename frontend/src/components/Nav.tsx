import { Link, useLocation } from 'react-router-dom'

const links = [
  { to: '/',          label: 'Dashboard' },
  { to: '/cases',     label: 'Cases' },
  { to: '/benchmark', label: 'Benchmark' },
  { to: '/store',     label: 'Demo Store' },
]

export default function Nav() {
  const { pathname } = useLocation()
  return (
    <nav className="border-b border-gray-800 bg-gray-900 px-6 py-3 flex items-center gap-8">
      <span className="text-emerald-400 font-bold text-lg tracking-tight">RecoverAI</span>
      <div className="flex gap-6">
        {links.map(l => (
          <Link
            key={l.to}
            to={l.to}
            className={`text-sm font-medium transition-colors ${
              pathname === l.to
                ? 'text-white'
                : 'text-gray-400 hover:text-gray-200'
            }`}
          >
            {l.label}
          </Link>
        ))}
      </div>
      <div className="ml-auto">
        <span className="text-xs text-gray-500 bg-gray-800 px-2 py-1 rounded">
          Test Mode
        </span>
      </div>
    </nav>
  )
}
