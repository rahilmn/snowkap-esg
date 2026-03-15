"""Media router — file upload, processing status, semantic search.

Per MASTER_BUILD_PLAN Phase 10:
- Upload → MinIO → Celery processor → pgvector embedding
- Semantic search across all tenant media
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func, select

from backend.core.dependencies import TenantContext, get_tenant_context
from backend.models.media import MediaChunk, MediaFile

logger = structlog.get_logger()
router = APIRouter()

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

ALLOWED_TYPES = {
    "application/pdf",
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "audio/mpeg", "audio/wav", "audio/mp4", "audio/ogg",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv", "text/plain",
}


class MediaFileResponse(BaseModel):
    id: str
    filename: str
    original_filename: str
    content_type: str
    file_size: int
    status: str
    processor: str | None
    page_count: int | None
    entities: list | None
    esg_topics: list | None
    tags: list | None


class SearchResult(BaseModel):
    chunk_id: str
    content: str
    media_file_id: str
    filename: str
    page_number: int | None
    similarity: float


class MediaStats(BaseModel):
    total_files: int
    processed_count: int
    total_chunks: int
    by_type: dict[str, int]


@router.post("/upload", response_model=MediaFileResponse, status_code=status.HTTP_201_CREATED)
async def upload_file(
    file: UploadFile,
    company_id: str | None = None,
    tags: str | None = None,
    ctx: TenantContext = Depends(get_tenant_context),
) -> MediaFileResponse:
    """Upload a file for multimodal processing.

    Supported: PDF, images, audio, Excel/CSV, text files.
    File is stored in MinIO, then processed asynchronously via Celery.
    """
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Filename required")

    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type: {content_type}",
        )

    # Read file data
    file_data = await file.read()
    if len(file_data) > MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum: {MAX_FILE_SIZE // (1024 * 1024)}MB",
        )

    # Upload to MinIO
    from backend.services.storage_service import storage_service
    upload_result = await storage_service.upload_file(
        tenant_id=ctx.tenant_id,
        file_data=file_data,
        filename=file.filename,
        content_type=content_type,
        file_type="uploads",
    )

    # Parse tags
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None

    # Create DB record
    media_file = MediaFile(
        tenant_id=ctx.tenant_id,
        filename=upload_result["key"].split("/")[-1],
        original_filename=file.filename,
        content_type=content_type,
        file_size=len(file_data),
        minio_bucket=upload_result["bucket"],
        minio_key=upload_result["key"],
        status="uploaded",
        company_id=company_id,
        uploaded_by=ctx.user.user_id,
        tags=tag_list,
    )
    ctx.db.add(media_file)
    await ctx.db.flush()

    # Dispatch Celery processing task
    from backend.tasks.media_tasks import process_media_file_task
    process_media_file_task.delay(media_file.id, ctx.tenant_id)

    logger.info(
        "media_uploaded",
        media_file_id=media_file.id,
        filename=file.filename,
        size=len(file_data),
        tenant_id=ctx.tenant_id,
    )

    return MediaFileResponse(
        id=media_file.id,
        filename=media_file.filename,
        original_filename=media_file.original_filename,
        content_type=media_file.content_type,
        file_size=media_file.file_size,
        status=media_file.status,
        processor=media_file.processor,
        page_count=None,
        entities=None,
        esg_topics=None,
        tags=tag_list,
    )


@router.get("/", response_model=list[MediaFileResponse])
async def list_media_files(
    status_filter: str | None = None,
    company_id: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[MediaFileResponse]:
    """List uploaded media files for this tenant."""
    query = select(MediaFile).where(MediaFile.tenant_id == ctx.tenant_id)
    if status_filter:
        query = query.where(MediaFile.status == status_filter)
    if company_id:
        query = query.where(MediaFile.company_id == company_id)
    query = query.order_by(MediaFile.created_at.desc()).limit(limit).offset(offset)

    result = await ctx.db.execute(query)
    files = result.scalars().all()

    return [
        MediaFileResponse(
            id=f.id, filename=f.filename, original_filename=f.original_filename,
            content_type=f.content_type, file_size=f.file_size, status=f.status,
            processor=f.processor, page_count=f.page_count,
            entities=f.entities, esg_topics=f.esg_topics, tags=f.tags,
        )
        for f in files
    ]


@router.get("/{media_file_id}", response_model=MediaFileResponse)
async def get_media_file(
    media_file_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
) -> MediaFileResponse:
    """Get details of a specific media file."""
    result = await ctx.db.execute(
        select(MediaFile).where(
            MediaFile.id == media_file_id,
            MediaFile.tenant_id == ctx.tenant_id,
        )
    )
    f = result.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    return MediaFileResponse(
        id=f.id, filename=f.filename, original_filename=f.original_filename,
        content_type=f.content_type, file_size=f.file_size, status=f.status,
        processor=f.processor, page_count=f.page_count,
        entities=f.entities, esg_topics=f.esg_topics, tags=f.tags,
    )


@router.get("/{media_file_id}/text")
async def get_extracted_text(
    media_file_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Get extracted text content from a processed media file."""
    result = await ctx.db.execute(
        select(MediaFile).where(
            MediaFile.id == media_file_id,
            MediaFile.tenant_id == ctx.tenant_id,
        )
    )
    f = result.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    if f.status != "processed":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"File status: {f.status}")

    return {
        "media_file_id": f.id,
        "text": f.extracted_text,
        "metadata": f.extracted_metadata,
        "page_count": f.page_count,
    }


@router.get("/{media_file_id}/download-url")
async def get_download_url(
    media_file_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Get a presigned download URL for a media file."""
    result = await ctx.db.execute(
        select(MediaFile).where(
            MediaFile.id == media_file_id,
            MediaFile.tenant_id == ctx.tenant_id,
        )
    )
    f = result.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    from backend.services.storage_service import storage_service
    url = await storage_service.get_presigned_url(f.minio_key)
    return {"url": url, "filename": f.original_filename}


@router.post("/search", response_model=list[SearchResult])
async def search_media(
    query: str,
    limit: int = Query(default=10, le=50),
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[SearchResult]:
    """Semantic search across all tenant media using pgvector embeddings."""
    from backend.services.embedding_service import semantic_search

    results = await semantic_search(
        tenant_id=ctx.tenant_id,
        query=query,
        limit=limit,
        db=ctx.db,
    )

    return [SearchResult(**r) for r in results]


@router.get("/stats/summary", response_model=MediaStats)
async def media_stats(
    ctx: TenantContext = Depends(get_tenant_context),
) -> MediaStats:
    """Get media processing statistics for this tenant."""
    total = (await ctx.db.execute(
        select(func.count(MediaFile.id)).where(MediaFile.tenant_id == ctx.tenant_id)
    )).scalar() or 0

    processed = (await ctx.db.execute(
        select(func.count(MediaFile.id)).where(
            MediaFile.tenant_id == ctx.tenant_id,
            MediaFile.status == "processed",
        )
    )).scalar() or 0

    chunks = (await ctx.db.execute(
        select(func.count(MediaChunk.id)).where(MediaChunk.tenant_id == ctx.tenant_id)
    )).scalar() or 0

    type_result = await ctx.db.execute(
        select(MediaFile.processor, func.count(MediaFile.id))
        .where(MediaFile.tenant_id == ctx.tenant_id, MediaFile.processor.isnot(None))
        .group_by(MediaFile.processor)
    )
    by_type = {row[0]: row[1] for row in type_result.all()}

    return MediaStats(
        total_files=total,
        processed_count=processed,
        total_chunks=chunks,
        by_type=by_type,
    )
