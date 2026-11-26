import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { DmPage } from './dm/DmPage'
import { TablePage } from './table/TablePage'
import './index.css'

function Home() {
  return (
    <div className="flex min-h-full flex-col items-center justify-center gap-8 bg-[radial-gradient(ellipse_at_top,#1e293b_0%,#0b0d12_60%)] p-8 text-center">
      <svg viewBox="0 0 64 64" className="h-20 w-20">
        <polygon points="32,6 54.5,19 54.5,45 32,58 9.5,45 9.5,19" fill="none" stroke="#f59e0b" strokeWidth="4" />
        <circle cx="32" cy="32" r="6" fill="#f59e0b" />
      </svg>
      <div>
        <h1 className="text-3xl font-bold tracking-tight text-slate-100">Local-AI Tabletop VTT</h1>
        <p className="mt-2 text-slate-400">Open the display on the table monitor and the control panel on the DM's device.</p>
      </div>
      <div className="flex flex-wrap justify-center gap-4">
        <a href="/table" className="rounded-xl bg-amber-500 px-6 py-4 text-left text-slate-950 shadow-lg hover:bg-amber-400">
          <div className="text-lg font-semibold">/table</div>
          <div className="text-sm opacity-80">Fullscreen 4K tabletop display</div>
        </a>
        <a href="/dm" className="rounded-xl bg-white/5 px-6 py-4 text-left ring-1 ring-white/15 hover:bg-white/10">
          <div className="text-lg font-semibold text-slate-100">/dm</div>
          <div className="text-sm text-slate-400">DM control panel</div>
        </a>
      </div>
    </div>
  )
}

const path = window.location.pathname.replace(/\/+$/, '')
const Page = path.startsWith('/table') ? TablePage : path.startsWith('/dm') ? DmPage : Home

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Page />
  </StrictMode>,
)
