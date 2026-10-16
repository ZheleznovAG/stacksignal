"""GitHub authors: accounts you follow, owners of repositories you starred, and ones you add yourself.

Revision ID: b3c41f7e9a02
Revises: 9d8e34f15a20
"""

from alembic import op
import sqlalchemy as sa

revision = "b3c41f7e9a02"
down_revision = "9d8e34f15a20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "authors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("login", sa.String(100), nullable=False),
        sa.Column("login_key", sa.String(100), nullable=False),
        sa.Column("github_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("followed", sa.Boolean(), nullable=False),
        sa.Column("starred_repos", sa.Integer(), nullable=False),
        sa.Column("last_starred_at", sa.DateTime(), nullable=True),
        sa.Column("added_manually", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("repos_checked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("login_key"),
    )
    op.create_index("ix_authors_status", "authors", ["status"])


def downgrade() -> None:
    op.drop_index("ix_authors_status", table_name="authors")
    op.drop_table("authors")
