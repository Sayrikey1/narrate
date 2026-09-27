"""Whether every word of a take was actually spoken.

Revision ID: b8e41d27c3f9
Revises: 7e2b9d4c15af

Four columns on `take`, all additive. No money column is touched, and nothing
already recorded changes meaning.

The decision worth writing down is *not* reusing `take.status`. A take whose
audio dropped a word still succeeded as a request, and seven code paths select
takes by `status == 'succeeded'`: resume, cut selection, variant export and
billing calibration among them. A new status value would have made a suspect
take regenerate — and bill — on the next plain resume, and quietly vanish from
export. Verification is a second, independent axis, so it gets its own column.

Existing takes start as `unverified`, which is the truth: nothing has checked
them. `batch_alter_table` because SQLite cannot ALTER in place; the NOT NULL
column carries a `server_default` because the table has rows.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8e41d27c3f9"
down_revision = "7e2b9d4c15af"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("take", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "verify_status",
                sa.String(length=16),
                nullable=False,
                server_default="unverified",
            )
        )
        batch_op.add_column(sa.Column("verify_findings_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("verifier", sa.String(length=96), nullable=True))
        batch_op.add_column(sa.Column("verified_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("take", schema=None) as batch_op:
        batch_op.drop_column("verified_at")
        batch_op.drop_column("verifier")
        batch_op.drop_column("verify_findings_json")
        batch_op.drop_column("verify_status")
