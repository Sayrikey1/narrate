"""Deleting a project or an episode, without losing a cent of what it cost.

Revision ID: d4a7e2c91b36
Revises: b8e41d27c3f9

One nullable column, `archived_at`, on `project` and on `script`. A delete sets
it; the row stays. Four facts rule out removing the row:

* The ledger is append-only and has no foreign keys, so its rows would point at
  ids that no longer exist.
* Ids are INTEGER PRIMARY KEY without AUTOINCREMENT, so SQLite hands a deleted
  id to the next row created — and a new episode would inherit the old one's
  spend, its monthly-cap usage and its asset folders.
* Waste is "spend whose take is not in a cut": cascading an episode's cuts away
  would turn its finished audio into re-rolls.
* A delete during a run would roll back the billed charge written with the take.

Keeping the row avoids all four. Nothing already recorded changes meaning, and
a nullable column is added in place — no table rebuild.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4a7e2c91b36"
down_revision = "b8e41d27c3f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("script", schema=None) as batch_op:
        batch_op.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("script", schema=None) as batch_op:
        batch_op.drop_column("archived_at")
    with op.batch_alter_table("project", schema=None) as batch_op:
        batch_op.drop_column("archived_at")
