"""Project-local prerequisite graph for needs."""
from alembic import op
import sqlalchemy as sa

revision = "a61f4d8c20e7"
down_revision = "f5b902d83a17"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "need_dependencies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prerequisite_id", sa.Integer(), sa.ForeignKey("context_need_links.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dependent_id", sa.Integer(), sa.ForeignKey("context_need_links.id", ondelete="CASCADE"), nullable=False),
        sa.Column("origin", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "prerequisite_id", "dependent_id", name="uq_need_dependency"),
        sa.CheckConstraint("prerequisite_id != dependent_id", name="ck_need_dependency_not_self"),
    )
    op.create_index("ix_need_dependencies_project_id", "need_dependencies", ["project_id"])
    op.create_index("ix_need_dependencies_prerequisite_id", "need_dependencies", ["prerequisite_id"])
    op.create_index("ix_need_dependencies_dependent_id", "need_dependencies", ["dependent_id"])


def downgrade():
    op.drop_table("need_dependencies")
