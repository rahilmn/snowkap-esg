# SNOWKAP ESG — Demo Access Credentials

## Public URL
**URL**: _(will be updated once ngrok is running)_

---

## Demo Accounts

All accounts use **passwordless magic link** authentication. Enter the email below, and the system will auto-login (demo mode — no email verification required for beta accounts).

### Banking Sector

| Company | Email | Designation | Role |
|---------|-------|-------------|------|
| **ICICI Bank Ltd** | `beta@icicibank.com` | Head of Sustainability | CSO |
| **YES Bank Ltd** | `beta@yesbank.in` | Head of Sustainability | CSO |
| **IDFC First Bank Ltd** | `beta@idfcfirstbank.com` | Head of Sustainability | CSO |

### Energy & Industrial Sector

| Company | Email | Designation | Role |
|---------|-------|-------------|------|
| **Adani Power Ltd** | `beta@adanipower.com` | Head of Sustainability | CSO |
| **JSW Energy Ltd** | `beta@jsw.in` | Head of Sustainability | CSO |
| **Waaree Energies Ltd** | `beta@waaree.com` | Head of Sustainability | CSO |

### Asset Management

| Company | Email | Designation | Role |
|---------|-------|-------------|------|
| **Singularity AMC Pvt Ltd** | `beta@singularityamc.com` | Head of Sustainability | CSO |

---

## How to Login

1. Open the demo URL
2. Enter one of the email addresses above
3. The system auto-detects the company from the email domain
4. Click "Continue" — you'll be logged in directly (no magic link email in demo mode)
5. Each company sees ONLY its own ESG news feed (multi-tenant isolation)

---

## What to Explore

### View Insights (tap any article)
- **ESG Theme Bar** — primary + secondary theme classification
- **Narrative Intelligence** — AI-extracted core claim, causation chain, stakeholder framing
- **Risk Matrix** — 10-category Probability × Exposure heatmap (HOME-tier articles)
- **Risk Spotlight** — Top 3 risks (FEED-tier articles)
- **Framework Alignment** — 13 ESG frameworks matched with section-level citations
- **AI Recommendations** — 3-agent RE³ validated recommendations
- **Causal Chain** — how the news connects to the tracked company

### Ask AI Agent (tap "Ask AI" on any article)
- Context-aware: knows what you already saw in View Insights
- Role-specific: adapts output to your designation (CSO sees framework gaps, CEO sees competitive positioning)
- Quick action buttons: "What should I prioritize?", "Framework compliance gaps", etc.
- 9 specialist agents: supply chain, compliance, analytics, executive, trend, stakeholder, opportunity, content, legal

### Saved Stories
- Swipe up on cards to save
- Multi-select delete + Clear All

---

## Technical Details

- **Platform**: React 19 + FastAPI + PostgreSQL + Apache Jena + Redis
- **AI**: GPT-4o (analysis) + GPT-4o-mini (lightweight tasks)
- **Pipeline**: 12-stage intelligence pipeline with 5D relevance scoring
- **Multi-tenant**: Complete data isolation per company
