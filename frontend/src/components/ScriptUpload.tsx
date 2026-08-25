import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Project, ScriptTemplate } from "../types";
import { DownloadIcon, UploadIcon } from "./Icon";

const ACCEPT = ".txt,.md,.markdown,.text";

/** Add a script by dropping a file, browsing for one, or pasting.
 *
 *  All three land on the same ingestion, so a marker stripped one way is
 *  stripped every way. The server validates extension, size and that the bytes
 *  really are UTF-8 — an extension proves nothing. */
export function ScriptUpload({
  projects,
  preselect,
  onCreated,
}: {
  projects: Project[];
  preselect: number | null;
  onCreated: (scriptId: number) => void;
}) {
  const input = useRef<HTMLInputElement | null>(null);
  const [over, setOver] = useState(false);
  const [pasting, setPasting] = useState(false);
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const projectId = preselect ?? projects[0]?.id ?? null;

  if (!projects.length) {
    return (
      <div className="empty">
        <strong>No projects yet</strong>
        Create one above, then add a script to it.
      </div>
    );
  }

  function report(created: { id: number; chunks: number; slots: number; warnings: string[] }) {
    setResult(
      `${created.chunks} chunk${created.chunks === 1 ? "" : "s"}` +
        (created.slots ? ` · ${created.slots} effect slot${created.slots === 1 ? "" : "s"}` : "") +
        (created.warnings.length ? ` · ${created.warnings.join("; ")}` : ""),
    );
    onCreated(created.id);
  }

  async function send(file: File) {
    if (projectId === null) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      report(await api.uploadScript(projectId, file, title || undefined));
      setTitle("");
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ""));
    } finally {
      setBusy(false);
    }
  }

  async function sendPasted() {
    if (projectId === null || !text.trim()) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      report(
        await api.createScript({
          project_id: projectId,
          title: title || "Untitled",
          text,
        }),
      );
      setText("");
      setTitle("");
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ""));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="field">
        <label>Project</label>
        <select
          value={projectId ?? ""}
          onChange={(e) => onCreated(-Number(e.target.value))}
          disabled
          title="Scripts are added to the selected project"
        >
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label>Title (optional — the filename is used otherwise)</label>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Episode 14" />
      </div>

      {!pasting ? (
        <>
          <div
            className={`dropzone${over ? " over" : ""}`}
            onClick={() => input.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setOver(false);
              const file = e.dataTransfer.files?.[0];
              if (file) void send(file);
            }}
          >
            <UploadIcon />
            <strong>{busy ? "Reading…" : "Drop a script here"}</strong>
            <span className="hint">.txt · .md · .markdown — or click to browse</span>
          </div>
          <input
            ref={input}
            type="file"
            accept={ACCEPT}
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void send(file);
              e.target.value = "";
            }}
          />
          <button className="ghost small paste-toggle" onClick={() => setPasting(true)}>
            or paste the text instead
          </button>
        </>
      ) : (
        <>
          <div className="field">
            <label>Script</label>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={
                "Paste the script.\n\n[SFX: wind howling, 4s] marks an effect.\n[@ 02:15] marks a target time."
              }
            />
          </div>
          <div className="toolbar">
            <button className="primary" disabled={busy || !text.trim()} onClick={sendPasted}>
              Add script
            </button>
            <button className="ghost small" onClick={() => setPasting(false)}>
              upload a file instead
            </button>
          </div>
        </>
      )}

      {result && <p className="note ok">Added — {result}</p>}
      {error && <p className="note error">{error}</p>}

      <TemplateLinks />

      <p className="note">
        <code>[SFX: description, 4s]</code> places an effect; <code>[@ 02:15]</code> marks a
        target time. Both are stripped before anything is sent, so they are never spoken or
        billed.
      </p>
    </>
  );
}

/** Starter scripts, offered where somebody is about to write one.
 *
 *  Each is annotated in HTML comments, which the parser strips before anything
 *  is sent — so a template explains itself *and* generates correctly with every
 *  comment left in place. Nothing has to be deleted first, which is the whole
 *  reason it is worth offering rather than documenting.
 *
 *  Silent if the list cannot be fetched: a starter script is a convenience, and
 *  an error about one has no business sitting above the upload box that works. */
function TemplateLinks() {
  const [templates, setTemplates] = useState<ScriptTemplate[]>([]);

  useEffect(() => {
    api
      .templates()
      .then(setTemplates)
      .catch(() => undefined);
  }, []);

  if (!templates.length) return null;

  return (
    <div className="templates">
      <span className="dim">Not sure of the format? Start from a template:</span>
      <div className="toolbar">
        {templates.map((template) => (
          <a
            key={template.slug}
            className="download"
            href={api.templateUrl(template.slug, true)}
            download={template.filename}
            title={template.summary}
          >
            <DownloadIcon /> {template.title}
          </a>
        ))}
      </div>
      <span className="faint">
        Annotated throughout, and every note is stripped before anything is sent —
        so it generates as-is.
      </span>
    </div>
  );
}
