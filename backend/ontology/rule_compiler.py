"""Business rule compiler — tenant rules → OWL axioms → Jena named graph.

Per MASTER_BUILD_PLAN Phase 3.5: Tenant Business Rules as OWL Axioms
- BusinessRuleCompiler: tenant rules → OWL axioms → Jena named graph
- Mathematical inference: threshold-based auto-classification
- Human assertion: domain-specific classifications via admin UI
- Permission-gated: admin creates rules, users assert facts

Stage 2.6: URI encoding via urllib.parse.quote (not just space replacement).
Required field validation. Duplicate detection before OWL deployment.
"""

import urllib.parse

import structlog

from backend.ontology.jena_client import jena_client

logger = structlog.get_logger()

SNOWKAP_NS = "http://snowkap.com/ontology/esg#"

# Track deployed rules per tenant for duplicate detection
_deployed_rules: dict[str, set[str]] = {}


class RuleValidationError(ValueError):
    """Raised when a rule is missing required fields."""
    pass


def _encode_uri_segment(text: str) -> str:
    """Safely encode a string for use as a URI segment.

    Stage 2.6: Proper URI encoding instead of just space replacement.
    """
    return urllib.parse.quote(text.strip().replace(" ", "_"), safe="")


def _validate_required(rule: dict, *fields: str) -> None:
    """Validate that required fields are present and non-empty."""
    for field_path in fields:
        parts = field_path.split(".")
        value = rule
        for part in parts:
            if not isinstance(value, dict):
                raise RuleValidationError(f"Missing required field: {field_path}")
            value = value.get(part)
            if value is None:
                raise RuleValidationError(f"Missing required field: {field_path}")
        if isinstance(value, str) and not value.strip():
            raise RuleValidationError(f"Empty required field: {field_path}")


def _get_rule_fingerprint(rule: dict) -> str:
    """Generate a fingerprint for duplicate detection."""
    rule_type = rule.get("rule_type", "")
    condition = str(sorted(rule.get("condition", {}).items()))
    action = str(sorted(rule.get("action", {}).items()))
    return f"{rule_type}:{condition}:{action}"


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
    """Compile a threshold-based rule to OWL restriction."""
    _validate_required(rule, "condition.property", "condition.threshold", "action.classify_as")

    condition = rule["condition"]
    action = rule["action"]

    property_name = _encode_uri_segment(condition["property"])
    threshold = condition["threshold"]
    target_class = _encode_uri_segment(action["classify_as"])

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
    """Compile a classification assertion to RDF triple."""
    _validate_required(rule, "condition.subject", "action.classify_as")

    condition = rule["condition"]
    action = rule["action"]

    subject_slug = _encode_uri_segment(condition["subject"])
    class_slug = _encode_uri_segment(action["classify_as"])

    return f"""\
@prefix snowkap: <{SNOWKAP_NS}> .

# Rule: {rule.get('name', 'unnamed')}
snowkap:{subject_slug} a snowkap:{class_slug} .
"""


def _compile_relationship_rule(rule: dict) -> str:
    """Compile a relationship rule to RDF triple."""
    _validate_required(rule, "condition.subject", "condition.predicate", "condition.object")

    condition = rule["condition"]

    subject = _encode_uri_segment(condition["subject"])
    predicate = _encode_uri_segment(condition["predicate"])
    obj = _encode_uri_segment(condition["object"])

    return f"""\
@prefix snowkap: <{SNOWKAP_NS}> .

# Rule: {rule.get('name', 'unnamed')}
snowkap:{subject} snowkap:{predicate} snowkap:{obj} .
"""


def _compile_material_issue_rule(rule: dict) -> str:
    """Compile material issue → company linkage."""
    _validate_required(rule, "condition.industry", "action.issue")

    condition = rule["condition"]
    action = rule["action"]

    industry = _encode_uri_segment(condition["industry"])
    issue = _encode_uri_segment(action["issue"])
    pillar = action.get("esg_pillar", "E")
    issue_label = action["issue"].replace("_", " ")

    return f"""\
@prefix snowkap: <{SNOWKAP_NS}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

# Rule: {rule.get('name', 'unnamed')}
snowkap:issue_{issue} a snowkap:MaterialIssue ;
    rdfs:label "{issue_label}" ;
    snowkap:esgPillar "{pillar}" .

snowkap:industry_{industry} snowkap:hasMaterialIssue snowkap:issue_{issue} .
"""


def _compile_framework_mapping_rule(rule: dict) -> str:
    """Map a material issue to a framework indicator."""
    _validate_required(rule, "condition.issue", "action.framework", "action.indicator")

    condition = rule["condition"]
    action = rule["action"]

    issue = _encode_uri_segment(condition["issue"])
    framework = _encode_uri_segment(action["framework"])
    indicator = action["indicator"]

    return f"""\
@prefix snowkap: <{SNOWKAP_NS}> .

# Rule: {rule.get('name', 'unnamed')}
snowkap:issue_{issue} snowkap:reportsUnder snowkap:{framework} .
snowkap:issue_{issue} snowkap:frameworkIndicator "{action['framework']}:{indicator}" .
"""


def _compile_geographic_risk_rule(rule: dict) -> str:
    """Assign a climate/disaster risk to a geographic region."""
    _validate_required(rule, "condition.region", "action.risk_type")

    condition = rule["condition"]
    action = rule["action"]

    region = _encode_uri_segment(condition["region"].lower())
    risk_type = action["risk_type"]
    region_label = condition["region"]

    return f"""\
@prefix snowkap: <{SNOWKAP_NS}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

# Rule: {rule.get('name', 'unnamed')}
snowkap:region_{region} a snowkap:GeographicRegion ;
    rdfs:label "{region_label}" ;
    snowkap:climateRiskZone "{risk_type}" .
"""


async def compile_and_deploy_rule(
    rule: dict,
    tenant_id: str,
) -> dict:
    """Compile a rule to OWL and deploy it to the tenant's Jena graph.

    Stage 2.6: Duplicate detection before deployment.
    Returns: {"success": bool, "owl_axiom": str, "error": str|None}
    """
    # Validate rule type
    try:
        owl = compile_rule_to_owl(rule)
    except RuleValidationError as e:
        return {"success": False, "owl_axiom": None, "error": str(e)}

    if not owl:
        return {"success": False, "owl_axiom": None, "error": f"Unknown rule type: {rule.get('rule_type')}"}

    # Stage 2.6: Duplicate detection
    fingerprint = _get_rule_fingerprint(rule)
    tenant_rules = _deployed_rules.setdefault(tenant_id, set())
    if fingerprint in tenant_rules:
        logger.info("rule_duplicate_skipped", rule_name=rule.get("name"), tenant_id=tenant_id)
        return {"success": True, "owl_axiom": owl, "error": None, "duplicate": True}

    success = await jena_client.upload_ttl(
        owl,
        graph_uri=jena_client._tenant_graph(tenant_id),
    )

    if success:
        tenant_rules.add(fingerprint)
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
