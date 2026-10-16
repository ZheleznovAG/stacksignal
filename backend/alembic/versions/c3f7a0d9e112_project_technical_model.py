"""Stored structured technical model of each project."""
from alembic import op
import sqlalchemy as sa

revision = "c3f7a0d9e112"
down_revision = "a61f4d8c20e7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "project_technical_models",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("goals", sa.JSON(), nullable=False),
        sa.Column("subsystems", sa.JSON(), nullable=False),
        sa.Column("gaps", sa.JSON(), nullable=False),
        sa.Column("risks", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", name="uq_project_technical_model_project"),
    )
    op.create_index("ix_project_technical_models_project_id", "project_technical_models", ["project_id"])


def downgrade():
    op.drop_table("project_technical_models")
