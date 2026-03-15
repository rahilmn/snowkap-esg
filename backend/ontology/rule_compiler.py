"""Business rule compiler — tenant rules → OWL axioms → Jena named graph.

Per MASTER_BUILD_PLAN Phase 3.5: Tenant Business Rules as OWL Axioms
- BusinessRuleCompiler: tenant rules → OWL axioms → Jena named graph
- Mathematical inference: threshold-based auto-classification
- Human assertion: domain-specific classifications via admin UI
- Permission-gated: admin creates rules, users assert facts
"""

import structlog

from backend.ontology.jena_client import jena_client

logger = structlog.get_logger()

SNOWKAP_NS = "http://snowkap.com/ontology/esg#"


def compile_rule_to_owl(rule: dict) -> str | None:
    """Compile a business rule definition into OWL axiom Turtle syntax.

    Supported rule types:
    - threshold: "If property > value, classify as X"
    - classification: "Entity X is of type Y"
    - relationship: "Entity X relates to Entity Y via predicate Z"
    - material_issue: "Industry X has material issue Y"
    - framework_mapping: "Issue X maps to framework Y indicator Z"
    - geographic_risk: "Region X has risk type Y"
    """
    rule_type = rule.get("rule_type", "")

    compilers = {
        "threshold": _compile_threshold_rule,
        "classification": _compile_classification_rule,
        "relationship": _compile_relationship_rule,
        "material_issue": _compile_material_issue_rule,
        "framework_mapping": _compile_framework_mapping_rule,
        "geographic_risk": _compile_geographic_risk_rule,
    }

    compiler = compilers.get(rule_type)
    if compiler:
        return compiler(rule)

    logger.warning("unknown_rule_type", rule_type=rule_type)
    return None


def _compile_threshold_rule(rule: dict) -> str:
    """Compile a threshold-based rule to OWL restriction.

    Example: "If emissions > 1000 tons, classify as high_emitter"
    """
    condition = rule.get("condition", {})
    action = rule.get("action", {})

    property_name = condition.get("property", "hasValue")
    threshold = condition.get("threshold", 0)
    target_class = action.get("classify_as", "HighImpact")

    return f"""\
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix snowkap: <{SNOWKAP_NS}> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

# Rule: {rule.get('name', 'unnamed')}
snowkap:{target_class} owl:equivalentClass [
    a owl:Restriction ;
    owl:onProperty snowkap:{property_name} ;
    owl:minExclusive "{threshold}"^^xsd:float
] .
"""


def _compile_classification_rule(rule: dict) -> str:
    """Compile a classification assertion to RDF triple.

    Example: "Company X is in renewable_energy sector"
    """
    condition = rule.get("condition", {})
    action = rule.get("action", {})

    subject = condition.get("subject", "Unknown")
    target_class = action.get("classify_as", "Unknown")

    subject_slug = subject.replace(" ", "_")
    class_slug = target_class.replace(" ", "_")

    return f"""\
@prefix snowkap: <{SNOWKAP_NS}> .

# Rule: {rule.get('name', 'unnamed')}
snowkap:{subject_slug} a snowkap:{class_slug} .
"""


def _compile_relationship_rule(rule: dict) -> str:
    """Compile a relationship rule to RDF triple.

    Example: "Company X sources from Supplier Y"
    """
    condition = rule.get("condition", {})

    subject = condition.get("subject", "").replace(" ", "_")
    predicate = condition.get("predicate", "relatedTo")
    obj = condition.get("object", "").replace(" ", "_")

    return f"""\
@prefix snowkap: <{SNOWKAP_NS}> .

# Rule: {rule.get('name', 'unnamed')}
snowkap:{subject} snowkap:{predicate} snowkap:{obj} .
"""


def _compile_material_issue_rule(rule: dict) -> str:
    """Compile material issue → company linkage.

    Example: "Transportation industry has material issue: fuel_management"
    """
    condition = rule.get("condition", {})
    action = rule.get("action", {})

    industry = condition.get("industry", "").replace(" ", "_")
    issue = action.get("issue", "").replace(" ", "_")
    pillar = action.get("esg_pillar", "E")

    return f"""\
@prefix snowkap: <{SNOWKAP_NS}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

# Rule: {rule.get('name', 'unnamed')}
snowkap:issue_{issue} a snowkap:MaterialIssue ;
    rdfs:label "{issue.replace('_', ' ')}" ;
    snowkap:esgPillar "{pillar}" .

snowkap:industry_{industry} snowkap:hasMaterialIssue snowkap:issue_{issue} .
"""


def _compile_framework_mapping_rule(rule: dict) -> str:
    """Map a material issue to a framework indicator.

    Example: "emissions issue maps to BRSR Principle 6, GRI 305"
    """
    condition = rule.get("condition", {})
    action = rule.get("action", {})

    issue = condition.get("issue", "").replace(" ", "_")
    framework = action.get("framework", "")
    indicator = action.get("indicator", "")

    return f"""\
@prefix snowkap: <{SNOWKAP_NS}> .

# Rule: {rule.get('name', 'unnamed')}
snowkap:issue_{issue} snowkap:reportsUnder snowkap:{framework} .
snowkap:issue_{issue} snowkap:frameworkIndicator "{framework}:{indicator}" .
"""


def _compile_geographic_risk_rule(rule: dict) -> str:
    """Assign a climate/disaster risk to a geographic region.

    Example: "Mumbai region has coastal_flood risk"
    """
    condition = rule.get("condition", {})
    action = rule.get("action", {})

    region = condition.get("region", "").lower().replace(" ", "_")
    risk_type = action.get("risk_type", "")

    return f"""\
@prefix snowkap: <{SNOWKAP_NS}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

# Rule: {rule.get('name', 'unnamed')}
snowkap:region_{region} a snowkap:GeographicRegion ;
    snowkap:climateRiskZone "{risk_type}" .
"""


async def compile_and_deploy_rule(
    rule: dict,
    tenant_id: str,
) -> dict:
    """Compile a rule to OWL and deploy it to the tenant's Jena graph.

    Returns: {"success": bool, "owl_axiom": str, "error": str|None}
    """
    owl = compile_rule_to_owl(rule)
    if not owl:
        return {"success": False, "owl_axiom": None, "error": f"Unknown rule type: {rule.get('rule_type')}"}

    success = await jena_client.upload_ttl(
        owl,
        graph_uri=jena_client._tenant_graph(tenant_id),
    )

    if success:
        logger.info("rule_deployed", rule_name=rule.get("name"), tenant_id=tenant_id)
    else:
        logger.error("rule_deploy_failed", rule_name=rule.get("name"), tenant_id=tenant_id)

    return {
        "success": success,
        "owl_axiom": owl,
        "error": None if success else "Failed to upload to Jena",
    }


async def deploy_assertion(
    subject_uri: str,
    predicate_uri: str,
    object_uri: str,
    tenant_id: str,
) -> bool:
    """Deploy a human assertion as a triple to the tenant's Jena graph."""
    triples = [(f"<{subject_uri}>", f"<{predicate_uri}>", f"<{object_uri}>")]
    return await jena_client.insert_triples(triples, tenant_id)
