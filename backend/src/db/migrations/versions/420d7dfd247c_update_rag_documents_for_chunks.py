"""update_rag_documents_for_chunks

Add chunk_index, embedding_json, file_id, and model columns to
rag_documents for chunk-level storage with MariaDB JSON embeddings.
Deprecate vector_id (make nullable).

Revision ID: 420d7dfd247c
Revises: a7b8c9d0e1f2
Create Date: 2026-05-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "420d7dfd247c"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- Add new columns to rag_documents ---------------------------------
    op.add_column(
        "rag_documents",
        sa.Column(
            "chunk_index",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Position of this chunk within the source document",
        ),
    )
    op.add_column(
        "rag_documents",
        sa.Column(
            "embedding_json",
            sa.JSON(),
            nullable=True,
            comment="Embedding vector stored as JSON array of floats",
        ),
    )
    op.add_column(
        "rag_documents",
        sa.Column(
            "file_id",
            mysql.CHAR(length=36),
            nullable=True,
            comment="Source file upload (null for ad-hoc indexed text)",
        ),
    )
    op.add_column(
        "rag_documents",
        sa.Column(
            "model",
            sa.String(length=64),
            nullable=True,
            comment="Embedding model used to generate this vector",
        ),
    )

    # Make vector_id nullable (deprecated — embeddings now in embedding_json)
    op.alter_column(
        "rag_documents",
        "vector_id",
        existing_type=sa.String(length=255),
        nullable=True,
    )

    # Add foreign key for file_id
    op.create_foreign_key(
        "fk_rag_documents_file_id",
        "rag_documents",
        "file_uploads",
        ["file_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Drop foreign key first
    op.drop_constraint(
        "fk_rag_documents_file_id",
        "rag_documents",
        type_="foreignkey",
    )

    # Make vector_id not-null again
    op.alter_column(
        "rag_documents",
        "vector_id",
        existing_type=sa.String(length=255),
        nullable=False,
    )

    # Drop new columns
    op.drop_column("rag_documents", "model")
    op.drop_column("rag_documents", "file_id")
    op.drop_column("rag_documents", "embedding_json")
    op.drop_column("rag_documents", "chunk_index")
