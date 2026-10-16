"""Track the profile used to generate each search query.

Revision ID: 9d8e34f15a20
Revises: 71b5c7a92d10
"""

from alembic import op
import sqlalchemy as sa

revision = "9d8e34f15a20"
down_revision = "71b5c7a92d10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("search_queries") as batch:
        batch.add_column(sa.Column("profile_fingerprint", sa.String(64), nullable=False, server_default=""))
        batch.add_column(sa.Column("basis", sa.JSON(), nullable=False, server_default="[]"))
    with op.batch_alter_table("search_queries") as batch:
        batch.alter_column("profile_fingerprint", server_default=None)
        batch.alter_column("basis", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("search_queries") as batch:
        batch.drop_column("basis")
        batch.drop_column("profile_fingerprint")
