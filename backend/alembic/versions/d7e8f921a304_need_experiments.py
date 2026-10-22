"""Frozen experiment briefs and explicit outcomes for project needs."""
from alembic import op
import sqlalchemy as sa

revision = "d7e8f921a304"
down_revision = "c3f7a0d9e112"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "need_experiments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("context_need_id", sa.Integer(), sa.ForeignKey("context_need_links.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("active_key", sa.String(64), nullable=True),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("brief", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("result_note", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("need_confirmed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("active_key", name="uq_need_experiment_active_key"),
        sa.CheckConstraint("status IN ('draft','running','completed','cancelled')", name="ck_experiment_status"),
        sa.CheckConstraint("outcome IN ('','useful','not_useful','inconclusive')", name="ck_experiment_outcome"),
    )
    for column in ("context_need_id", "item_id"):
        op.create_index(f"ix_need_experiments_{column}", "need_experiments", [column])


def downgrade():
    op.drop_table("need_experiments")
