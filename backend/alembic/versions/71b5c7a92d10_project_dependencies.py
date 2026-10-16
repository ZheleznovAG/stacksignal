"""Preserve structured project dependencies and backfill legacy package identities.

Revision ID: 71b5c7a92d10
Revises: 29a12cdfa2f9
"""

import re

from alembic import op
import sqlalchemy as sa

revision = "71b5c7a92d10"
down_revision = "29a12cdfa2f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dependencies = op.create_table(
        "project_dependencies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("ecosystem", sa.String(30), nullable=False),
        sa.Column("package_key", sa.String(400), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("declared_spec", sa.String(300), nullable=False),
        sa.Column("resolved_version", sa.String(120), nullable=False),
        sa.Column("manifest_path", sa.String(500), nullable=False),
        sa.Column("scope", sa.String(30), nullable=False),
        sa.Column("purl", sa.String(600), nullable=False),
        sa.UniqueConstraint("project_id", "ecosystem", "package_key", "manifest_path", "scope", "declared_spec",
                            name="uq_project_dependency"),
    )
    op.create_index("ix_project_dependencies_project_id", "project_dependencies", ["project_id"])
    projects = sa.table("projects", sa.column("id", sa.Integer()), sa.column("stack_packages", sa.JSON()))
    conn = op.get_bind()
    for project_id, packages in conn.execute(sa.select(projects.c.id, projects.c.stack_packages)):
        seen = set()
        for package in packages or []:
            if not isinstance(package, dict):
                continue
            ecosystem = str(package.get("ecosystem") or "").strip().lower()
            name = str(package.get("name") or "").strip()
            key = re.sub(r"[-_.]+", "-", name).lower() if ecosystem == "pypi" else (
                name.lower() if ecosystem in {"npm", "nuget"} else name)
            if not ecosystem or not name or (ecosystem, key) in seen:
                continue
            seen.add((ecosystem, key))
            conn.execute(dependencies.insert().values(project_id=project_id, ecosystem=ecosystem, package_key=key,
                                                      name=name, declared_spec="", resolved_version="",
                                                      manifest_path="", scope="runtime", purl=""))


def downgrade() -> None:
    op.drop_index("ix_project_dependencies_project_id", table_name="project_dependencies")
    op.drop_table("project_dependencies")
