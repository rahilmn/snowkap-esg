"""Phase C — MCP server CLI entry point.

Usage::

    python scripts/run_mcp_server.py --smoke
    python scripts/run_mcp_server.py --list-tools
    python scripts/run_mcp_server.py --invoke wiki-search --payload '{"q":"water"}'

Real stdio/SSE transport is deferred (Phase 1 ships introspection +
in-process dispatch only — matches Base Version's Phase M Wave 4
Task 4.1 stance). When the SDK is wired up, this script will grow a
`--stdio` and `--sse` mode without changing the introspection paths.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from engine.mcp import build_server  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Snowkap-ESG MCP server CLI")
    parser.add_argument("--smoke", action="store_true",
                        help="Print introspection snapshot + exit")
    parser.add_argument("--list-tools", action="store_true",
                        help="Print the catalog of registered tools")
    parser.add_argument("--list-resources", action="store_true",
                        help="Print the resource URIs")
    parser.add_argument("--invoke", metavar="TOOL",
                        help="Invoke a tool by name")
    parser.add_argument("--payload", default="{}",
                        help="JSON payload for --invoke (default: {})")
    args = parser.parse_args(argv)

    handle = build_server()

    if args.smoke:
        print(json.dumps(handle.smoke(), indent=2))
        return 0

    if args.list_tools:
        out = [t.to_dict() for t in handle.list_tools()]
        print(json.dumps(out, indent=2))
        return 0

    if args.list_resources:
        out = [r.to_dict() for r in handle.list_resources()]
        print(json.dumps(out, indent=2))
        return 0

    if args.invoke:
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError as exc:
            print(f"--payload is not valid JSON: {exc}", file=sys.stderr)
            return 2
        result = handle.invoke(args.invoke, payload)
        print(json.dumps(result, indent=2, default=str))
        return 0 if result.get("ok") else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
