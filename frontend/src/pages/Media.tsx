import { MediaLibrary } from "../components/MediaLibrary";
import { Variants } from "../components/Variants";
import { Empty } from "../ui/feedback";
import { Card, PageHeader, Split } from "../ui/layout";
import type { Project } from "../types";

/** Everything a project has produced, playable, readable and downloadable —
 *  and, beside it, the versions of the open script that can still be exported. */
export function MediaPage({
  project,
  scriptId,
}: {
  project: Project | null;
  /** The script in context, if a script page led here. */
  scriptId: number | null;
}) {
  return (
    <>
      <PageHeader
        title="Media"
        subtitle={
          project
            ? `${project.name} — everything generated for this project`
            : "pick a project to see its files"
        }
      />

      <Split
        main={
          <Card>
            {project ? (
              <MediaLibrary projectId={project.id} />
            ) : (
              <Empty title="No project selected">
                Pick one on the projects page and its exports, cues and takes appear here.
              </Empty>
            )}
          </Card>
        }
        aside={scriptId !== null ? <Variants scriptId={scriptId} /> : undefined}
      />
    </>
  );
}
