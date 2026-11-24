import { useEffect, useState } from "react";
import { api, Branch, Health, IngestJob } from "../api";
import { Badge, Button, Card, cx, ErrorBox, Field, inputCls, Spinner } from "../ui";

export default function Ingest({ branches, health, onChanged }: { branches: Branch[]; health: Health | null; onChanged: () => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [text, setText] = useState("");
  const [title, setTitle] = useState("notes.md");
  const [branch, setBranch] = useState("main");
  const [newBranch, setNewBranch] = useState("");
  const [isSession, setIsSession] = useState(false);
  const [day, setDay] = useState<string>("");
  const [job, setJob] = useState<IngestJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);

  useEffect(() => {
    if (!job || job.status === "complete" || job.status === "failed") {
      if (job) onChanged();
      return;
    }
    const t = setTimeout(() => api.job(job.id).then(setJob).catch(() => undefined), 700);
    return () => clearTimeout(t);
  }, [job, onChanged]);

  const target = newBranch.trim() || branch;
  const dayVal = day === "" ? null : +day;

  const submit = async () => {
    setError(null);
    try {
      if (files.length) setJob(await api.ingestFiles(files, target, isSession, dayVal));
      else setJob(await api.ingestText({ title, text, branch: target, is_session: isSession, in_world_day: dayVal }));
      setFiles([]);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const accept = [".md", ".markdown", ".txt", ".pdf", ".docx", ".html", ...(health?.docling ? [".pptx", ".odt"] : []), ...(health?.whisper ? ["audio/*"] : [])].join(",");

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card title="Ingest campaign lore" subtitle="Obsidian notes (frontmatter + [[wikilinks]]), PDFs, Word docs, session notes or audio.">
        <div
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); setFiles([...files, ...Array.from(e.dataTransfer.files)]); }}
          className={cx("rounded-lg border-2 border-dashed p-6 text-center text-sm transition", drag ? "border-ember-400 bg-ember-500/5" : "border-ink-700")}
        >
          <p className="text-ink-300">Drop files here or <label className="cursor-pointer text-ember-300 hover:text-ember-400">browse<input type="file" multiple accept={accept} className="hidden" onChange={(e) => setFiles([...files, ...Array.from(e.target.files ?? [])])} /></label></p>
          <p className="mt-1 text-xs text-ink-400">
            .md .txt .pdf .docx {health?.docling ? "· docling ✓" : "· (docling off: pypdf/python-docx fallback)"} {health?.whisper ? "· audio ✓" : "· (audio needs faster-whisper)"}
          </p>
          {!!files.length && (
            <ul className="mt-3 flex flex-wrap justify-center gap-1.5">
              {files.map((f, i) => <Badge key={i} tone="ember">{f.name} <button onClick={() => setFiles(files.filter((_, j) => j !== i))}>×</button></Badge>)}
            </ul>
          )}
        </div>

        {!files.length && (
          <div className="mt-4 space-y-3">
            <Field label="…or paste Markdown">
              <textarea className={cx(inputCls, "h-40 font-mono text-[13px]")} value={text} onChange={(e) => setText(e.target.value)}
                placeholder={"---\ntype: npc\nfaction: \"[[House Vane]]\"\nwants: [\"[[Greywater Docks]]\"]\n---\nOld Tamsin fears Sela Marrow."} />
            </Field>
            <Field label="Title"><input className={inputCls} value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
          </div>
        )}

        <div className="mt-4 grid grid-cols-2 gap-3">
          <Field label="Into branch">
            <select className={inputCls} value={branch} onChange={(e) => setBranch(e.target.value)}>
              {branches.filter((b) => !b.is_forecast).map((b) => <option key={b.id} value={b.name}>{b.name}</option>)}
            </select>
          </Field>
          <Field label="…or new branch" hint="Review the import before merging">
            <input className={inputCls} placeholder="ingest-session-15" value={newBranch} onChange={(e) => setNewBranch(e.target.value.replace(/[^a-zA-Z0-9_-]/g, "-"))} />
          </Field>
          <Field label="In-world day" hint="Blank = latest day on branch">
            <input type="number" className={inputCls} value={day} onChange={(e) => setDay(e.target.value)} />
          </Field>
          <label className="mt-6 flex items-center gap-2 text-sm text-ink-300">
            <input type="checkbox" className="accent-amber-500" checked={isSession} onChange={(e) => setIsSession(e.target.checked)} />
            These are session notes (stamp facts with the day)
          </label>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <Button variant="primary" disabled={!files.length && !text.trim()} onClick={submit}>Ingest → {target}</Button>
          <ErrorBox error={error} />
        </div>
      </Card>

      <Card title="Ingestion result" subtitle={job ? <>job <span className="font-mono">{job.id}</span> → {job.branch}</> : "Each file becomes one commit."}>
        {!job ? <p className="text-sm text-ink-400">Entity resolution merges “The Baron” / “Lord Vane” into one canonical node via the alias table and fuzzy matching.</p> : (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Badge tone={job.status === "complete" ? "green" : job.status === "failed" ? "red" : "ember"}>{job.status}</Badge>
              {job.status === "running" && <Spinner />}
            </div>
            <ErrorBox error={job.error} />
            {job.results.map((r) => (
              <div key={r.source} className="rounded-lg border border-ink-800 p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-ink-100">{r.source}</span>
                  <Badge tone="slate">{r.converter}</Badge>
                  <Badge>{r.chunks} chunks</Badge>
                  <Badge tone="green">+{r.created.length} nodes</Badge>
                  <Badge tone="blue">+{r.edges} edges</Badge>
                  <Badge tone="slate">day {r.in_world_day}</Badge>
                </div>
                {!!r.created.length && <div className="mt-2 text-xs text-ink-300">New: {r.created.map((c) => c.name).join(", ")}</div>}
                {!!r.aliases_merged.length && (
                  <div className="mt-1 text-xs text-ink-300">Resolved: {r.aliases_merged.map((a) => `“${a.surface}” → ${a.canonical}`).join("; ")}</div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
