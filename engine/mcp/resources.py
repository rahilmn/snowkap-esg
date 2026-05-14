"""Phase C — MCP resource registry.

Surfaces read-only assets behind URIs so an MCP client can pull
authoritative state without going through a tool call:

  snowkap://wiki/{tier}/{rest}             → wiki page (markdown)
  snowkap://ontology/{file}.ttl            → loaded TTL
  snowkap://audit/{name}.jsonl             → audit ledger (last 5KB)
  snowkap://autoresearcher/{tier}/ledger   → experiment ledger (last 5KB)

Caps every text return at ~32KB so a misbehaving client can't pull
the whole graph into a chat context window.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from engine.mcp.server import ResourceDescriptor


_MAX_RESOURCE_BYTES = 32_768
_AUDIT_TAIL_BYTES = 8_192


def _read_tail(path: Path, max_bytes: int) -> str:
    """Read at most `max_bytes` from the end of a file."""
    if not path.exists():
        return ""
    size = path.stat().st_size
    offset = max(0, size - max_bytes)
    with path.open("rb") as f:
        f.seek(offset)
        chunk = f.read(max_bytes)
    return chunk.decode("utf-8", errors="replace")


def _read_capped(path: Path, max_bytes: int) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as f:
        chunk = f.read(max_bytes)
    return chunk.decode("utf-8", errors="replace")


def list_default_resources(base_data: Path) -> Iterable[ResourceDescriptor]:
    """Enumerate resource URIs available on the local disk.

    Cheap — does a few `glob()` calls and one `iterdir()`. Designed to
    run on every `/api/mcp/resources` request.
    """
    out: list[ResourceDescriptor] = []

    # Wiki pages — index pages only by default (full tree is too large)
    wiki_root = base_data.parent / "wiki" if (base_data / "..").exists() else base_data / "wiki"
    if not wiki_root.exists():
        wiki_root = Path(base_data).parent.parent / "wiki"
    if wiki_root.exists():
        for tier_dir in ("system", "tenants", "users"):
            idx = wiki_root / tier_dir / "index.md"
            if idx.exists():
                out.append(
                    ResourceDescriptor(
                        uri=f"snowkap://wiki/{tier_dir}/index",
                        name=f"Wiki tier {tier_dir} index",
                        description=f"Tier-{tier_dir} wiki index page (markdown).",
                        mime_type="text/markdown",
                    )
                )

    # Ontology TTL files
    ontology_dir = base_data / "ontology"
    if ontology_dir.exists():
        for ttl in sorted(ontology_dir.glob("*.ttl")):
            out.append(
                ResourceDescriptor(
                    uri=f"snowkap://ontology/{ttl.name}",
                    name=f"Ontology · {ttl.stem}",
                    description="Loaded TTL graph (capped at 32 KB).",
                    mime_type="text/turtle",
                )
            )

    # Audit logs (tail-only)
    audit_dir = base_data / "audit"
    if audit_dir.exists():
        for jl in sorted(audit_dir.glob("*.jsonl")):
            out.append(
                ResourceDescriptor(
                    uri=f"snowkap://audit/{jl.name}",
                    name=f"Audit · {jl.stem}",
                    description="Append-only audit log (last 8 KB tail).",
                    mime_type="application/x-ndjson",
                )
            )

    # Autoresearcher ledgers
    ar_dir = base_data / "autoresearcher"
    if ar_dir.exists():
        for tier_dir in sorted(p for p in ar_dir.iterdir() if p.is_dir()):
            ledger = tier_dir / "experiments.jsonl"
            if ledger.exists():
                out.append(
                    ResourceDescriptor(
                        uri=f"snowkap://autoresearcher/{tier_dir.name}/ledger",
                        name=f"Autoresearcher · {tier_dir.name} ledger",
                        description="Recent experiment ledger entries (last 8 KB tail).",
                        mime_type="application/x-ndjson",
                    )
                )

    return out


def read_default_resource(uri: str, base_data: Path) -> tuple[str, str] | None:
    """Return `(mime_type, text)` for a known URI, or None.

    Centralised dispatcher — any new resource family adds a branch here.
    """
    if not uri.startswith("snowkap://"):
        return None
    body = uri[len("snowkap://"):]

    # Wiki — snowkap://wiki/<tier>/<rest>
    if body.startswith("wiki/"):
        rest = body[len("wiki/"):]
        # Resolve wiki root (sibling to data/)
        candidate_roots = [
            base_data.parent / "wiki",
            base_data / "wiki",
        ]
        for root in candidate_roots:
            if not root.exists():
                continue
            # Special-case the tier index alias
            if rest in ("system/index", "tenants/index", "users/index"):
                target = root / rest.split("/")[0] / "index.md"
            else:
                target = root / (rest + ".md") if not rest.endswith(".md") else root / rest
            if target.exists() and target.is_file():
                return ("text/markdown", _read_capped(target, _MAX_RESOURCE_BYTES))
        return None

    # Ontology — snowkap://ontology/<file>.ttl
    if body.startswith("ontology/"):
        name = body[len("ontology/"):]
        ttl = base_data / "ontology" / name
        if ttl.exists() and ttl.suffix == ".ttl":
            return ("text/turtle", _read_capped(ttl, _MAX_RESOURCE_BYTES))
        return None

    # Audit — snowkap://audit/<name>.jsonl  (tail-only)
    if body.startswith("audit/"):
        name = body[len("audit/"):]
        jl = base_data / "audit" / name
        if jl.exists() and jl.suffix == ".jsonl":
            return ("application/x-ndjson", _read_tail(jl, _AUDIT_TAIL_BYTES))
        return None

    # Autoresearcher — snowkap://autoresearcher/<tier>/ledger
    if body.startswith("autoresearcher/"):
        parts = body[len("autoresearcher/"):].split("/")
        if len(parts) >= 2 and parts[1] == "ledger":
            tier = parts[0]
            ledger = base_data / "autoresearcher" / tier / "experiments.jsonl"
            if ledger.exists():
                return ("application/x-ndjson", _read_tail(ledger, _AUDIT_TAIL_BYTES))
        return None

    return None
