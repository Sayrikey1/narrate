"""Chapter titles, episode packaging, outline-first drafting.

Revision ID: 7e2b9d4c15af
Revises: c3d81f4a97e5

Three tables and six columns, all additive. Nothing already recorded moves, and
no money column is touched — reconciliation replays the ledger, so a migration
that shifted a historical figure would be worse than no migration at all.

Two scoping decisions worth writing down:

* **Description and tags are columns on `script`, not a table.** There is one of
  each per episode and neither causes any spend, so a table would be schema for
  its own sake. `project.prefix_tags` already establishes that a per-row config
  string lives on the row.
* **A chosen title needs no table.** `script.title` already is one. Accepting a
  candidate writes through to it and the candidate rows stay as a record of what
  was considered, which is the same relationship `cut` has to `take`.

`batch_alter_table` for the added columns: SQLite cannot ALTER in place. Each
added NOT NULL column carries a `server_default` because these tables have rows
in them; the two nullable ones do not need one, and the new tables need none at
all because they have no rows yet.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "7e2b9d4c15af"
down_revision = "c3d81f4a97e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("description_boilerplate", sa.Text(), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("default_tags", sa.Text(), nullable=False, server_default=""))

    with op.batch_alter_table("script", schema=None) as batch_op:
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("tags", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("target_seconds", sa.Float(), nullable=True))

    with op.batch_alter_table("chunk", schema=None) as batch_op:
        batch_op.add_column(sa.Column("chapter_title", sa.String(length=120), nullable=True))

    op.create_table(
        "title_candidate",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("script_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.String(length=200), nullable=False),
        sa.Column("formula", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("accepted", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("model_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["script_id"], ["script.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("script_id", "text", name="uq_title_candidate_text"),
    )
    with op.batch_alter_table("title_candidate", schema=None) as batch_op:
        batch_op.create_index("ix_title_candidate_script", ["script_id"], unique=False)

    op.create_table(
        "thumbnail_brief",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("script_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("archetype", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("overlay_text", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("contrast", sa.Text(), nullable=False, server_default=""),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column("principles", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("accepted", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("model_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["script_id"], ["script.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("thumbnail_brief", schema=None) as batch_op:
        batch_op.create_index("ix_thumbnail_brief_script", ["script_id"], unique=False)

    op.create_table(
        "script_beat",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("script_id", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(length=120), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False, server_default=""),
        sa.Column("target_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="outline"),
        sa.Column("accepted", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("model_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["script_id"], ["script.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("script_id", "ordinal", name="uq_beat_order"),
    )
    with op.batch_alter_table("script_beat", schema=None) as batch_op:
        batch_op.create_index("ix_script_beat_script", ["script_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("script_beat", schema=None) as batch_op:
        batch_op.drop_index("ix_script_beat_script")
    op.drop_table("script_beat")

    with op.batch_alter_table("thumbnail_brief", schema=None) as batch_op:
        batch_op.drop_index("ix_thumbnail_brief_script")
    op.drop_table("thumbnail_brief")

    with op.batch_alter_table("title_candidate", schema=None) as batch_op:
        batch_op.drop_index("ix_title_candidate_script")
    op.drop_table("title_candidate")

    with op.batch_alter_table("chunk", schema=None) as batch_op:
        batch_op.drop_column("chapter_title")

    with op.batch_alter_table("script", schema=None) as batch_op:
        batch_op.drop_column("target_seconds")
        batch_op.drop_column("tags")
        batch_op.drop_column("description")

    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("default_tags")
        batch_op.drop_column("description_boilerplate")
