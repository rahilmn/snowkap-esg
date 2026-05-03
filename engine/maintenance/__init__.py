"""Maintenance helpers — retention, archival, wipe.

Pulled out of `engine/index/` so they can be called from cron / worker
scripts without dragging in the full FastAPI app context.
"""
