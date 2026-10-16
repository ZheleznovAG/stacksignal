"""Repositories of watched authors, as seen on their profiles (metadata only).

Revision ID: d7e2a9c4b815
Revises: b3c41f7e9a02
"""

from alembic import op
import sqlalchemy as sa

revision = "d7e2a9c4b815"
down_revision = "b3c41f7e9a02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "author_repos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("authors.id"), nullable=False),
        sa.Column("full_name", sa.String(300), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("fork", sa.Boolean(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("pushed_at", sa.DateTime(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("promoted_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("full_name"),
    )
    op.create_index("ix_author_repos_author_id", "author_repos", ["author_id"])


def downgrade() -> None:
    op.drop_index("ix_author_repos_author_id", table_name="author_repos")
    op.drop_table("author_repos")
