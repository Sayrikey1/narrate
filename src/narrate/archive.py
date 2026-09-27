"""The one check every spending path makes: is this episode still live?

Kept apart from `episodes`, which imports the runner, so the runner and the
effects module can call it without a cycle.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from narrate.db.models import Project, Script


class Archived(RuntimeError):
    """A deleted project or episode was asked to spend."""


def ensure_live(session: Session, script: Script) -> None:
    """Refuse to spend on a deleted episode, or on one in a deleted project.

    Called where spending starts — a generation, effects, a regeneration, the
    paid copy and suggestion commands — so a stale browser tab or an old id on
    the command line cannot bill an episode that was deleted.
    """
    if script.archived_at is not None:
        raise Archived(
            f"Episode {script.id} ({script.title!r}) was deleted. Restore it with "
            f"`narrate script restore {script.id}` before generating anything for it."
        )
    project = session.get(Project, script.project_id)
    if project is not None and project.archived_at is not None:
        raise Archived(
            f"Project {project.name!r} was deleted. Restore it with "
            f"`narrate project restore {project.id}` first."
        )
