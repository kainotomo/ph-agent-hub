# =============================================================================
# PH Agent Hub — ORM: RAG Documents
# =============================================================================
# Stores document chunks with their embedding vectors for semantic search.
# Each row = one chunk of a document. Use FileUpload.extracted_text as the
# source text and store embedding vectors in the embedding_json column.
#
# For production at scale, replace Python cosine similarity with pgvector
# or Qdrant — the embedding_json column is a drop-in replacement.
# =============================================================================

import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime, Integer, ForeignKey, JSON, func
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from .tenants import Tenant
from .file_uploads import FileUpload


class RAGDocument(Base):
    __tablename__ = "rag_documents"

    id: Mapped[str] = mapped_column(
        CHAR(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    tenant_id: Mapped[str] = mapped_column(
        CHAR(36), ForeignKey("tenants.id"), nullable=False
    )
    file_id: Mapped[str | None] = mapped_column(
        CHAR(36), ForeignKey("file_uploads.id", ondelete="SET NULL"), nullable=True,
        comment="Source file upload (null for ad-hoc indexed text)",
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False,
        comment="Chunk text (not full document)")
    chunk_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False,
        comment="Position of this chunk within the source document")
    extra_metadata: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    embedding_json: Mapped[list | None] = mapped_column(JSON, nullable=True,
        comment="Embedding vector stored as JSON array of floats")
    model: Mapped[str | None] = mapped_column(String(64), nullable=True,
        comment="Embedding model used to generate this vector")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
