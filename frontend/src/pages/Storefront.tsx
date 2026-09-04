import { useState } from 'react'
import { Link } from 'react-router-dom'

const RAZORPAY_KEY = import.meta.env.VITE_RAZORPAY_KEY_ID || 'rzp_test_TU4qFW0Hjdrn1F'

declare global {
  interface Window { Razorpay: any }
}

const PRODUCTS = [
  { id: 1, name: 'RecoverAI Pro', price: 99000, desc: 'Annual subscription', icon: '\u26A1' },
  { id: 2, name: 'Analytics Add-on', price: 49900, desc: 'Monthly analytics', icon: '\uD83D\uDCCA' },
  { id: 3, name: 'Enterprise Plan', price: 499900, desc: 'Per seat/year', icon: '\uD83C\uDFE2' },
]

interface CaseInfo {
  case_ref: string
  case_id: number
  status: string
  action: string
}

export default function Storefront() {
  const [status, setStatus] = useState<Record<number, { type: string; message: string; caseInfo?: CaseInfo }>>({})
  const [loading, setLoading] = useState<number | null>(null)

  const loadRazorpay = () =>
    new Promise<boolean>(resolve => {
      if (window.Razorpay) return resolve(true)
      const s = document.createElement('script')
      s.src = 'https://checkout.razorpay.com/v1/checkout.js'
      s.onload = () => resolve(true)
      s.onerror = () => resolve(false)
      document.body.appendChild(s)
    })

  const pollForCase = async (checkoutId: string, productId: number, maxAttempts = 10) => {
    for (let i = 0; i < maxAttempts; i++) {
      await new Promise(r => setTimeout(r, 1500))
      try {
        const res = await fetch('/api/dashboard/cases?limit=20')
        const data = await res.json()
        const found = data.cases?.find((c: any) => 
          c.case_type === 'CHECKOUT_ABANDONMENT' && 
          c.status !== 'CREATED'
        )
        if (found) {
          setStatus(s => ({ ...s, [productId]: {
            type: 'success',
            message: 'Recovery case registered!',
            caseInfo: {
              case_ref: found.case_ref,
              case_id: found.id,
              status: found.status,
              action: found.recommended_action || 'Processing...'
            }
          }}))
          return
        }
      } catch (e) {
        console.error('Poll error:', e)
      }
    }
  }

  const handleBuy = async (product: typeof PRODUCTS[0]) => {
    setLoading(product.id)
    setStatus(s => ({ ...s, [product.id]: { type: 'loading', message: 'Creating order...' } }))
    
    const loaded = await loadRazorpay()
    if (!loaded) {
      setStatus(s => ({ ...s, [product.id]: { type: 'error', message: 'Failed to load Razorpay' } }))
      setLoading(null)
      return
    }

    try {
      const r = await fetch('/api/storefront/create-order', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ amount: product.price, product_name: product.name }),
      })
      const order = await r.json()

      const rzp = new window.Razorpay({
        key: RAZORPAY_KEY,
        amount: product.price,
        currency: 'INR',
        name: 'RecoverAI Store',
        description: product.name,
        order_id: order.id,
        prefill: { name: 'Demo Customer', email: 'demo@recoverai.test', contact: '9999999999' },
        theme: { color: '#10b981' },
        handler: (response: any) => {
          setStatus(s => ({ ...s, [product.id]: { type: 'success', message: 'Payment successful! ID: ' + response.razorpay_payment_id.slice(0,12) + '...' }}))
        },
        modal: { ondismiss: () => setStatus(s => ({ ...s, [product.id]: { type: 'warning', message: 'Checkout dismissed' }})) },
      })
      rzp.open()
    } catch (e: any) {
      setStatus(s => ({ ...s, [product.id]: { type: 'error', message: 'Error: ' + e.message } }))
    } finally {
      setLoading(null)
    }
  }

  const handleAbandon = async (product: typeof PRODUCTS[0]) => {
    setLoading(product.id)
    setStatus(s => ({ ...s, [product.id]: { type: 'loading', message: 'Simulating abandonment...' } }))
    
    try {
      const checkoutId = 'checkout_' + Date.now() + '_' + product.id
      const r = await fetch('/api/storefront/abandon', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ checkout_id: checkoutId, amount: product.price, product_name: product.name }),
      })
      const result = await r.json()
      
      if (result.queued) {
        setStatus(s => ({ ...s, [product.id]: { type: 'processing', message: 'Abandonment detected! RecoverAI is analyzing...' }}))
        pollForCase(checkoutId, product.id)
      } else {
        setStatus(s => ({ ...s, [product.id]: { type: 'warning', message: 'Already processed' }}))
      }
    } catch (e: any) {
      setStatus(s => ({ ...s, [product.id]: { type: 'error', message: 'Error: ' + e.message } }))
    } finally {
      setLoading(null)
    }
  }

  const getStyle = (t: string) => {
    if (t === 'success') return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
    if (t === 'error') return 'bg-red-500/20 text-red-400 border-red-500/30'
    if (t === 'processing') return 'bg-blue-500/20 text-blue-400 border-blue-500/30'
    if (t === 'loading') return 'bg-gray-500/20 text-gray-400 border-gray-500/30'
    return 'bg-yellow-500/20 text-yellow-400 border-yellow-500/30'
  }

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-950 to-gray-900">
      <div className="max-w-5xl mx-auto px-6 py-12">
        <div className="text-center mb-12">
          <div className="inline-flex items-center gap-2 bg-emerald-500/10 border border-emerald-500/20 rounded-full px-4 py-1.5 mb-6">
            <span className="w-2 h-2 bg-emerald-400 rounded-full animate-pulse" />
            <span className="text-emerald-400 text-sm font-medium">Test Mode Active</span>
          </div>
          <h1 className="text-4xl font-bold mb-3 bg-gradient-to-r from-white to-gray-400 bg-clip-text text-transparent">RecoverAI Demo Store</h1>
          <p className="text-gray-400 text-lg">Trigger real Razorpay events and watch RecoverAI recover revenue</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-12">
          {PRODUCTS.map(p => (
            <div key={p.id} className="group bg-gray-900/80 border border-gray-800 rounded-2xl p-6 hover:border-gray-700 transition-all">
              <div className="w-14 h-14 rounded-2xl bg-emerald-500/10 flex items-center justify-center mb-5 text-2xl">{p.icon}</div>
              <h2 className="font-bold text-xl mb-1">{p.name}</h2>
              <p className="text-gray-500 text-sm mb-5">{p.desc}</p>
              <div className="text-3xl font-bold text-white mb-6">Rs{(p.price / 100).toLocaleString('en-IN')}</div>
              <div className="space-y-3">
                <button onClick={() => handleBuy(p)} disabled={loading === p.id} className="w-full bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white font-semibold py-3 rounded-xl">
                  {loading === p.id ? '...' : 'Buy Now'}
                </button>
                <button onClick={() => handleAbandon(p)} disabled={loading === p.id} className="w-full border-2 border-dashed border-orange-500/30 hover:border-orange-500/50 text-orange-400 py-2.5 rounded-xl text-sm">
                  Simulate Abandonment
                </button>
              </div>
              
              {status[p.id] && (
                <div className={'mt-4 text-sm px-4 py-3 rounded-xl border ' + getStyle(status[p.id].type)}>
                  <div className="flex items-center gap-2">
                    {status[p.id].type === 'processing' && <span className="animate-spin">@</span>}
                    {status[p.id].type === 'success' && <span>V</span>}
                    <span>{status[p.id].message}</span>
                  </div>
                  
                  {status[p.id].caseInfo && (
                    <div className="mt-3 pt-3 border-t border-emerald-500/20">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-xs text-gray-400">Case ID</span>
                        <Link to={'/cases/' + status[p.id].caseInfo!.case_id} className="font-mono text-emerald-400 hover:underline">
                          {status[p.id].caseInfo!.case_ref}
                        </Link>
                      </div>
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-xs text-gray-400">Status</span>
                        <span className="text-xs px-2 py-0.5 rounded bg-blue-500/20 text-blue-400">{status[p.id].caseInfo!.status}</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-gray-400">Action</span>
                        <span className="text-xs text-white">{status[p.id].caseInfo!.action}</span>
                      </div>
                      <Link to="/cases" className="block mt-3 text-center text-xs text-emerald-400 hover:text-emerald-300">
                        View all cases -&gt;
                      </Link>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="bg-gray-900/50 border border-gray-800 rounded-2xl p-6">
          <h3 className="font-bold text-lg mb-4">How to Demo</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 text-sm text-gray-400">
            <div>
              <h4 className="font-semibold text-emerald-400 mb-2">Successful Payment</h4>
              <p>Card: 4111 1111 1111 1111 / any expiry / any CVV</p>
            </div>
            <div>
              <h4 className="font-semibold text-red-400 mb-2">Failed Payment</h4>
              <p>Card: 4000 0000 0000 0002 - triggers recovery case</p>
            </div>
            <div className="md:col-span-2">
              <h4 className="font-semibold text-orange-400 mb-2">Checkout Abandonment</h4>
              <p>Click Simulate Abandonment - creates CHECKOUT_ABANDONMENT recovery case with real-time status updates</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
