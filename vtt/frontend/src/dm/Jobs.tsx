import type { Job } from '../lib/types'
import { Panel, ProgressBar } from './ui'

const STAGES: Record<Job['kind'], { key: Job['stage']; label: string }[]> = {
  generate: [
    { key: 'prompting', label: 'Prompting' },
    { key: 'base_map', label: 'Base Map' },
    { key: 'animating', label: 'Animating' },
  ],
  animate: [
    { key: 'prompting', label: 'Vision prompt' },
    { key: 'animating', label: 'Animating' },
  ],
  token: [{ key: 'base_map', label: 'Portrait' }],
}
const ORDER: Job['stage'][] = ['queued', 'prompting', 'base_map', 'animating', 'saving', 'done']

function title(job: Job) {
  const i = job.input as { description?: string; title?: string; name?: string }
  return i.name ?? i.title ?? i.description ?? job.kind
}

export function Jobs({ jobs }: { jobs: Job[] }) {
  const visible = jobs.slice(0, 8)
  return (
    <Panel title="AI Jobs">
      {visible.length === 0 ? (
        <p className="text-sm text-slate-500">No jobs yet. Generate or import a map to see live progress here.</p>
      ) : (
        <ul className="space-y-3">
          {visible.map((job) => {
            const current = ORDER.indexOf(job.stage)
            const tone = job.status === 'error' ? 'red' : job.status === 'done' ? 'green' : 'amber'
            return (
              <li key={job.id} className="rounded-lg bg-slate-800/60 p-3 ring-1 ring-white/5">
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <span className="truncate text-sm text-slate-200" title={title(job)}>
                    {job.kind === 'animate' ? '🎞️' : job.kind === 'token' ? '◉' : '✦'} {title(job)}
                  </span>
                  <span
                    className={`shrink-0 text-xs font-medium ${
                      tone === 'red' ? 'text-red-400' : tone === 'green' ? 'text-emerald-400' : 'text-amber-300'
                    }`}
                  >
                    {job.status === 'running' ? `${Math.round(job.progress * 100)}%` : job.stage_label ?? job.status}
                  </span>
                </div>
                <ProgressBar value={job.status === 'error' ? 1 : job.progress} tone={tone} />
                <div className="mt-2 flex gap-1">
                  {STAGES[job.kind].map((s) => {
                    const idx = ORDER.indexOf(s.key)
                    const done = job.status === 'done' || current > idx
                    const active = job.status === 'running' && job.stage === s.key
                    return (
                      <span
                        key={s.key}
                        className={`flex-1 rounded px-1.5 py-0.5 text-center text-[10px] font-medium ${
                          active
                            ? 'bg-amber-500/25 text-amber-200'
                            : done
                              ? 'bg-emerald-500/15 text-emerald-300'
                              : 'bg-white/5 text-slate-500'
                        }`}
                      >
                        {s.label}
                      </span>
                    )
                  })}
                </div>
                {(job.error || job.message) && (
                  <p className={`mt-1.5 line-clamp-2 text-xs ${job.error ? 'text-red-400' : 'text-slate-500'}`}>
                    {job.error ?? job.message}
                  </p>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </Panel>
  )
}
