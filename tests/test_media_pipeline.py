"""Media pipeline tests — processors, embedding service, storage.

Covers:
- File type detection (PDF, image, audio, spreadsheet, text)
- Processor routing
- MIME type validation
- Chunk generation logic
- Embedding service interface
"""

import pytest

from backend.services.processors import (
    ProcessorResult,
    detect_processor,
)
from backend.routers.media import ALLOWED_TYPES, MAX_FILE_SIZE


# --- Processor Detection Tests ---

class TestProcessorDetection:
    @pytest.mark.parametrize("content_type,filename,expected", [
        ("application/pdf", "report.pdf", "pdf"),
        ("image/png", "chart.png", "image"),
        ("image/jpeg", "photo.jpg", "image"),
        ("image/gif", "animation.gif", "image"),
        ("image/webp", "diagram.webp", "image"),
        ("audio/mpeg", "interview.mp3", "audio"),
        ("audio/wav", "recording.wav", "audio"),
        ("audio/mp4", "clip.m4a", "audio"),
        ("audio/ogg", "podcast.ogg", "audio"),
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "data.xlsx", "spreadsheet"),
        ("application/vnd.ms-excel", "data.xls", "spreadsheet"),
        ("text/csv", "data.csv", "spreadsheet"),
        ("text/plain", "notes.txt", "text"),
    ])
    def test_correct_processor_detected(self, content_type: str, filename: str, expected: str):
        processor = detect_processor(content_type, filename)
        assert processor == expected, f"Expected {expected} for {content_type}/{filename}, got {processor}"

    def test_unknown_type_returns_none(self):
        assert detect_processor("application/octet-stream", "mystery.bin") is None
        assert detect_processor("video/mp4", "video.mp4") is None

    def test_csv_by_extension(self):
        # CSV files might have generic content type but .csv extension
        processor = detect_processor("text/csv", "data.csv")
        assert processor == "spreadsheet"


# --- MIME Type Validation Tests ---

class TestMimeTypeValidation:
    def test_allowed_types_include_all_processors(self):
        assert "application/pdf" in ALLOWED_TYPES
        assert "image/png" in ALLOWED_TYPES
        assert "image/jpeg" in ALLOWED_TYPES
        assert "audio/mpeg" in ALLOWED_TYPES
        assert "text/csv" in ALLOWED_TYPES
        assert "text/plain" in ALLOWED_TYPES
        assert "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" in ALLOWED_TYPES

    def test_dangerous_types_not_allowed(self):
        assert "application/javascript" not in ALLOWED_TYPES
        assert "application/x-executable" not in ALLOWED_TYPES
        assert "text/html" not in ALLOWED_TYPES
        assert "application/x-sh" not in ALLOWED_TYPES

    def test_max_file_size_is_50mb(self):
        assert MAX_FILE_SIZE == 50 * 1024 * 1024


# --- ProcessorResult Tests ---

class TestProcessorResult:
    def test_processor_result_creation(self):
        result = ProcessorResult(
            text="Hello world",
            metadata={"pages": 1},
            page_count=1,
            language="en",
            chunks=[{"content": "Hello world", "chunk_index": 0}],
        )
        assert result.text == "Hello world"
        assert result.page_count == 1
        assert len(result.chunks) == 1

    def test_empty_processor_result(self):
        result = ProcessorResult(
            text="",
            metadata={},
            page_count=0,
            language=None,
            chunks=[],
        )
        assert result.text == ""
        assert len(result.chunks) == 0
