"""SQLAlchemy 2.0 models — import all for Alembic autogenerate."""

from backend.models.base import Base, TenantMixin  # noqa: F401
from backend.models.tenant import Tenant, TenantConfig, TenantMembership  # noqa: F401
from backend.models.user import MagicLink, User  # noqa: F401
from backend.models.company import Company, Facility, Supplier  # noqa: F401
from backend.models.news import Article, ArticleScore, CausalChain  # noqa: F401
from backend.models.analysis import Analysis, Framework, Recommendation  # noqa: F401
from backend.models.prediction import PredictionReport, SimulationRun  # noqa: F401
from backend.models.ontology import Assertion, InferenceLog, OntologyRule  # noqa: F401
from backend.models.legacy import (  # noqa: F401
    Adjudication, AgentRun, AiValidation, AnalyzedArticleHash,
    ApiUsageLog, BattleHistory, BusinessImpactCache, Campaign,
    CampaignCompany, CardEarningLog, CompanySasbMapping, CompetitiveAnalysisReport,
    Contact, CostSavingsLog, EsgAnalysisResult, Feedback,
    FormSubmission, FrameworkAnalysisResult, GameLeaderboard, GameProgress,
    GameRegion, InternalRecipient, Newsletter, PeerBenchmarkingCache,
    PeerBenchmarkingHistory, PlayerCard, ProgressiveEnhancementLog,
    RateLimit, RequestForm, SasbMetric, SavedArticle, SmeReview,
    SystemSettings, UserActivity,
)
