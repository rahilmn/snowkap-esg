"""The ontology resolver is immune to a data-dir volume shadow.

On Railway a volume mounted at /opt/snowkap/data overlays (blanks) the bundled
data/ontology, so the ~10.7k-triple graph used to load EMPTY and every event /
materiality / cascade lookup silently defaulted. get_ontology_dir() now falls
back to a bundled copy OUTSIDE the data dir (PROJECT_ROOT/ontology_bundle, baked
by the Dockerfile) when data/ontology is empty/shadowed.
"""
from __future__ import annotations

import engine.config as cfg


def _make(tmp_path, *, data_ttls, bundle_ttls):
    data_onto = tmp_path / "data" / "ontology"
    bundle = tmp_path / "ontology_bundle"
    data_onto.mkdir(parents=True)
    bundle.mkdir(parents=True)
    for n in data_ttls:
        (data_onto / n).write_text("# ttl", encoding="utf-8")
    for n in bundle_ttls:
        (bundle / n).write_text("# ttl", encoding="utf-8")
    return data_onto, bundle


def test_env_override_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("SNOWKAP_ONTOLOGY_DIR", str(tmp_path / "explicit"))
    assert cfg.get_ontology_dir() == tmp_path / "explicit"


def test_uses_data_ontology_when_populated(tmp_path, monkeypatch):
    monkeypatch.delenv("SNOWKAP_ONTOLOGY_DIR", raising=False)
    data_onto, _ = _make(tmp_path, data_ttls=["schema.ttl"], bundle_ttls=["schema.ttl"])
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    assert cfg.get_ontology_dir() == data_onto


def test_falls_back_to_bundle_when_data_shadowed(tmp_path, monkeypatch):
    # data/ontology EMPTY (volume-shadowed) but the bundle has the TTLs.
    monkeypatch.delenv("SNOWKAP_ONTOLOGY_DIR", raising=False)
    _, bundle = _make(tmp_path, data_ttls=[], bundle_ttls=["schema.ttl", "companies.ttl"])
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    assert cfg.get_ontology_dir() == bundle


def test_last_resort_is_data_ontology(tmp_path, monkeypatch):
    # Neither has TTLs → return the primary path (original behaviour, no crash).
    monkeypatch.delenv("SNOWKAP_ONTOLOGY_DIR", raising=False)
    _make(tmp_path, data_ttls=[], bundle_ttls=[])
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(cfg, "PROJECT_ROOT", tmp_path)
    assert cfg.get_ontology_dir() == tmp_path / "data" / "ontology"


def test_get_ontology_path_joins(tmp_path, monkeypatch):
    monkeypatch.setenv("SNOWKAP_ONTOLOGY_DIR", str(tmp_path / "o"))
    assert cfg.get_ontology_path("schema.ttl") == tmp_path / "o" / "schema.ttl"


def test_real_ontology_still_loads():
    # Regression: the live graph still loads its full triple set locally.
    from engine.ontology.graph import OntologyGraph
    g = OntologyGraph().load()
    assert len(g.graph) > 8000
