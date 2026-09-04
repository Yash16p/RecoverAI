import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import './index.css'
import Dashboard from './pages/Dashboard'
import Cases from './pages/Cases'
import CaseDetail from './pages/CaseDetail'
import Storefront from './pages/Storefront'
import Benchmark from './pages/Benchmark'
import Nav from './components/Nav'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <div className="min-h-screen bg-gray-950 text-gray-100">
        <Nav />
        <Routes>
          <Route path="/"           element={<Dashboard />} />
          <Route path="/cases"      element={<Cases />} />
          <Route path="/cases/:id"  element={<CaseDetail />} />
          <Route path="/benchmark"  element={<Benchmark />} />
          <Route path="/store"      element={<Storefront />} />
        </Routes>
      </div>
    </BrowserRouter>
  </React.StrictMode>
)
