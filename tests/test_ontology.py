"""Ontology layer tests — causal engine, entity extraction, rule compiler.

Per MASTER_BUILD_PLAN Phase 3: Smart ESG Ontology
"""

import pytest

from backend.ontology.causal_engine import (
    CausalPath,
    HOP_DECAY,
    calculate_impact,
    classify_relationship,
    generate_explanation,
)
from backend.ontology.rule_compiler import compile_rule_to_owl


# --- Causal Engine Tests ---

class TestImpactDecay:
    """Per MASTER_BUILD_PLAN: direct=1.0, 1-hop=0.7, 2-hop=0.4, 3-hop=0.2"""

    def test_direct_impact_is_1(self):
        assert calculate_impact(0) == 1.0

    def test_one_hop_is_0_7(self):
        assert calculate_impact(1) == 0.7

    def test_two_hop_is_0_4(self):
        assert calculate_impact(2) == 0.4

    def test_three_hop_is_0_2(self):
        assert calculate_impact(3) == 0.2

    def test_four_hop_is_0_1(self):
        assert calculate_impact(4) == 0.1

    def test_beyond_max_hops(self):
        assert calculate_impact(10) == 0.05

    def test_with_base_score(self):
        assert calculate_impact(1, base_score=0.5) == 0.35

    def test_decay_map_completeness(self):
        for hop in range(5):
            assert hop in HOP_DECAY


class TestCausalPath:
    def test_explanation_generation(self):
        path = CausalPath(
            nodes=["LPG prices", "cooking fuel", "truck drivers", "fleet welfare costs"],
            hops=3,
        )
        explanation = generate_explanation(path)
        assert explanation == "LPG prices → cooking fuel → truck drivers → fleet welfare costs"

    def test_empty_path_explanation(self):
        path = CausalPath(nodes=[])
        assert generate_explanation(path) == ""

    def test_single_node_explanation(self):
        path = CausalPath(nodes=["water scarcity"])
        assert generate_explanation(path) == "water scarcity"


class TestRelationshipClassification:
    def test_direct_impact(self):
        rel = classify_relationship(["http://snowkap.com/ontology/esg#directlyImpacts"])
        assert rel == "directOperational"

    def test_supply_chain(self):
        rel = classify_relationship(["http://snowkap.com/ontology/esg#suppliesTo"])
        assert rel == "supplyChainUpstream"

    def test_geographic(self):
        rel = classify_relationship(["http://snowkap.com/ontology/esg#locatedIn"])
        assert rel == "geographicProximity"

    def test_unknown_defaults_to_direct(self):
        rel = classify_relationship(["http://example.com/unknown"])
        assert rel == "directOperational"


# --- Rule Compiler Tests ---

class TestRuleCompiler:
    def test_threshold_rule_compiles(self):
        rule = {
            "name": "High Emitter",
            "rule_type": "threshold",
            "condition": {"property": "emissions", "threshold": 1000, "operator": ">"},
            "action": {"classify_as": "HighEmitter"},
        }
        owl = compile_rule_to_owl(rule)
        assert owl is not None
        assert "HighEmitter" in owl
        assert "owl:equivalentClass" in owl
        assert "1000" in owl

    def test_classification_rule_compiles(self):
        rule = {
            "name": "Sector Classification",
            "rule_type": "classification",
            "condition": {"subject": "Mahindra Logistics"},
            "action": {"classify_as": "Transportation"},
        }
        owl = compile_rule_to_owl(rule)
        assert owl is not None
        assert "Mahindra_Logistics" in owl
        assert "Transportation" in owl

    def test_relationship_rule_compiles(self):
        rule = {
            "name": "Supplier Link",
            "rule_type": "relationship",
            "condition": {
                "subject": "SteelCo",
                "predicate": "suppliesTo",
                "object": "MahindraLogistics",
            },
        }
        owl = compile_rule_to_owl(rule)
        assert owl is not None
        assert "suppliesTo" in owl

    def test_material_issue_rule_compiles(self):
        rule = {
            "name": "Transport Emissions",
            "rule_type": "material_issue",
            "condition": {"industry": "Transportation"},
            "action": {"issue": "fuel_management", "esg_pillar": "E"},
        }
        owl = compile_rule_to_owl(rule)
        assert owl is not None
        assert "fuel_management" in owl
        assert 'esgPillar' in owl

    def test_framework_mapping_compiles(self):
        rule = {
            "name": "Emissions to BRSR",
            "rule_type": "framework_mapping",
            "condition": {"issue": "emissions"},
            "action": {"framework": "BRSR", "indicator": "P6"},
        }
        owl = compile_rule_to_owl(rule)
        assert owl is not None
        assert "BRSR" in owl

    def test_geographic_risk_compiles(self):
        rule = {
            "name": "Mumbai Flood Risk",
            "rule_type": "geographic_risk",
            "condition": {"region": "Mumbai"},
            "action": {"risk_type": "coastal_flood"},
        }
        owl = compile_rule_to_owl(rule)
        assert owl is not None
        assert "coastal_flood" in owl
        assert "mumbai" in owl

    def test_unknown_rule_type_returns_none(self):
        rule = {"rule_type": "nonexistent", "condition": {}, "action": {}}
        assert compile_rule_to_owl(rule) is None


# --- Geographic Intelligence Tests ---

class TestGeographicIntelligence:
    def test_climate_risk_zones_exist(self):
        from backend.ontology.geographic_intelligence import CLIMATE_RISK_ZONES
        assert "coastal_flood" in CLIMATE_RISK_ZONES
        assert "drought_prone" in CLIMATE_RISK_ZONES
        assert "mumbai" in CLIMATE_RISK_ZONES["coastal_flood"]

    def test_haversine_distance(self):
        from backend.ontology.geographic_intelligence import haversine_distance
        # Mumbai to Delhi ~1,150 km
        dist = haversine_distance(19.076, 72.877, 28.613, 77.209)
        assert 1100 < dist < 1200

    def test_haversine_same_point(self):
        from backend.ontology.geographic_intelligence import haversine_distance
        assert haversine_distance(19.076, 72.877, 19.076, 72.877) == 0.0


# --- Supply Chain Tests ---

class TestSupplyChainGraph:
    def test_commodity_chains_exist(self):
        from backend.ontology.supply_chain_graph import COMMODITY_CHAINS
        assert "steel" in COMMODITY_CHAINS
        assert "iron_ore" in COMMODITY_CHAINS["steel"]

    def test_scope3_categories_complete(self):
        from backend.ontology.supply_chain_graph import SCOPE3_UPSTREAM, SCOPE3_DOWNSTREAM
        assert len(SCOPE3_UPSTREAM) == 8  # Categories 1-8
        assert len(SCOPE3_DOWNSTREAM) == 7  # Categories 9-15
