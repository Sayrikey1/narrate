"""A flagged take a person listened to and accepted.

Revision ID: e6c3a9d58b21
Revises: d4a7e2c91b36

One nullable column, `take.verify_accepted_at`. The check misreads some things
every time — a name it cannot spell, a contraction it writes out in full — and
regenerating cannot fix a take that was never wrong. Before this, the only way
to clear the flag was to pay for new takes that came back flagged the same way.

`verify_status` is left as the check's own verdict rather than overwritten, so
the findings stay on the take to be heard again and the acceptance can be
undone. A nullable column is added in place — no table rebuild.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e6c3a9d58b21"
down_revision = "d4a7e2c91b36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("take", schema=None) as batch_op:
        batch_op.add_column(sa.Column("verify_accepted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("take", schema=None) as batch_op:
        batch_op.drop_column("verify_accepted_at")
