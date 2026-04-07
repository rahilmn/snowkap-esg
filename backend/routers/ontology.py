"""Ontology router — SPARQL queries, rules, causal chains, assertions, explorer.

Per CLAUDE.md Rule #5: NEVER expose Jena SPARQL endpoint directly — always proxy through FastAPI.
SPARQL queries always scoped to tenant named graph: urn:snowkap:tenant:{tenant_id}

Per MASTER_BUILD_PLAN Phase 3.6: Ontology API
- SPARQL queries scoped to tenant named graph
- Rule CRUD (create, update, delete business rules)
- Inference dashboard (auto-derived vs human-asserted)
- Causal chain explorer: "Show me all paths from [news event] to [my company]"
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select

from backend.core.dependencies import TenantContext, get_tenant_context
from backend.core.redis import CACHE_TTL_ANALYSIS, cache_get, cache_set
from backend.models.ontology import Assertion, InferenceLog, OntologyRule
from backend.ontology.rule_compiler import compile_and_deploy_rule, deploy_assertion
from backend.services.ontology_service import (
    analyze_article_impact,
    execute_sparql,
    get_causal_chain_explorer,
    get_ontology_stats,
)

logger = structlog.get_logger()
router = APIRouter()


# --- Request / Response schemas ---

class SPARQLRequest(BaseModel):
    query: str


class RuleCreate(BaseModel):
    name: str
    description: str | None = None
    rule_type: str  # threshold, classification, relationship, material_issue, framework_mapping, geographic_risk
    condition: dict
    action: dict


class RuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    condition: dict | None = None
    action: dict | None = None
    is_active: bool | None = None


class RuleResponse(BaseModel):
    id: str
    name: str
    description: str | None
    rule_type: str
    is_active: bool
    owl_axiom: str | None = None


class AssertionCreate(BaseModel):
    subject_uri: str
    predicate_uri: str
    object_uri: str
    source: str | None = None


class AssertionResponse(BaseModel):
    id: str
    subject_uri: str
    predicate_uri: str
    object_uri: str
    assertion_type: str
    confidence: float
    asserted_by: str | None
    is_active: bool


class CausalExplorerRequest(BaseModel):
    entity_text: str
    max_hops: int = 4


class CausalExplorerResult(BaseModel):
    company_name: str
    company_uri: str
    max_impact_score: float
    min_hops: int
    all_paths_count: int
    best_explanation: str


class ArticleImpactRequest(BaseModel):
    article_id: str


class OntologyStatsResponse(BaseModel):
    companies: int = 0
    facilities: int = 0
    suppliers: int = 0
    commodities: int = 0
    material_issues: int = 0
    frameworks: int = 0
    regulations: int = 0
    causal_chains: int = 0


class InferenceDashboardResponse(BaseModel):
    total_rules: int
    active_rules: int
    total_assertions: int
    human_assertions: int
    auto_assertions: int
    total_inferences: int


# --- SPARQL Proxy ---

@router.post("/sparql")
async def sparql_proxy(
    req: SPARQLRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Execute a SPARQL query scoped to the tenant's named graph.

    Per CLAUDE.md: Each tenant gets a named graph urn:snowkap:tenant:{tenant_id}
    Destructive queries (DROP, DELETE, CLEAR) are blocked.
    """
    logger.info("sparql_query", tenant_id=ctx.tenant_id, query_length=len(req.query))
    return await execute_sparql(ctx.tenant_id, req.query)


# --- Ontology Stats ---

@router.get("/stats", response_model=OntologyStatsResponse)
async def ontology_stats(
    ctx: TenantContext = Depends(get_tenant_context),
) -> OntologyStatsResponse:
    """Get ontology graph statistics for this tenant."""
    stats = await get_ontology_stats(ctx.tenant_id)
    return OntologyStatsResponse(
        companies=stats.get("company_count", 0),
        facilities=stats.get("facility_count", 0),
        suppliers=stats.get("supplier_count", 0),
        commodities=stats.get("commodity_count", 0),
        material_issues=stats.get("materialissue_count", 0),
        frameworks=stats.get("framework_count", 0),
        regulations=stats.get("regulation_count", 0),
        causal_chains=stats.get("causalchain_count", 0),
    )


# --- Rules CRUD ---

@router.get("/rules", response_model=list[RuleResponse])
async def list_rules(
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[RuleResponse]:
    """List ontology rules for this tenant."""
    result = await ctx.db.execute(
        select(OntologyRule).where(OntologyRule.tenant_id == ctx.tenant_id)
    )
    rules = result.scalars().all()
    return [
        RuleResponse(
            id=r.id, name=r.name, description=r.description,
            rule_type=r.rule_type, is_active=r.is_active, owl_axiom=r.owl_axiom,
        )
        for r in rules
    ]


@router.post("/rules", response_model=RuleResponse, status_code=201)
async def create_rule(
    req: RuleCreate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> RuleResponse:
    """Create a new ontology rule and deploy to Jena — manage_ontology permission required."""
    if "manage_ontology" not in ctx.user.permissions:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No permission to manage ontology")

    # Compile and deploy to Jena
    rule_dict = {
        "name": req.name,
        "rule_type": req.rule_type,
        "condition": req.condition,
        "action": req.action,
    }
    deploy_result = await compile_and_deploy_rule(rule_dict, ctx.tenant_id)

    rule = OntologyRule(
        tenant_id=ctx.tenant_id,
        name=req.name,
        description=req.description,
        rule_type=req.rule_type,
        condition=req.condition,
        action=req.action,
        owl_axiom=deploy_result.get("owl_axiom"),
        created_by=ctx.user.user_id,
    )
    ctx.db.add(rule)

    # Log the inference
    inference = InferenceLog(
        tenant_id=ctx.tenant_id,
        rule_id=rule.id,
        inference_type="rule_deployment",
        input_data=rule_dict,
        output_triples=[{"owl_axiom": deploy_result.get("owl_axiom")}],
        confidence=1.0 if deploy_result["success"] else 0.0,
        status="completed" if deploy_result["success"] else "failed",
    )
    ctx.db.add(inference)
    await ctx.db.flush()

    logger.info("ontology_rule_created", rule_id=rule.id, name=rule.name, deployed=deploy_result["success"])
    return RuleResponse(
        id=rule.id, name=rule.name, description=rule.description,
        rule_type=rule.rule_type, is_active=rule.is_active, owl_axiom=rule.owl_axiom,
    )


@router.patch("/rules/{rule_id}", response_model=RuleResponse)
async def update_rule(
    rule_id: str,
    req: RuleUpdate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> RuleResponse:
    """Update an ontology rule."""
    if "manage_ontology" not in ctx.user.permissions:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No permission to manage ontology")

    result = await ctx.db.execute(
        select(OntologyRule).where(
            OntologyRule.id == rule_id,
            OntologyRule.tenant_id == ctx.tenant_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    if req.name is not None:
        rule.name = req.name
    if req.description is not None:
        rule.description = req.description
    if req.condition is not None:
        rule.condition = req.condition
    if req.action is not None:
        rule.action = req.action
    if req.is_active is not None:
        rule.is_active = req.is_active

    # Re-compile and deploy if condition or action changed
    if req.condition is not None or req.action is not None:
        rule_dict = {
            "name": rule.name,
            "rule_type": rule.rule_type,
            "condition": rule.condition,
            "action": rule.action,
        }
        deploy_result = await compile_and_deploy_rule(rule_dict, ctx.tenant_id)
        rule.owl_axiom = deploy_result.get("owl_axiom")

    await ctx.db.flush()
    return RuleResponse(
        id=rule.id, name=rule.name, description=rule.description,
        rule_type=rule.rule_type, is_active=rule.is_active, owl_axiom=rule.owl_axiom,
    )


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
) -> None:
    """Delete an ontology rule — manage_ontology permission required."""
    if "manage_ontology" not in ctx.user.permissions:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No permission to manage ontology")

    result = await ctx.db.execute(
        select(OntologyRule).where(
            OntologyRule.id == rule_id,
            OntologyRule.tenant_id == ctx.tenant_id,
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    await ctx.db.delete(rule)
    await ctx.db.flush()


# --- Assertions ---

@router.get("/assertions", response_model=list[AssertionResponse])
async def list_assertions(
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[AssertionResponse]:
    """List all human and auto assertions for this tenant."""
    result = await ctx.db.execute(
        select(Assertion).where(Assertion.tenant_id == ctx.tenant_id)
    )
    assertions = result.scalars().all()
    return [
        AssertionResponse(
            id=a.id, subject_uri=a.subject_uri, predicate_uri=a.predicate_uri,
            object_uri=a.object_uri, assertion_type=a.assertion_type,
            confidence=a.confidence, asserted_by=a.asserted_by, is_active=a.is_active,
        )
        for a in assertions
    ]


@router.post("/assertions", response_model=AssertionResponse, status_code=201)
async def create_assertion(
    req: AssertionCreate,
    ctx: TenantContext = Depends(get_tenant_context),
) -> AssertionResponse:
    """Create a human assertion and deploy to Jena."""
    # Deploy to Jena
    deployed = await deploy_assertion(
        req.subject_uri, req.predicate_uri, req.object_uri, ctx.tenant_id,
    )

    assertion = Assertion(
        tenant_id=ctx.tenant_id,
        subject_uri=req.subject_uri,
        predicate_uri=req.predicate_uri,
        object_uri=req.object_uri,
        assertion_type="human",
        confidence=1.0,
        asserted_by=ctx.user.user_id,
        source=req.source,
    )
    ctx.db.add(assertion)
    await ctx.db.flush()

    logger.info("assertion_created", assertion_id=assertion.id, deployed=deployed)
    return AssertionResponse(
        id=assertion.id, subject_uri=assertion.subject_uri,
        predicate_uri=assertion.predicate_uri, object_uri=assertion.object_uri,
        assertion_type=assertion.assertion_type, confidence=assertion.confidence,
        asserted_by=assertion.asserted_by, is_active=assertion.is_active,
    )


# --- Causal Chain Explorer ---

@router.post("/explore", response_model=list[CausalExplorerResult])
async def causal_chain_explore(
    req: CausalExplorerRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> list[CausalExplorerResult]:
    """Causal chain explorer: find all paths from an entity to tenant's companies.

    Per MASTER_BUILD_PLAN Phase 3.6: "Show me all paths from [news event] to [my company]"
    """
    impacts = await get_causal_chain_explorer(req.entity_text, ctx.tenant_id)
    return [
        CausalExplorerResult(
            company_name=i["company_name"],
            company_uri=i["company_uri"],
            max_impact_score=i["max_impact_score"],
            min_hops=i["min_hops"],
            all_paths_count=i["all_paths_count"],
            best_explanation=i["best_path"].explanation,
        )
        for i in impacts
    ]


# --- Article Impact Analysis ---

@router.post("/analyze-impact")
async def analyze_impact(
    req: ArticleImpactRequest,
    ctx: TenantContext = Depends(get_tenant_context),
) -> dict:
    """Run full impact analysis pipeline on an article. Result cached 24h per tenant."""
    cache_key = f"impact:{req.article_id}"
    cached = await cache_get(ctx.tenant_id, "analysis", cache_key)
    if cached is not None:
        return cached

    impacts = await analyze_article_impact(req.article_id, ctx.tenant_id, ctx.db)
    result = {
        "article_id": req.article_id,
        "impacts": impacts,
        "total_companies_affected": len(impacts),
    }
    if impacts:
        await cache_set(ctx.tenant_id, "analysis", cache_key, result, ttl=CACHE_TTL_ANALYSIS)
    return result


# --- Inference Dashboard ---

@router.get("/dashboard", response_model=InferenceDashboardResponse)
async def inference_dashboard(
    ctx: TenantContext = Depends(get_tenant_context),
) -> InferenceDashboardResponse:
    """Inference dashboard — auto-derived vs human-asserted stats.

    Per MASTER_BUILD_PLAN Phase 3.6.
    """
    # Rules stats
    rules_result = await ctx.db.execute(
        select(func.count(OntologyRule.id)).where(OntologyRule.tenant_id == ctx.tenant_id)
    )
    total_rules = rules_result.scalar() or 0

    active_rules_result = await ctx.db.execute(
        select(func.count(OntologyRule.id)).where(
            OntologyRule.tenant_id == ctx.tenant_id,
            OntologyRule.is_active == True,
        )
    )
    active_rules = active_rules_result.scalar() or 0

    # Assertions stats
    total_assertions_result = await ctx.db.execute(
        select(func.count(Assertion.id)).where(Assertion.tenant_id == ctx.tenant_id)
    )
    total_assertions = total_assertions_result.scalar() or 0

    human_assertions_result = await ctx.db.execute(
        select(func.count(Assertion.id)).where(
            Assertion.tenant_id == ctx.tenant_id,
            Assertion.assertion_type == "human",
        )
    )
    human_assertions = human_assertions_result.scalar() or 0

    # Inference log stats
    inferences_result = await ctx.db.execute(
        select(func.count(InferenceLog.id)).where(InferenceLog.tenant_id == ctx.tenant_id)
    )
    total_inferences = inferences_result.scalar() or 0

    return InferenceDashboardResponse(
        total_rules=total_rules,
        active_rules=active_rules,
        total_assertions=total_assertions,
        human_assertions=human_assertions,
        auto_assertions=total_assertions - human_assertions,
        total_inferences=total_inferences,
    )
