"""Cast members, and the columns a multi-voice take needs.

Revision ID: 9a1c4f7be2d0
Revises: 75bbda75ce01

Three changes, all additive:

* `cast_member` — names a project's speakers and the voice that plays each.
  Project-scoped, so a series casts a character once.
* `chunk.turns_json` — the speaker turns inside a chunk. Only set for a chunk
  generated as a *dialogue*, where one request covers several turns; a
  one-voice chunk leaves it null and nothing changes for it.
* `take.voices_json` — the ordered voices a take actually used. `take.voice_id`
  is a single non-null column and stays the lead speaker's voice, so every
  existing query keeps working; this records the rest rather than pretending a
  four-voice exchange had one.

`batch_alter_table` throughout: SQLite cannot ALTER in place. Every added
NOT NULL column carries a `server_default` because these tables have rows in
them, and no historical money column is rewritten.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9a1c4f7be2d0"
down_revision = "75bbda75ce01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cast_member",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("voice_id", sa.String(length=64), nullable=False),
        sa.Column("settings_json", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_cast_name"),
    )

    with op.batch_alter_table("chunk", schema=None) as batch_op:
        batch_op.add_column(sa.Column("turns_json", sa.Text(), nullable=True))

    with op.batch_alter_table("take", schema=None) as batch_op:
        batch_op.add_column(sa.Column("voices_json", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("take", schema=None) as batch_op:
        batch_op.drop_column("voices_json")

    with op.batch_alter_table("chunk", schema=None) as batch_op:
        batch_op.drop_column("turns_json")

    op.drop_table("cast_member")
