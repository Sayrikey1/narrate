import { MediaLibrary } from "../components/MediaLibrary";
import { Empty } from "../ui/feedback";
import { Card, PageHeader } from "../ui/layout";
import type { Project } from "../types";

/** Everything a project has produced, playable, readable and downloadable. */
export function MediaPage({ project }: { project: Project | null }) {
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

      <Card>
        {project ? (
          <MediaLibrary projectId={project.id} />
        ) : (
          <Empty title="No project selected">
            Pick one on the projects page and its exports, cues and takes appear here.
          </Empty>
        )}
      </Card>
    </>
  );
}
