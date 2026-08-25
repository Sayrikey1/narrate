"""Local handles for voices, so a cloned voice can be reused by name.

Revision ID: c3d81f4a97e5
Revises: 9a1c4f7be2d0

Stores no audio and grants no access — just a name, the provider's voice id, and
what the provider said about it when it was registered. Additive: nothing reads
this table unless a name is actually used, so an existing database keeps working
untouched.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3d81f4a97e5"
down_revision = "9a1c4f7be2d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "registered_voice",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("voice_id", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_registered_voice_slug"),
        sa.UniqueConstraint("voice_id", name="uq_registered_voice_id"),
    )


def downgrade() -> None:
    op.drop_table("registered_voice")
