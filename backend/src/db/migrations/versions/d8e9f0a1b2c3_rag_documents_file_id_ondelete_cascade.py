"""rag_documents_file_id_ondelete_cascade

Change the ``rag_documents.file_id`` foreign key from ``ON DELETE SET NULL``
to ``ON DELETE CASCADE`` (Issue #628).

A chunk has no meaning without its source file upload, and with ``SET NULL``
every bulk delete of ``file_uploads`` rows (session deletion, message
deletion, temp-upload cleanup, user deletion) left the indexed chunks behind
as unreclaimable rows that still answered ``rag_search`` queries.

Revision ID: d8e9f0a1b2c3
Revises: c4d5e6f7a8b9
Create Date: 2026-11-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_NAME = "fk_rag_documents_file_id"


def _current_file_id_fk_name(bind) -> str | None:
    """Return the name of the FK on ``rag_documents.file_id``, if any."""
    for fk in sa.inspect(bind).get_foreign_keys("rag_documents"):
        if fk.get("constrained_columns") == ["file_id"]:
            return fk.get("name")
    return None


def upgrade() -> None:
    bind = op.get_bind()
    name = _current_file_id_fk_name(bind)
    if name:
        op.drop_constraint(name, "rag_documents", type_="foreignkey")
    op.create_foreign_key(
        FK_NAME,
        "rag_documents",
        "file_uploads",
        ["file_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    bind = op.get_bind()
    name = _current_file_id_fk_name(bind)
    if name:
        op.drop_constraint(name, "rag_documents", type_="foreignkey")
    op.create_foreign_key(
        FK_NAME,
        "rag_documents",
        "file_uploads",
        ["file_id"],
        ["id"],
        ondelete="SET NULL",
    )
