"""Background job infrastructure that runs OUTSIDE the API event loop.

The API process used to run heavy onboarding work via FastAPI's
``BackgroundTasks``. That works for sub-second jobs but onboarding does
yfinance lookups, NewsAPI calls, and 12-stage LLM pipelines — any one
of which can stall the event loop and freeze login / feed latency for
every other tenant on the worker.

This package gives us a tiny SQLite-backed queue (``onboard_queue``) and
a standalone worker process (``scripts.onboarding_worker``) that drain
those jobs in their own Replit workflow. The API only enqueues; it never
runs onboarding inline.
"""
