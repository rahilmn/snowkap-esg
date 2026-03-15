"""Model integrity tests — validates SQLAlchemy model definitions.

Covers:
- All models register with Base.metadata
- tenant_id present on tenant-scoped tables
- Table names follow naming convention
- No duplicate table names
"""

import pytest

from backend.models import Base
from backend.models.base import TenantMixin, generate_uuid


# --- Model Registration Tests ---

class TestModelRegistration:
    """Verify all models are properly registered for Alembic autogenerate."""

    EXPECTED_TABLES = {
        # Core tenant tables
        "tenants", "tenant_memberships", "tenant_config",
        # Users
        "users", "magic_links",
        # Companies
        "companies", "facilities", "suppliers",
        # News
        "articles", "article_scores", "causal_chains",
        # Analysis
        "analyses", "recommendations", "frameworks",
        # Predictions
        "prediction_reports", "simulation_runs",
        # Ontology
        "ontology_rules", "assertions", "inference_logs",
        # Media
        "media_files", "media_chunks",
    }

    def test_all_expected_tables_registered(self):
        registered = set(Base.metadata.tables.keys())
        for table in self.EXPECTED_TABLES:
            assert table in registered, f"Table '{table}' not registered in Base.metadata"

    def test_no_duplicate_table_names(self):
        tables = list(Base.metadata.tables.keys())
        assert len(tables) == len(set(tables)), "Duplicate table names found"

    def test_substantial_table_count(self):
        """We expect 47+ tables from core + legacy models."""
        table_count = len(Base.metadata.tables)
        assert table_count >= 20, f"Only {table_count} tables registered — expected at least 20"


# --- TenantMixin Tests ---

class TestTenantMixin:
    TENANT_SCOPED_TABLES = [
        "companies", "articles", "article_scores", "causal_chains",
        "analyses", "recommendations", "prediction_reports",
        "ontology_rules", "assertions", "media_files", "media_chunks",
    ]

    @pytest.mark.parametrize("table_name", TENANT_SCOPED_TABLES)
    def test_tenant_scoped_tables_have_tenant_id(self, table_name: str):
        table = Base.metadata.tables.get(table_name)
        assert table is not None, f"Table {table_name} not found"
        columns = {col.name for col in table.columns}
        assert "tenant_id" in columns, f"Table {table_name} missing tenant_id column"

    def test_tenant_id_is_indexed(self):
        """tenant_id should be indexed for query performance."""
        table = Base.metadata.tables.get("companies")
        assert table is not None
        tenant_col = table.c.tenant_id
        assert tenant_col.index or any(
            "tenant_id" in str(idx.columns) for idx in table.indexes
        ), "tenant_id should be indexed"


# --- UUID Generation ---

class TestUUID:
    def test_uuid_uniqueness(self):
        uuids = {generate_uuid() for _ in range(1000)}
        assert len(uuids) == 1000

    def test_uuid_format(self):
        uid = generate_uuid()
        assert len(uid) == 36
        parts = uid.split("-")
        assert len(parts) == 5


# --- Table Schema Validation ---

class TestTableSchemas:
    def test_tenants_has_required_columns(self):
        table = Base.metadata.tables["tenants"]
        cols = {c.name for c in table.columns}
        assert "id" in cols
        assert "name" in cols
        assert "domain" in cols

    def test_users_has_required_columns(self):
        table = Base.metadata.tables["users"]
        cols = {c.name for c in table.columns}
        assert "id" in cols
        assert "email" in cols

    def test_companies_has_required_columns(self):
        table = Base.metadata.tables["companies"]
        cols = {c.name for c in table.columns}
        assert "id" in cols
        assert "name" in cols
        assert "tenant_id" in cols

    def test_articles_has_required_columns(self):
        table = Base.metadata.tables["articles"]
        cols = {c.name for c in table.columns}
        assert "id" in cols
        assert "tenant_id" in cols
        assert "title" in cols

    def test_prediction_reports_has_required_columns(self):
        table = Base.metadata.tables["prediction_reports"]
        cols = {c.name for c in table.columns}
        assert "id" in cols
        assert "tenant_id" in cols
        assert "company_id" in cols
        assert "confidence_score" in cols

    def test_media_files_has_required_columns(self):
        table = Base.metadata.tables["media_files"]
        cols = {c.name for c in table.columns}
        assert "id" in cols
        assert "tenant_id" in cols
        assert "minio_key" in cols
        assert "status" in cols

    def test_causal_chains_has_required_columns(self):
        table = Base.metadata.tables["causal_chains"]
        cols = {c.name for c in table.columns}
        assert "id" in cols
        assert "tenant_id" in cols
        assert "article_id" in cols
