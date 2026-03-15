"""Media models — uploaded files, extracted content, embeddings.

Per MASTER_BUILD_PLAN Phase 10:
- Celery pipeline: upload → MinIO → processor → pgvector embedding
- Extracted data → entity extraction → feed into Jena ontology
- Semantic search across all tenant media
"""

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TenantMixin, generate_uuid


class MediaFile(Base, TenantMixin):
    """An uploaded file stored in MinIO with extracted metadata."""
    __tablename__ = "media_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    minio_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    minio_key: Mapped[str] = mapped_column(String(1000), nullable=False)

    # Processing status
    status: Mapped[str] = mapped_column(String(50), default="uploaded")  # uploaded, processing, processed, failed
    processor: Mapped[str | None] = mapped_column(String(100))  # pdf, image, audio, spreadsheet

    # Extracted content
    extracted_text: Mapped[str | None] = mapped_column(Text)
    extracted_metadata: Mapped[dict | None] = mapped_column(JSONB)
    page_count: Mapped[int | None] = mapped_column(Integer)
    language: Mapped[str | None] = mapped_column(String(10))

    # Entity extraction results
    entities: Mapped[list | None] = mapped_column(JSONB)
    esg_topics: Mapped[list | None] = mapped_column(JSONB)

    # Context links
    company_id: Mapped[str | None] = mapped_column(String(36))
    uploaded_by: Mapped[str | None] = mapped_column(String(36))
    tags: Mapped[list | None] = mapped_column(JSONB)


class MediaChunk(Base, TenantMixin):
    """A chunk of extracted text from a media file with pgvector embedding."""
    __tablename__ = "media_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    media_file_id: Mapped[str] = mapped_column(String(36), ForeignKey("media_files.id"), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)

    # pgvector embedding (1536 dimensions for ada-002 or 1024 for Claude)
    # Stored as ARRAY(Float) — actual pgvector column created in migration
    embedding: Mapped[list | None] = mapped_column(ARRAY(Float))

    # Chunk metadata (named metadata_ to avoid SQLAlchemy reserved name)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
