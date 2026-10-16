"""Context-specific needs, evidence and explicit results; preserve legacy profile text."""
import json

from alembic import op
import sqlalchemy as sa

revision = "f5b902d83a17"
down_revision = "e4a819c72f06"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("needs", sa.Column("id", sa.Integer(), primary_key=True),
                    sa.Column("title", sa.String(600), nullable=False), sa.Column("origin", sa.String(30), nullable=False))
    op.create_table("context_need_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("need_id", sa.Integer(), sa.ForeignKey("needs.id"), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False), sa.Column("state", sa.String(20), nullable=False),
        sa.Column("constraints", sa.Text(), nullable=False), sa.Column("acceptance_criteria", sa.Text(), nullable=False),
        sa.Column("search_terms", sa.JSON(), nullable=False), sa.Column("pilot", sa.Boolean(), nullable=False),
        sa.Column("result_note", sa.Text(), nullable=False),
        sa.Column("result_fingerprint", sa.String(64), nullable=False),
        sa.Column("confirmed_item_id", sa.Integer(), sa.ForeignKey("items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("project_id", "need_id", name="uq_context_need"),
        sa.CheckConstraint("state IN ('open','evaluating','satisfied','deferred','dropped')", name="ck_need_state"))
    op.create_index("ix_context_need_links_project_id", "context_need_links", ["project_id"])
    op.create_table("item_need_relevance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("context_need_id", sa.Integer(), sa.ForeignKey("context_need_links.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False), sa.Column("score", sa.Float(), nullable=True),
        sa.Column("constraint_status", sa.String(20), nullable=False), sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False), sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("assessed_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("item_id", "context_need_id", name="uq_item_context_need"))
    for column in ("item_id", "context_need_id"):
        op.create_index(f"ix_item_need_relevance_{column}", "item_need_relevance", [column])
    op.create_table("need_events", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("context_need_id", sa.Integer(), sa.ForeignKey("context_need_links.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor", sa.String(20), nullable=False), sa.Column("action", sa.String(30), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_need_events_context_need_id", "need_events", ["context_need_id"])
    # Separate identities per context: identical words do not imply identical requirements.
    connection = op.get_bind()
    for project_id, raw in connection.execute(sa.text("SELECT id, needs FROM projects")):
        values = json.loads(raw) if isinstance(raw, str) else raw
        for title in dict.fromkeys(v.strip() for v in (values or []) if isinstance(v, str) and v.strip()):
            need_id = connection.execute(sa.text("INSERT INTO needs(title,origin) VALUES (:title,'profile_import') RETURNING id"), {"title": title}).scalar_one()
            connection.execute(sa.text("""INSERT INTO context_need_links
                (project_id,need_id,priority,state,constraints,acceptance_criteria,search_terms,pilot,result_note,result_fingerprint,updated_at)
                VALUES (:project,:need,2,'open','','','[]',0,'','',CURRENT_TIMESTAMP)"""), {"project": project_id, "need": need_id})


def downgrade():
    for table in ("need_events", "item_need_relevance", "context_need_links", "needs"):
        op.drop_table(table)
