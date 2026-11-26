import type { AppConfig, Asset, Job, LogEntry } from './types'

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    } catch {
      /* ignore */
    }
    throw new Error(detail || `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  config: () => fetch('/api/config').then((r) => json<AppConfig>(r)),
  assets: (type: 'map' | 'token' = 'map') => fetch(`/api/maps?type=${type}`).then((r) => json<Asset[]>(r)),
  push: (id: string) => fetch(`/api/maps/${id}/push`, { method: 'POST' }).then((r) => json(r)),
  clearTable: () => fetch('/api/table/clear', { method: 'POST' }).then((r) => json(r)),
  rename: (id: string, title: string) =>
    fetch(`/api/maps/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    }).then((r) => json<Asset>(r)),
  remove: (id: string) => fetch(`/api/maps/${id}`, { method: 'DELETE' }).then((r) => json(r)),
  generate: (body: { description: string; style: string; animate: boolean; push: boolean }) =>
    fetch('/api/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then((r) => json<Job>(r)),
  addNote: (text: string) =>
    fetch('/api/session-log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }).then((r) => json<LogEntry>(r)),
  deleteLogEntry: (id: number) => fetch(`/api/session-log/${id}`, { method: 'DELETE' }).then((r) => json(r)),
  newSession: () => fetch('/api/session-log', { method: 'DELETE' }).then((r) => json(r)),
}

/** Multipart upload with progress (4K PNGs are 10-30 MB). */
export function uploadMap(
  file: File,
  opts: { mode: 'display' | 'animate'; push: boolean },
  onProgress: (fraction: number) => void,
): Promise<{ queued: boolean; asset?: Asset; job?: Job }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const form = new FormData()
    form.append('file', file)
    form.append('mode', opts.mode)
    form.append('push', String(opts.push))
    xhr.open('POST', '/api/upload')
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total)
    xhr.onload = () => {
      try {
        const body = JSON.parse(xhr.responseText)
        if (xhr.status >= 400) reject(new Error(body.detail ?? `HTTP ${xhr.status}`))
        else resolve(body)
      } catch (e) {
        reject(e)
      }
    }
    xhr.onerror = () => reject(new Error('Upload failed'))
    xhr.send(form)
  })
}
