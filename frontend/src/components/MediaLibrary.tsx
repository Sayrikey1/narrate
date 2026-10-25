import { useEffect, useState } from "react";
import { api, bytes } from "../api";
import { DocumentView } from "../ui/DocumentView";
import { Empty, Loadable } from "../ui/feedback";
import { DownloadIcon } from "./Icon";
import { Player } from "./Player";
import type { MediaFile, ProjectMedia, SavedArchive } from "../types";

/** Which artifacts are documents rather than audio. `plan.md` is the one that
 *  matters: it is the guide to the edit, and reading it should not require
 *  downloading it first and finding something to open it with. */
const READABLE = /\.(md|markdown|txt|text|json)$/i;

/** Everything a project has produced, playable and downloadable.
 *
 *  This is how media comes back out later: the exported master and its
 *  timeline-named pieces, the reusable effect library, and the takes — each
 *  with a player and a download, plus a zip per export. */
export function MediaLibrary({ projectId }: { projectId: number }) {
  const [media, setMedia] = useState<ProjectMedia | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reading, setReading] = useState<{ file: MediaFile; text: string } | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    api
      .media(projectId)
      .then(setMedia)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [projectId]);

  if (reading) {
    return (
      <>
        <div className="toolbar media-back">
          <button className="ghost small" onClick={() => setReading(null)}>
            ← Back to files
          </button>
        </div>
        <DocumentView
          source={reading.text}
          filename={reading.file.name}
          downloadUrl={api.mediaUrl(reading.file.path, true)}
        />
      </>
    );
  }

  if (loading || error) {
    return <Loadable loading={loading} error={error} rows={4} />;
  }
  if (!media || !media.groups.length) {
    return (
      <Empty title="Nothing generated yet">
        Generate a script, then export it — the exported master, its
        timeline-named pieces, the effect library and every take appear here.
      </Empty>
    );
  }

  return (
    <>
      <p className="dim">
        {media.files} file{media.files === 1 ? "" : "s"} · {bytes(media.bytes)} on disk
      </p>

      {media.groups.map((group) => (
        <div key={`${group.kind}-${group.title}`} className="media-group">
          <div className="group-head">
            <strong>{group.title}</strong>
            <span className="dim">{bytes(group.bytes)}</span>
            {group.kind === "export" && group.files[0]?.script_id != null && (
              <ZipAction scriptId={group.files[0].script_id} folder={media.export_folder} />
            )}
          </div>

          <table>
            <tbody>
              {group.files.map((file) => (
                <tr key={file.path}>
                  <td className="mono">{file.name}</td>
                  <td className="dim">
                    {file.label}
                    {file.duration_s ? ` · ${file.duration_s.toFixed(2)}s` : ""}
                  </td>
                  <td className="num dim mono">{bytes(file.bytes)}</td>
                  <td>
                    {file.playable ? (
                      <Player src={api.mediaUrl(file.path)} duration={file.duration_s} />
                    ) : READABLE.test(file.name) ? (
                      <button
                        className="ghost small"
                        onClick={async () => {
                          try {
                            setReading({ file, text: await api.mediaText(file.path) });
                          } catch (e) {
                            setError(String(e));
                          }
                        }}
                      >
                        Read
                      </button>
                    ) : (
                      <span className="faint">—</span>
                    )}
                  </td>
                  <td>
                    <a className="download" href={api.mediaUrl(file.path, true)} download>
                      <DownloadIcon />
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </>
  );
}

/** An export's zip. With exports in a folder of their own it is saved there,
 *  beside the episode folders — `004.zip` next to `004/` — so every zip ends up
 *  in one place rather than wherever the browser saves. Otherwise, a download.
 *  Either way it unzips to the episode's folder. */
function ZipAction({ scriptId, folder }: { scriptId: number; folder: string | null }) {
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<SavedArchive | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!folder) {
    return (
      <a className="download" href={api.archiveUrl(scriptId)} download>
        <DownloadIcon /> zip
      </a>
    );
  }
  const where = folder.split("/").filter(Boolean).pop() ?? folder;

  async function save() {
    setSaving(true);
    setSaved(null);
    setError(null);
    try {
      setSaved(await api.saveArchive(scriptId));
    } catch (e) {
      setError(String(e).replace(/^Error:\s*/, ""));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <button
        className="ghost small"
        onClick={() => void save()}
        disabled={saving}
        title={`Saves the zip in ${folder}, beside the episode folders, replacing one of the same name`}
      >
        <DownloadIcon /> {saving ? "Saving…" : "Save zip"}
      </button>
      {saved && (
        <span className="faint" role="status">
          {saved.replaced ? "Replaced" : "Saved"} {saved.name} in {where}
        </span>
      )}
      {error && (
        <span className="error" role="alert">
          {error}
        </span>
      )}
    </>
  );
}
