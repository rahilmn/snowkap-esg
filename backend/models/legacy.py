"""Legacy tables translated from Drizzle schema (shared/schema.ts).

Per MASTER_BUILD_PLAN Phase 2: Translate 47 Drizzle tables → SQLAlchemy with tenant_id.
Per CLAUDE.md Rule #4: NEVER modify shared/schema.ts — all new work here.

These models add tenant_id to every table for multi-tenant isolation.
Tables from the legacy schema that don't map to the new domain models.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import Base, TenantMixin, generate_uuid

# pgvector support
try:
    from pgvector.sqlalchemy import Vector
except ImportError:
    Vector = None


# ---------------------------------------------------------------------------
# Framework Analysis & Validation (from frameworkAnalysis, aiValidation, smeReview)
# ---------------------------------------------------------------------------

class FrameworkAnalysisResult(Base, TenantMixin):
    """ESG framework analysis for a specific article + company."""
    __tablename__ = "framework_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    framework: Mapped[str] = mapped_column(String(100), nullable=False)
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False)
    sentiment: Mapped[str] = mapped_column(String(50), nullable=False)
    materiality_score: Mapped[float | None] = mapped_column(Float)
    key_findings: Mapped[dict] = mapped_column(JSONB, nullable=False)
    recommendations: Mapped[dict] = mapped_column(JSONB, nullable=False)
    applicable_criteria: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    impact_assessment: Mapped[str] = mapped_column(Text, nullable=False)


class AiValidation(Base, TenantMixin):
    """AI validation results for an article."""
    __tablename__ = "ai_validation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    validation_results: Mapped[dict] = mapped_column(JSONB, nullable=False)
    selected_framework: Mapped[str] = mapped_column(String(100), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    comparison_table: Mapped[dict] = mapped_column(JSONB, nullable=False)


class SmeReview(Base, TenantMixin):
    """SME review of an AI validation."""
    __tablename__ = "sme_review"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    validation_id: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    feedback: Mapped[str | None] = mapped_column(Text)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False)


# ---------------------------------------------------------------------------
# Newsletter & Campaigns (from newsletter, campaigns, campaignCompanies)
# ---------------------------------------------------------------------------

class Newsletter(Base, TenantMixin):
    """Newsletter content generated from article analysis."""
    __tablename__ = "newsletters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[str] = mapped_column(String(255), nullable=False)
    validation_id: Mapped[int | None] = mapped_column(Integer)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    newsletter_type: Mapped[str] = mapped_column(String(100), nullable=False)
    sentiment: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    html_content: Mapped[str] = mapped_column(Text, nullable=False)
    has_watermark: Mapped[bool] = mapped_column(Boolean, default=False)
    watermark_url: Mapped[str | None] = mapped_column(Text)
    distribution_list: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    recipient_email: Mapped[str | None] = mapped_column(String(255))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Campaign(Base, TenantMixin):
    """Email campaign for sales outreach."""
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_slug: Mapped[str] = mapped_column(String(255), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_type: Mapped[str] = mapped_column(String(100), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    html_content: Mapped[str] = mapped_column(Text, nullable=False)
    text_content: Mapped[str | None] = mapped_column(Text)
    recipients: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    sender_email: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_name: Mapped[str] = mapped_column(String(255), nullable=False)
    reply_to_email: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="draft")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    article_ids: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by_name: Mapped[str] = mapped_column(String(255), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)


class CampaignCompany(Base, TenantMixin):
    """Company available for campaign targeting."""
    __tablename__ = "campaign_companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    industry: Mapped[str] = mapped_column(String(255), nullable=False)
    esg_keywords: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    general_keywords: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------------------------------------------------------------------------
# Contacts (from contacts)
# ---------------------------------------------------------------------------

class Contact(Base, TenantMixin):
    """Contact for campaign management."""
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_type: Mapped[str] = mapped_column(String(100), nullable=False)
    job_title: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(100))
    department: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    last_contacted: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    engagement_score: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------------------
# Feedback & Saved Articles
# ---------------------------------------------------------------------------

class Feedback(Base, TenantMixin):
    """User feedback."""
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    company_id: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    priority: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="new")
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SavedArticle(Base, TenantMixin):
    """User's saved articles."""
    __tablename__ = "saved_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    article_id: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    article_data: Mapped[dict] = mapped_column(JSONB, nullable=False)


# ---------------------------------------------------------------------------
# Competitive Intelligence & Benchmarking
# ---------------------------------------------------------------------------

class CompetitiveAnalysisReport(Base, TenantMixin):
    """Competitive analysis report."""
    __tablename__ = "competitive_analysis_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_ids: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    report_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    executive_summary: Mapped[str] = mapped_column(Text, nullable=False)
    performance_gaps: Mapped[dict] = mapped_column(JSONB, nullable=False)
    citations: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BusinessImpactCache(Base, TenantMixin):
    """Cached business impact analysis."""
    __tablename__ = "business_impact_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    impact_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PeerBenchmarkingCache(Base, TenantMixin):
    """Cached peer benchmarking assessment."""
    __tablename__ = "peer_benchmarking_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    bmc_assessment: Mapped[dict] = mapped_column(JSONB, nullable=False)
    overall_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)
    peer_comparison: Mapped[dict] = mapped_column(JSONB, nullable=False)
    peer_list: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    all_citations: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PeerBenchmarkingHistory(Base, TenantMixin):
    """Quarterly peer benchmarking snapshots."""
    __tablename__ = "peer_benchmarking_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    quarter: Mapped[str] = mapped_column(String(20), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    weighted_score: Mapped[float] = mapped_column(Float, nullable=False)
    average_score: Mapped[float] = mapped_column(Float, nullable=False)
    peer_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    total_peers: Mapped[int] = mapped_column(Integer, nullable=False)
    industry_average: Mapped[float] = mapped_column(Float, nullable=False)
    component_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)
    peer_scores: Mapped[dict] = mapped_column(JSONB, nullable=False)


# ---------------------------------------------------------------------------
# Usage Tracking & Cost Optimization
# ---------------------------------------------------------------------------

class ApiUsageLog(Base, TenantMixin):
    """API usage tracking for cost management."""
    __tablename__ = "api_usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service: Mapped[str] = mapped_column(String(100), nullable=False)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(50))
    request_id: Mapped[str | None] = mapped_column(String(255))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)


class CostSavingsLog(Base, TenantMixin):
    """Cost savings from optimization strategies."""
    __tablename__ = "cost_savings_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    optimization_type: Mapped[str] = mapped_column(String(100), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255))
    article_id: Mapped[str | None] = mapped_column(String(255))
    saved_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    request_id: Mapped[str | None] = mapped_column(String(255))


# ---------------------------------------------------------------------------
# Article Deduplication & Content Hashes
# ---------------------------------------------------------------------------

class AnalyzedArticleHash(Base, TenantMixin):
    """Article content hash for deduplication."""
    __tablename__ = "analyzed_article_hashes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_hash: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    article_id: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    skip_reason: Mapped[str | None] = mapped_column(Text)
    # embedding: Vector(1536) — added via raw migration for pgvector


# ---------------------------------------------------------------------------
# ESG Analysis Results (enhanced multi-framework)
# ---------------------------------------------------------------------------

class EsgAnalysisResult(Base, TenantMixin):
    """Enhanced ESG analysis result with multi-framework + SASB."""
    __tablename__ = "esg_analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    article_title: Mapped[str] = mapped_column(Text, nullable=False)
    article_url: Mapped[str | None] = mapped_column(Text)
    company_slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sasb_industry: Mapped[str | None] = mapped_column(String(255))
    sasb_topics: Mapped[dict | None] = mapped_column(JSONB)
    sasb_materiality_score: Mapped[float | None] = mapped_column(Float)
    framework_analyses: Mapped[dict] = mapped_column(JSONB, nullable=False)
    overall_sentiment: Mapped[str | None] = mapped_column(String(50))
    criticality_score: Mapped[int | None] = mapped_column(Integer)
    key_insights: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    strategic_recommendations: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    discussion_summary: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Agent Runs & Adjudications
# ---------------------------------------------------------------------------

class AgentRun(Base, TenantMixin):
    """ESG analysis agent run results."""
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    sentiment: Mapped[str | None] = mapped_column(String(50))
    materiality_score: Mapped[int | None] = mapped_column(Integer)
    severity_score: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    reasoning_brief: Mapped[str | None] = mapped_column(Text)


class Adjudication(Base, TenantMixin):
    """Final sentiment adjudication results."""
    __tablename__ = "adjudications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    final_label: Mapped[str] = mapped_column(String(100), nullable=False)
    final_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    agreement_ratio: Mapped[float | None] = mapped_column(Float)
    composite_reasoning: Mapped[str | None] = mapped_column(Text)
    chosen_method: Mapped[str | None] = mapped_column(String(100))


# ---------------------------------------------------------------------------
# SASB Metrics & Mapping
# ---------------------------------------------------------------------------

class SasbMetric(Base):
    """SASB metric definitions — shared across tenants (reference data)."""
    __tablename__ = "sasb_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    metric_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    metric_name: Mapped[str] = mapped_column(String(500), nullable=False)
    metric_description: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    topic_description: Mapped[str | None] = mapped_column(Text)
    sector: Mapped[str] = mapped_column(String(255), nullable=False)
    industry: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    quantitative_qualitative: Mapped[str | None] = mapped_column(String(100))
    unit: Mapped[str | None] = mapped_column(String(255))
    sasb_dimension: Mapped[str | None] = mapped_column(String(255))
    materiality_score: Mapped[int | None] = mapped_column(Integer)
    strategic_importance: Mapped[str | None] = mapped_column(String(50))
    keywords: Mapped[list[str] | None] = mapped_column(ARRAY(String))


class CompanySasbMapping(Base, TenantMixin):
    """Company-to-SASB industry mapping."""
    __tablename__ = "company_sasb_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sasb_industry: Mapped[str] = mapped_column(String(255), nullable=False)
    sasb_sector: Mapped[str] = mapped_column(String(255), nullable=False)
    material_topics: Mapped[list[str] | None] = mapped_column(ARRAY(String))


# ---------------------------------------------------------------------------
# User Activities
# ---------------------------------------------------------------------------

class UserActivity(Base, TenantMixin):
    """User engagement tracking."""
    __tablename__ = "user_activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    user_email: Mapped[str] = mapped_column(String(255), nullable=False)
    company_id: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    ip_hash: Mapped[str | None] = mapped_column(String(255))
    user_agent: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Gamification (ESG Defender)
# ---------------------------------------------------------------------------

class PlayerCard(Base, TenantMixin):
    """Player's card collection for ESG Defender game."""
    __tablename__ = "player_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    card_type: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    earned_from: Mapped[str | None] = mapped_column(String(100))


class GameProgress(Base, TenantMixin):
    """Player game progress."""
    __tablename__ = "game_progress"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    total_points: Mapped[int] = mapped_column(Integer, default=0)
    cities_saved: Mapped[int] = mapped_column(Integer, default=0)
    monsters_defeated: Mapped[int] = mapped_column(Integer, default=0)
    battles_won: Mapped[int] = mapped_column(Integer, default=0)
    battles_lost: Mapped[int] = mapped_column(Integer, default=0)
    current_streak: Mapped[int] = mapped_column(Integer, default=0)
    longest_streak: Mapped[int] = mapped_column(Integer, default=0)
    articles_read: Mapped[int] = mapped_column(Integer, default=0)
    analyses_run: Mapped[int] = mapped_column(Integer, default=0)
    last_login_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    saved_regions: Mapped[list[str] | None] = mapped_column(ARRAY(String))


class BattleHistory(Base, TenantMixin):
    """Battle history for ESG Defender game."""
    __tablename__ = "battle_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    region_id: Mapped[str] = mapped_column(String(255), nullable=False)
    monster_type: Mapped[str] = mapped_column(String(100), nullable=False)
    result: Mapped[str] = mapped_column(String(50), nullable=False)
    points_earned: Mapped[int] = mapped_column(Integer, default=0)
    xp_earned: Mapped[int] = mapped_column(Integer, default=0)
    cards_used: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    damage_dealt: Mapped[int] = mapped_column(Integer, default=0)
    damage_taken: Mapped[int] = mapped_column(Integer, default=0)
    turns_played: Mapped[int] = mapped_column(Integer, default=0)


class GameRegion(Base):
    """Game regions — shared across tenants."""
    __tablename__ = "game_regions"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str] = mapped_column(String(100), default="India")
    current_monster: Mapped[str | None] = mapped_column(String(100))
    monster_hp: Mapped[int | None] = mapped_column(Integer)
    threat_level: Mapped[int] = mapped_column(Integer, default=1)
    times_defended: Mapped[int] = mapped_column(Integer, default=0)
    last_saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    respawns_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CardEarningLog(Base, TenantMixin):
    """Card earning log — what actions gave what cards."""
    __tablename__ = "card_earning_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    card_type: Mapped[str] = mapped_column(String(100), nullable=False)
    card_rarity: Mapped[str] = mapped_column(String(50), nullable=False)
    earned_from: Mapped[str] = mapped_column(String(100), nullable=False)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)


class GameLeaderboard(Base, TenantMixin):
    """Game leaderboard entries."""
    __tablename__ = "game_leaderboard"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_name: Mapped[str] = mapped_column(String(255), nullable=False)
    company_slug: Mapped[str | None] = mapped_column(String(255))
    total_points: Mapped[int] = mapped_column(Integer, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    cities_saved: Mapped[int] = mapped_column(Integer, default=0)
    rank: Mapped[int | None] = mapped_column(Integer)
    period: Mapped[str] = mapped_column(String(50), nullable=False)


# ---------------------------------------------------------------------------
# System-level tables (no tenant_id — platform-wide)
# ---------------------------------------------------------------------------

class SystemSettings(Base):
    """Platform-wide system settings (single row)."""
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    auto_analysis_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_analysis_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analysis_in_progress: Mapped[bool] = mapped_column(Boolean, default=False)
    current_analysis_company: Mapped[str | None] = mapped_column(String(255))
    daily_business_impact_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    weekday_only_refresh: Mapped[bool] = mapped_column(Boolean, default=True)
    delta_check_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    progressive_enhancement_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence_threshold: Mapped[int] = mapped_column(Integer, default=85)
    adaptive_caching_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    base_cache_duration_hours: Mapped[int] = mapped_column(Integer, default=72)
    extended_cache_duration_hours: Mapped[int] = mapped_column(Integer, default=168)
    low_activity_threshold: Mapped[int] = mapped_column(Integer, default=5)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(String(255))


class ProgressiveEnhancementLog(Base, TenantMixin):
    """Progressive enhancement quality tracking."""
    __tablename__ = "progressive_enhancement_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation: Mapped[str] = mapped_column(String(100), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255))
    article_id: Mapped[str | None] = mapped_column(String(255))
    tier1_model: Mapped[str] = mapped_column(String(100), nullable=False)
    tier1_confidence: Mapped[float | None] = mapped_column(Float)
    tier1_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    tier1_completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    tier1_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)
    escalation_reason: Mapped[str | None] = mapped_column(Text)
    tier2_model: Mapped[str | None] = mapped_column(String(100))
    tier2_prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    tier2_completion_tokens: Mapped[int | None] = mapped_column(Integer)
    tier2_cost_usd: Mapped[float | None] = mapped_column(Float)
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    cost_savings: Mapped[float | None] = mapped_column(Float)
    accuracy_score: Mapped[float | None] = mapped_column(Float)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)


class RateLimit(Base):
    """Rate limiting — platform-wide."""
    __tablename__ = "rate_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    request_type: Mapped[str] = mapped_column(String(100), nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, default=1)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Internal Adoption System
# ---------------------------------------------------------------------------

class InternalRecipient(Base):
    """Internal team members for weekly request forms."""
    __tablename__ = "internal_recipients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    role: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class RequestForm(Base):
    """Weekly request forms sent to recipients."""
    __tablename__ = "request_forms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    week_number: Mapped[int] = mapped_column(Integer, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reminders_sent: Mapped[int] = mapped_column(Integer, default=0)
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(50), default="active")


class FormSubmission(Base):
    """Individual form submissions from recipients."""
    __tablename__ = "form_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_id: Mapped[int] = mapped_column(Integer, nullable=False)
    recipient_id: Mapped[int] = mapped_column(Integer, nullable=False)
    response: Mapped[str] = mapped_column(String(50), nullable=False)
    company_slug: Mapped[str | None] = mapped_column(String(255))
    topic_id: Mapped[int | None] = mapped_column(Integer)
    custom_topic_name: Mapped[str | None] = mapped_column(String(500))
