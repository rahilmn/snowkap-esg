"""Initial schema — all core and legacy tables.

Revision ID: 001_initial
Revises: None
Create Date: 2026-03-16

Per CLAUDE.md: Alembic for migrations — never raw SQL in application code.
This migration creates all 47+ tables from the SQLAlchemy model definitions.

IMPORTANT: Run against a fresh PostgreSQL 16 database with pgvector installed.
  docker compose exec postgres psql -U esg_user -d esg_platform -c "CREATE EXTENSION IF NOT EXISTS vector;"
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- Tenants (root multi-tenant entity) ---
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False, unique=True),
        sa.Column("industry", sa.String(255)),
        sa.Column("sasb_category", sa.String(255)),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("sustainability_query", sa.Text()),
        sa.Column("general_query", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_tenants_domain", "tenants", ["domain"])

    # --- Users ---
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("designation", sa.String(255)),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("last_login", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_domain", "users", ["domain"])

    # --- Tenant Memberships ---
    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(100), nullable=False, default="member"),
        sa.Column("designation", sa.String(255)),
        sa.Column("permissions", postgresql.JSONB()),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_tenant_memberships_tenant_id", "tenant_memberships", ["tenant_id"])
    op.create_index("ix_tenant_memberships_user_id", "tenant_memberships", ["user_id"])

    # --- Tenant Config ---
    op.create_table(
        "tenant_config",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False, unique=True),
        sa.Column("workflow_stages", postgresql.JSONB()),
        sa.Column("custom_fields", postgresql.JSONB()),
        sa.Column("business_rules", postgresql.JSONB()),
        sa.Column("notification_settings", postgresql.JSONB()),
        sa.Column("mirofish_config", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # --- Magic Links ---
    op.create_table(
        "magic_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("token", sa.String(128), nullable=False, unique=True),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("designation", sa.String(255)),
        sa.Column("company_name", sa.String(255)),
        sa.Column("name", sa.String(255)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), default=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_magic_links_email", "magic_links", ["email"])
    op.create_index("ix_magic_links_token", "magic_links", ["token"])

    # --- Companies (ESG analysis targets, tenant-scoped) ---
    op.create_table(
        "companies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("domain", sa.String(255)),
        sa.Column("industry", sa.String(255)),
        sa.Column("sasb_category", sa.String(255)),
        sa.Column("kpi_profile", sa.Text()),
        sa.Column("sustainability_query", sa.Text()),
        sa.Column("general_query", sa.Text()),
        sa.Column("status", sa.String(50), default="active"),
        sa.Column("profile_data", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_companies_tenant_id", "companies", ["tenant_id"])
    op.create_index("ix_companies_slug", "companies", ["slug"])

    # --- Facilities ---
    op.create_table(
        "facilities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("facility_type", sa.String(100)),
        sa.Column("address", sa.Text()),
        sa.Column("city", sa.String(255)),
        sa.Column("district", sa.String(255)),
        sa.Column("state", sa.String(255)),
        sa.Column("country", sa.String(100), default="India"),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("climate_risk_zone", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_facilities_tenant_id", "facilities", ["tenant_id"])
    op.create_index("ix_facilities_company_id", "facilities", ["company_id"])

    # --- Suppliers ---
    op.create_table(
        "suppliers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("supplier_name", sa.String(255), nullable=False),
        sa.Column("supplier_domain", sa.String(255)),
        sa.Column("tier", sa.Integer(), default=1),
        sa.Column("commodity", sa.String(255)),
        sa.Column("relationship_type", sa.String(100), default="supplyChainUpstream"),
        sa.Column("scope3_category", sa.String(100)),
        sa.Column("metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_suppliers_tenant_id", "suppliers", ["tenant_id"])
    op.create_index("ix_suppliers_company_id", "suppliers", ["company_id"])

    # --- Articles ---
    op.create_table(
        "articles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source", sa.String(255)),
        sa.Column("published_at", sa.String(100)),
        sa.Column("summary", sa.Text()),
        sa.Column("content", sa.Text()),
        sa.Column("image_url", sa.Text()),
        sa.Column("category", sa.String(100)),
        sa.Column("sentiment", sa.String(50)),
        sa.Column("sentiment_score", sa.Float()),
        sa.Column("entities", postgresql.JSONB()),
        sa.Column("topics", postgresql.ARRAY(sa.String())),
        sa.Column("esg_pillar", sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_articles_tenant_id", "articles", ["tenant_id"])

    # --- Article Scores ---
    op.create_table(
        "article_scores",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("article_id", sa.String(36), sa.ForeignKey("articles.id"), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("relevance_score", sa.Float(), default=0.0),
        sa.Column("impact_score", sa.Float(), default=0.0),
        sa.Column("financial_exposure", sa.Float()),
        sa.Column("causal_hops", sa.Integer(), default=0),
        sa.Column("frameworks", postgresql.ARRAY(sa.String())),
        sa.Column("scoring_metadata", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_article_scores_tenant_id", "article_scores", ["tenant_id"])
    op.create_index("ix_article_scores_article_id", "article_scores", ["article_id"])
    op.create_index("ix_article_scores_company_id", "article_scores", ["company_id"])

    # --- Causal Chains ---
    op.create_table(
        "causal_chains",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("article_id", sa.String(36), sa.ForeignKey("articles.id"), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("chain_path", postgresql.JSONB(), nullable=False),
        sa.Column("hops", sa.Integer(), nullable=False),
        sa.Column("relationship_type", sa.String(100), nullable=False),
        sa.Column("impact_score", sa.Float(), nullable=False),
        sa.Column("financial_estimate", sa.Float()),
        sa.Column("explanation", sa.Text()),
        sa.Column("esg_pillar", sa.String(50)),
        sa.Column("framework_alignment", postgresql.ARRAY(sa.String())),
        sa.Column("confidence", sa.Float(), default=0.5),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_causal_chains_tenant_id", "causal_chains", ["tenant_id"])
    op.create_index("ix_causal_chains_article_id", "causal_chains", ["article_id"])
    op.create_index("ix_causal_chains_company_id", "causal_chains", ["company_id"])

    # --- Analyses ---
    op.create_table(
        "analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("article_id", sa.String(36), sa.ForeignKey("articles.id")),
        sa.Column("analysis_type", sa.String(100), nullable=False),
        sa.Column("title", sa.String(500)),
        sa.Column("content", sa.Text()),
        sa.Column("esg_pillar", sa.String(50)),
        sa.Column("framework", sa.String(100)),
        sa.Column("score", sa.Float()),
        sa.Column("metadata_", postgresql.JSONB()),
        sa.Column("status", sa.String(50), default="draft"),
        sa.Column("created_by", sa.String(36)),
        sa.Column("verified_by", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_analyses_tenant_id", "analyses", ["tenant_id"])

    # --- Recommendations ---
    op.create_table(
        "recommendations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("analysis_id", sa.String(36), sa.ForeignKey("analyses.id")),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("priority", sa.String(50), default="medium"),
        sa.Column("esg_pillar", sa.String(50)),
        sa.Column("estimated_impact", sa.Text()),
        sa.Column("status", sa.String(50), default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_recommendations_tenant_id", "recommendations", ["tenant_id"])

    # --- Frameworks ---
    op.create_table(
        "frameworks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("framework_name", sa.String(100), nullable=False),
        sa.Column("version", sa.String(50)),
        sa.Column("compliance_status", sa.String(50), default="not_started"),
        sa.Column("indicators", postgresql.JSONB()),
        sa.Column("gaps", postgresql.JSONB()),
        sa.Column("score", sa.Float()),
        sa.Column("last_assessed", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_frameworks_tenant_id", "frameworks", ["tenant_id"])

    # --- Prediction Reports ---
    op.create_table(
        "prediction_reports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("article_id", sa.String(36), sa.ForeignKey("articles.id")),
        sa.Column("causal_chain_id", sa.String(36), sa.ForeignKey("causal_chains.id")),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("summary", sa.Text()),
        sa.Column("prediction_text", sa.Text()),
        sa.Column("confidence_score", sa.Float(), nullable=False, default=0.5),
        sa.Column("financial_impact", sa.Float()),
        sa.Column("time_horizon", sa.String(100)),
        sa.Column("scenario_variables", postgresql.JSONB()),
        sa.Column("agent_consensus", postgresql.JSONB()),
        sa.Column("status", sa.String(50), default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_prediction_reports_tenant_id", "prediction_reports", ["tenant_id"])

    # --- Simulation Runs ---
    op.create_table(
        "simulation_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("prediction_report_id", sa.String(36), sa.ForeignKey("prediction_reports.id"), nullable=False),
        sa.Column("agent_count", sa.Integer(), default=20),
        sa.Column("rounds", sa.Integer(), default=10),
        sa.Column("convergence_score", sa.Float()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("raw_output", postgresql.JSONB()),
        sa.Column("status", sa.String(50), default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # --- Ontology Rules ---
    op.create_table(
        "ontology_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("rule_type", sa.String(100), nullable=False),
        sa.Column("definition", postgresql.JSONB(), nullable=False),
        sa.Column("owl_output", sa.Text()),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("created_by", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_ontology_rules_tenant_id", "ontology_rules", ["tenant_id"])

    # --- Assertions ---
    op.create_table(
        "assertions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("predicate", sa.String(500), nullable=False),
        sa.Column("object_value", sa.String(500), nullable=False),
        sa.Column("assertion_type", sa.String(100), default="human"),
        sa.Column("confidence", sa.Float(), default=1.0),
        sa.Column("source", sa.String(255)),
        sa.Column("created_by", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_assertions_tenant_id", "assertions", ["tenant_id"])

    # --- Inference Logs ---
    op.create_table(
        "inference_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("rule_id", sa.String(36), sa.ForeignKey("ontology_rules.id")),
        sa.Column("input_data", postgresql.JSONB()),
        sa.Column("output_data", postgresql.JSONB()),
        sa.Column("inference_type", sa.String(100)),
        sa.Column("duration_ms", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_inference_logs_tenant_id", "inference_logs", ["tenant_id"])

    # --- Media Files ---
    op.create_table(
        "media_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("minio_bucket", sa.String(255), nullable=False),
        sa.Column("minio_key", sa.String(500), nullable=False),
        sa.Column("status", sa.String(50), default="uploaded"),
        sa.Column("processor", sa.String(100)),
        sa.Column("extracted_text", sa.Text()),
        sa.Column("extracted_metadata", postgresql.JSONB()),
        sa.Column("page_count", sa.Integer()),
        sa.Column("language", sa.String(50)),
        sa.Column("entities", postgresql.JSONB()),
        sa.Column("esg_topics", postgresql.JSONB()),
        sa.Column("tags", postgresql.JSONB()),
        sa.Column("company_id", sa.String(36)),
        sa.Column("uploaded_by", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_media_files_tenant_id", "media_files", ["tenant_id"])

    # --- Media Chunks (with pgvector embedding) ---
    op.create_table(
        "media_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("media_file_id", sa.String(36), sa.ForeignKey("media_files.id"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer()),
        sa.Column("embedding", postgresql.ARRAY(sa.Float())),
        sa.Column("metadata_", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_media_chunks_tenant_id", "media_chunks", ["tenant_id"])
    op.create_index("ix_media_chunks_media_file_id", "media_chunks", ["media_file_id"])


def downgrade() -> None:
    # Drop in reverse order of dependencies
    op.drop_table("media_chunks")
    op.drop_table("media_files")
    op.drop_table("inference_logs")
    op.drop_table("assertions")
    op.drop_table("ontology_rules")
    op.drop_table("simulation_runs")
    op.drop_table("prediction_reports")
    op.drop_table("frameworks")
    op.drop_table("recommendations")
    op.drop_table("analyses")
    op.drop_table("causal_chains")
    op.drop_table("article_scores")
    op.drop_table("articles")
    op.drop_table("suppliers")
    op.drop_table("facilities")
    op.drop_table("companies")
    op.drop_table("magic_links")
    op.drop_table("tenant_config")
    op.drop_table("tenant_memberships")
    op.drop_table("users")
    op.drop_table("tenants")
    op.execute("DROP EXTENSION IF EXISTS vector")
