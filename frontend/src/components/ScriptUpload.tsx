import { useEffect, useId, useState } from "react";
import { api } from "../api";
import type { Project, ScriptTemplate } from "../types";
import { DownloadIcon } from "./Icon";

const ACCEPT = ".txt,.md,.markdown,.text";

/** Add an episode by dropping a file, browsing for one, or pasting.
 *
 *  All three land on the same ingestion, so a marker stripped one way is
 *  stripped every way. The server validates extension, size and that the bytes
 *  really are UTF-8 — an extension proves nothing.
 *
 *  The drop target is the file input's own label, and the input is visually
 *  hidden rather than `hidden`: it stays in the tab order, so the keyboard can
 *  reach "browse" as well as a pointer can. */
export function ScriptUpload({
  project,
  onCreated,
}: {
  /** The episode goes into this one — the project selected on the page. */
  project: Project | null;
  onCreated: (scriptId: number) => void;
}) {
  const id = useId();
  const [over, setOver] = useState(false);
  const [pasting, setPasting] = useState(false);
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!project) {
    return (
      <div className="empty">
        <strong>No projects yet</strong>
        Create one with <em>New project</em> above, then add an episode to it.
      </div>
    );
  }
  const projectId = project.id;

  function report(created: { id: number; chunks: number; slots: number; warnings: string[] }) {
    setResult(
      `${created.chunks} chunk${created.chunks === 1 ? "" : "s"}` +
        (created.slots ? ` · ${created.slots} effect slot${created.slots === 1 ? "" : "s"}` : "") +
        (created.warnings.length ? ` · ${created.warnings.join("; ")}` : ""),
    );
    onCreated(created.id);
  }

  async function send(file: File) {
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
    if (!text.trim()) return;
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
      <div className="adds-to">
        Adds to <span className="pill plain">{project.name}</span>
        <a className="link-quiet" href="#projects-h">
          change
        </a>
      </div>

      <div className="field">
        <label htmlFor={`${id}-title`}>
          Title <span className="label-note">— optional, the filename is used otherwise</span>
        </label>
        <input
          id={`${id}-title`}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Episode 14"
        />
      </div>

      {!pasting ? (
        <>
          <input
            id={`${id}-file`}
            className="sr-only file-input"
            type="file"
            accept={ACCEPT}
            disabled={busy}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void send(file);
              e.target.value = "";
            }}
          />
          <label
            htmlFor={`${id}-file`}
            className={`dropzone${over ? " over" : ""}`}
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
            <span className="dropzone-icon" aria-hidden>
              📄<span>✨</span>
            </span>
            <strong>{busy ? "Reading…" : "Drop a script here"}</strong>
            <span className="hint">
              .txt · .md · .markdown — or <span className="browse">browse</span>
            </span>
          </label>
          <button className="ghost small paste-toggle" onClick={() => setPasting(true)}>
            ✍️ or paste the text instead
          </button>
        </>
      ) : (
        <>
          <div className="field">
            <label htmlFor={`${id}-text`}>Script</label>
            <textarea
              id={`${id}-text`}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={
                "Paste the script.\n\n[SFX: wind howling, 4s] marks an effect.\n[@ 02:15] marks a target time."
              }
            />
          </div>
          <div className="toolbar">
            <button className="primary" disabled={busy || !text.trim()} onClick={sendPasted}>
              Add episode
            </button>
            <button className="ghost small" onClick={() => setPasting(false)}>
              upload a file instead
            </button>
          </div>
        </>
      )}

      {result && (
        <p className="note ok" role="status">
          Added — {result}
        </p>
      )}
      {error && (
        <p className="note error" role="alert">
          {error}
        </p>
      )}

      <TemplateLinks />
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
      <span className="dim">
        Not sure of the format? Start from a template — every note in it is stripped before
        sending, so it generates as-is.
      </span>
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
    </div>
  );
}
