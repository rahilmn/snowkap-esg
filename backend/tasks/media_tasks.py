"""Celery tasks for multimodal file processing.

Per MASTER_BUILD_PLAN Phase 10:
- Celery pipeline: upload → MinIO → processor → pgvector embedding
- Extracted data → entity extraction → feed into Jena ontology
"""

import asyncio

import structlog

from backend.tasks.celery_app import celery_app

logger = structlog.get_logger()


def _run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="media.process_file", bind=True, max_retries=2, default_retry_delay=30)
def process_media_file_task(self, media_file_id: str, tenant_id: str) -> dict:
    """Background task: process an uploaded media file.

    Pipeline: download from MinIO → detect type → extract text → generate embeddings → store.
    """
    async def _process():
        from backend.core.database import create_worker_session_factory
        from backend.models.media import MediaChunk, MediaFile
        from backend.services.embedding_service import generate_embeddings_batch
        from backend.services.processors import detect_processor, process_file
        from backend.services.storage_service import storage_service
        from sqlalchemy import select

        session_factory = create_worker_session_factory()
        async with session_factory() as db:
            # Load media file record
            result = await db.execute(
                select(MediaFile).where(
                    MediaFile.id == media_file_id,
                    MediaFile.tenant_id == tenant_id,
                )
            )
            media_file = result.scalar_one_or_none()
            if not media_file:
                return {"error": "Media file not found"}

            # Update status
            media_file.status = "processing"
            await db.flush()

            try:
                # Download from MinIO
                file_data = await storage_service.download_file(media_file.minio_key)

                # Detect processor
                processor_type = detect_processor(media_file.content_type, media_file.original_filename)
                if not processor_type:
                    media_file.status = "failed"
                    media_file.extracted_metadata = {"error": "Unsupported file type"}
                    await db.commit()
                    return {"error": "Unsupported file type"}

                media_file.processor = processor_type

                # Process file
                proc_result = await process_file(file_data, media_file.original_filename, processor_type)

                # Store extracted content
                media_file.extracted_text = proc_result.text[:100_000]  # Cap at 100k chars
                media_file.extracted_metadata = proc_result.metadata
                media_file.page_count = proc_result.page_count
                media_file.language = proc_result.language

                # Run entity extraction on the text
                if proc_result.text.strip():
                    try:
                        from backend.ontology.entity_extractor import extract_and_classify
                        extraction = await extract_and_classify(proc_result.text[:5000])
                        media_file.entities = extraction.get("entities", [])
                        media_file.esg_topics = extraction.get("esg_topics", [])
                    except Exception as e:
                        logger.warning("entity_extraction_failed", error=str(e))

                # Generate embeddings for chunks and store
                if proc_result.chunks:
                    chunk_texts = [c["content"] for c in proc_result.chunks]
                    embeddings = await generate_embeddings_batch(chunk_texts)

                    for i, chunk_data in enumerate(proc_result.chunks):
                        chunk = MediaChunk(
                            tenant_id=tenant_id,
                            media_file_id=media_file_id,
                            chunk_index=chunk_data.get("chunk_index", i),
                            content=chunk_data["content"],
                            page_number=chunk_data.get("page_number"),
                            embedding=embeddings[i] if i < len(embeddings) else None,
                            metadata_={"processor": processor_type},
                        )
                        db.add(chunk)

                media_file.status = "processed"
                await db.commit()

                logger.info(
                    "media_file_processed",
                    media_file_id=media_file_id,
                    processor=processor_type,
                    chunks=len(proc_result.chunks),
                    text_length=len(proc_result.text),
                )

                # Emit real-time update
                try:
                    from backend.core.socketio import emit_to_tenant
                    await emit_to_tenant(tenant_id, "media_processed", {
                        "media_file_id": media_file_id,
                        "filename": media_file.original_filename,
                        "status": "processed",
                        "chunks": len(proc_result.chunks),
                    })
                except Exception:
                    pass

                return {
                    "media_file_id": media_file_id,
                    "processor": processor_type,
                    "chunks": len(proc_result.chunks),
                    "text_length": len(proc_result.text),
                    "status": "processed",
                }

            except Exception as e:
                media_file.status = "failed"
                media_file.extracted_metadata = {"error": str(e)}
                await db.commit()
                raise

    try:
        return _run_async(_process())
    except Exception as e:
        logger.error("media_processing_failed", media_file_id=media_file_id, error=str(e))
        return {"media_file_id": media_file_id, "error": str(e)}
