import { useRef, useState } from 'react'
import { uploadMap } from '../lib/api'
import { Panel, ProgressBar, Segmented, Toggle } from './ui'

const ACCEPT = '.png,.webp,.jpg,.jpeg,.mp4,.webm'

export function Importer({ toast, watchInbox }: { toast: (m: string, l?: 'info' | 'warn' | 'error' | 'success') => void; watchInbox: boolean }) {
  const [mode, setMode] = useState<'display' | 'animate'>('display')
  const [push, setPush] = useState(true)
  const [drag, setDrag] = useState(false)
  const [uploads, setUploads] = useState<{ name: string; progress: number }[]>([])
  const input = useRef<HTMLInputElement>(null)

  const handle = async (files: FileList | null) => {
    if (!files) return
    for (const file of Array.from(files)) {
      const isVideo = /\.(mp4|webm)$/i.test(file.name)
      const effectiveMode = isVideo ? 'display' : mode
      setUploads((u) => [...u, { name: file.name, progress: 0 }])
      try {
        const res = await uploadMap(file, { mode: effectiveMode, push }, (p) =>
          setUploads((u) => u.map((x) => (x.name === file.name ? { ...x, progress: p } : x))),
        )
        toast(res.queued ? `Queued “${file.name}” for AI animation` : `Imported “${file.name}”`, 'success')
      } catch (e) {
        toast(`Upload failed: ${(e as Error).message}`, 'error')
      } finally {
        setUploads((u) => u.filter((x) => x.name !== file.name))
      }
    }
  }

  return (
    <Panel title="Import Static Map (Inkarnate etc.)">
      <div className="space-y-3">
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setDrag(true)
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDrag(false)
            void handle(e.dataTransfer.files)
          }}
          onClick={() => input.current?.click()}
          className={`flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-6 text-center transition-colors ${
            drag ? 'border-amber-400 bg-amber-400/10' : 'border-white/15 hover:border-white/30'
          }`}
        >
          <span className="text-2xl">⇪</span>
          <span className="mt-1 text-sm text-slate-300">Drop 4K gridless PNG / WebP here, or click to browse</span>
          <span className="text-xs text-slate-500">MP4/WebM loops are imported as-is</span>
          <input ref={input} type="file" accept={ACCEPT} multiple hidden onChange={(e) => void handle(e.target.files)} />
        </div>
        <Segmented
          value={mode}
          onChange={setMode}
          options={[
            { value: 'display', label: 'Display now (+ Ambient FX)' },
            { value: 'animate', label: 'Queue AI animation' },
          ]}
        />
        <Toggle label={mode === 'display' ? 'Push to table immediately' : 'Push to table when animated'} checked={push} onChange={setPush} />
        {uploads.map((u) => (
          <div key={u.name} className="space-y-1">
            <div className="truncate text-xs text-slate-400">Uploading {u.name}</div>
            <ProgressBar value={u.progress} />
          </div>
        ))}
        {watchInbox && (
          <p className="text-xs text-slate-500">
            Tip: files dropped into the server's <span className="font-mono text-slate-400">data/inbox/</span> folder are
            animated and imported automatically.
          </p>
        )}
      </div>
    </Panel>
  )
}
