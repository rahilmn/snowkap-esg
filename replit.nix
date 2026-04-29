# Replit Nix env for Snowkap ESG Intelligence Engine.
#
# Lean dependency set — the legacy entry had postgresql + gcc + libffi for
# the now-removed backend/. Current stack is Python 3.12 + SQLite (built into
# Python) + Node 20 (frontend build). No native compilation needed for the
# pinned package set in requirements.txt; rdflib is pure Python.

{ pkgs }: {
  deps = [
    pkgs.python312
    pkgs.python312Packages.pip
    pkgs.nodejs_20
    pkgs.bash
  ];
}
