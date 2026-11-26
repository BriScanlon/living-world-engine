import { useState } from 'react'
import { api } from '../lib/api'
import type { LogEntry } from '../lib/types'
import { Button, Panel } from './ui'

const ICON: Record<LogEntry['kind'], string> = { note: '✎', map: '▦', scene: '⌂' }

function clock(at: number) {
  return new Date(at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

/**
 * What happened at the table this session. Maps shown and world scenes visited are logged automatically;
 * the DM adds notes as play goes. The Living World Engine reads it to pre-fill the next forecast.
 */
export function SessionLog({ entries, toast }: { entries: LogEntry[]; toast: (m: string, l?: 'info' | 'warn' | 'error' | 'success') => void }) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  const add = async () => {
    const t = text.trim()
    if (!t) return
    setBusy(true)
    try {
      await api.addNote(t)
      setText('')
    } catch (e) {
      toast(`Couldn't save the note: ${(e as Error).message}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  const newSession = async () => {
    if (!confirm('Start a new session? This clears the log (pull it into the world engine first).')) return
    await api.newSession().catch((e) => toast((e as Error).message, 'error'))
  }

  return (
    <Panel
      title="Session Log"
      actions={entries.length > 0 && <Button variant="subtle" className="!px-2 !py-0.5 text-xs" onClick={newSession}>New session</Button>}
    >
      <div className="space-y-3 text-sm">
        <div className="flex gap-2">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) add()
            }}
            rows={2}
            maxLength={2000}
            placeholder="What just happened? e.g. The party sank the Guild's barge"
            className="min-w-0 flex-1 resize-y rounded-lg bg-slate-800 px-2.5 py-1.5 text-sm text-slate-100 ring-1 ring-white/10 placeholder:text-slate-500 focus:outline-none focus:ring-amber-400/60"
          />
          <Button variant="primary" disabled={busy || !text.trim()} onClick={add} title="Add note (Ctrl+Enter)">Add</Button>
        </div>
        {entries.length === 0 ? (
          <p className="text-slate-500">
            Nothing yet. Maps you show and world scenes you open are logged here automatically. The world engine's
            Session Forecaster can pull this log into the next forecast.
          </p>
        ) : (
          <ol className="max-h-80 space-y-1.5 overflow-y-auto pr-1">
            {entries.slice().reverse().map((e) => (
              <li key={e.id} className="group flex gap-2">
                <span className="w-10 shrink-0 font-mono text-[11px] leading-5 text-slate-500">{clock(e.at)}</span>
                <span className={`shrink-0 leading-5 ${e.kind === 'note' ? 'text-amber-300' : 'text-slate-500'}`}>{ICON[e.kind] ?? '·'}</span>
                <span className={`min-w-0 flex-1 whitespace-pre-wrap leading-5 ${e.kind === 'note' ? 'text-slate-100' : 'text-slate-400'}`}>{e.text}</span>
                <button
                  className="shrink-0 text-xs text-slate-600 opacity-0 hover:text-red-400 group-hover:opacity-100"
                  title="Remove"
                  onClick={() => api.deleteLogEntry(e.id).catch((err) => toast((err as Error).message, 'error'))}
                >
                  ✕
                </button>
              </li>
            ))}
          </ol>
        )}
      </div>
    </Panel>
  )
}
