"""SQLAlchemy ORM models for persisted documents and chunks."""

from datetime import datetime

from sqlalchemy import JSON, Computed, ForeignKey, Identity, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Shared declarative base for all ingestion ORM models."""


class DocumentRecord(Base):
    """A single ingested document."""

    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(primary_key=True)
    filename: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ChunkRecord(Base):
    """A single persisted chunk of a document, with full provenance metadata.

    `vector_id` is a separate autoincrement integer identity, distinct from the
    business-facing string `chunk_id` — FAISS requires int64 vector IDs.
    """

    __tablename__ = "chunks"

    chunk_id: Mapped[str] = mapped_column(primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.document_id"), index=True)
    chunk_index: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    section_path: Mapped[list[str]] = mapped_column(JSON)
    page_start: Mapped[int]
    page_end: Mapped[int]
    char_count: Mapped[int]
    parser_used: Mapped[str]
    source_filename: Mapped[str]
    vector_id: Mapped[int] = mapped_column(Identity(always=True), unique=True)
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
    )
