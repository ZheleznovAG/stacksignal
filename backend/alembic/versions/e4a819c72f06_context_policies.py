"""Context type, lifecycle and independent discovery, with derived legacy tier compatibility."""

from alembic import op
import sqlalchemy as sa

revision = "e4a819c72f06"
down_revision = "d7e2a9c4b815"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("kind", sa.String(30), nullable=False, server_default="project"))
    op.add_column("projects", sa.Column("lifecycle_status", sa.String(20), nullable=False, server_default="active"))
    op.add_column("projects", sa.Column("discovery_enabled", sa.Boolean(), nullable=False, server_default="1"))
    op.execute("UPDATE projects SET lifecycle_status = CASE WHEN tier IN ('watch','dormant') THEN tier ELSE 'active' END, "
               "discovery_enabled = CASE WHEN tier = 'dormant' THEN 0 ELSE 1 END")
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("tier")
        batch.create_check_constraint("ck_project_kind",
            "kind IN ('project','lab','infrastructure','experiment','goal','personal_system','reference_project')")
        batch.create_check_constraint("ck_project_lifecycle",
            "lifecycle_status IN ('active','watch','dormant','archived','incubate')")


def downgrade() -> None:
    op.add_column("projects", sa.Column("tier", sa.String(20), nullable=False, server_default="active"))
    op.execute("UPDATE projects SET tier = CASE WHEN lifecycle_status = 'active' THEN 'active' "
               "WHEN lifecycle_status IN ('watch','incubate') THEN 'watch' ELSE 'dormant' END")
    with op.batch_alter_table("projects") as batch:
        batch.drop_constraint("ck_project_kind", type_="check")
        batch.drop_constraint("ck_project_lifecycle", type_="check")
        batch.drop_column("kind")
        batch.drop_column("lifecycle_status")
        batch.drop_column("discovery_enabled")
        batch.alter_column("tier", server_default=None)
