# SNOWKAP ESG Intelligence System

**Version**: 2.1 | **Last Updated**: 2026-03-31

---

## How Intelligence Flows

```
News Sources (Google RSS + NewsAPI)
        |
        v
  [1] Content Extraction (trafilatura + language detection)
        |
        v
  [2] NLP Pipeline (sentiment, tone, narrative arc, source credibility)
        |
        v
  [3] Entity Extraction & Resolution (Claude NER + Jena graph matching)
        |
        v
  [4] ESG Theme Classification (21-theme taxonomy, sector-aware)
        |
        v
  [5] 5D Relevance Scoring (0-10, content quality gate)
        |
       / \
      /   \
  REJECTED   QUALIFIED (score >= 4)
  (skip)          |
                  v
  [6] Causal Chain Engine (BFS, max 4 hops, 17 relationship types)
        |
        v
  [7] Priority Scoring (7 components, 0-100)
        |
       / \
      /   \
  FEED-tier    HOME-tier (relevance >= 7)
     |              |
     v              v
  [8a] Risk       [8b] Full Analysis:
  Spotlight       - 10-Category Risk Matrix
  (top 3)         - Deep Insight Brief
                  - Framework RAG (13 frameworks)
                  - REREACT Recommendations (3-agent)
                  - Executive Insight
        |
        v
  [9] Event Deduplication (title similarity + 72h window)
        |
        v
  [10] Role-Based Feed Curation (6 roles, personalized scoring)
        |
        v
  DELIVERED TO USER (Home / Feed / Agent Chat)
```

---

## Module 1: NLP Narrative & Tone Extraction

The first step in every article's journey. Extracts structured signals before any scoring begins.

**5 extractions in a single LLM call (gpt-4o):**

| Step | Output | Scale |
|------|--------|-------|
| Sentiment | Score + label | -2 (crisis) to +2 (breakthrough) |
| Tone | Primary + secondary | 10 controlled tones (alarmist, cautionary, analytical, neutral, optimistic, promotional, adversarial, conciliatory, urgent, speculative) |
| Narrative Arc | Core claim, causation chain, stakeholder framing, temporal framing | Structured text |
| Source Credibility | Tier 1-4 | Institutional > Established Media > Trade > Unverified |
| ESG Signals | Entities, quantities, regulations, supply chain refs | Structured lists |

**Language Handling**: Non-English content (Marathi, Hindi, etc.) is auto-detected and translated to English via GPT-4o before analysis. Original text preserved in metadata.

**Source Tier Examples**:
- **Tier 1**: SEBI, RBI, SEC, EPA, World Bank, IPCC
- **Tier 2**: Bloomberg, Reuters, FT, Moneycontrol, Mint, Business Standard, Economic Times
- **Tier 3**: Trade publications, analyst notes, Whalesbook
- **Tier 4**: Social media, unvalidated press releases

---

## Module 2: Entity Extraction & Knowledge Graph Resolution

Identifies real-world entities in the article and links them to the tenant's knowledge graph in Apache Jena.

**Entity Types**: Companies, locations, commodities, regulations, events, persons, industries, frameworks

**Resolution Process**:
1. LLM extracts entities with confidence scores (0-1)
2. Each entity queried against tenant's Jena named graph (`urn:snowkap:tenant:{id}`)
3. Ranking: exact match > substring match > fuzzy match + edge count
4. Resolved entities get URIs for causal chain traversal

**Framework Alias Normalization**: 30+ common names mapped to canonical codes (e.g., "Task Force on Climate" -> TCFD, "Global Reporting Initiative" -> GRI)

---

## Module 3: ESG Theme Classification

Classifies every article into a 21-theme taxonomy spanning Environmental, Social, and Governance pillars.

### The 21-Theme Taxonomy

**Environmental (8 themes)**:
Energy, Emissions, Water, Biodiversity, Waste & Circularity, Climate Adaptation, Land Use, Air Quality

**Social (7 themes)**:
Human Capital, Health & Safety, Community Impact, Supply Chain Labor, Product Safety, Access & Affordability, DEI

**Governance (6 themes)**:
Board & Leadership, Ethics & Compliance, Risk Management, Transparency & Disclosure, Shareholder Rights, Tax Transparency

Each theme has 3-6 sub-metrics (e.g., Emissions -> scope_1_direct, scope_2_indirect, scope_3_value_chain, ghg_reduction_targets).

**Sector-Aware Classification**: For financial sector companies (banks, NBFCs, AMCs), the classifier distinguishes between the company's own governance vs. the environmental/social subject matter of the article. A green bond issuance is tagged as Energy (Environmental), not Transparency & Disclosure.

---

## Module 4: Framework Alignment (RAG)

Matches article content against 13 ESG reporting frameworks to identify compliance implications.

### Frameworks Covered

| Framework | Focus |
|-----------|-------|
| **TCFD** | Climate-related financial disclosures |
| **BRSR** | India's business responsibility reporting (SEBI) |
| **GRI** | Global sustainability reporting (400+ standards) |
| **CSRD/ESRS** | EU corporate sustainability reporting |
| **IFRS S1/S2** | International sustainability standards |
| **SASB** | Industry-specific sustainability accounting |
| **CDP** | Carbon/water/forests disclosure |
| **EU Taxonomy** | Sustainable activities classification |
| **SFDR** | Sustainable finance disclosure (EU) |
| **GHG Protocol** | Greenhouse gas accounting |
| **SBTi** | Science-based emissions targets |
| **TNFD** | Nature-related financial disclosures |
| **SEC Climate** | US climate disclosure rules |

**Matching Strategy**: Rule-based keyword triggers (primary) + LLM fallback. Each framework has section-level provisions with specific trigger keywords.

---

## Module 5: 6-Dimension Relevance Scoring (v2.1 — Double Materiality)

The content quality gate. Scores every article on 6 dimensions (0-2 each, max 12). v2.1 adds Impact Materiality for CSRD/ESRS alignment.

| Dimension | 0 | 1 | 2 |
|-----------|---|---|---|
| **ESG Correlation** | No ESG nexus | Indirect connection | Direct, material linkage |
| **Financial Impact** | <1% effect on revenue, expenses, or valuation | 1-5% effect (includes compliance costs, fines, capex) | >=5% revenue, expense, or valuation impact |
| **Compliance Risk** | No regulatory nexus | Affects compliance posture | Triggers new regulation/penalty/deadline |
| **Supply Chain** | No SC relevance | Tier 2/3 or geographic | Tier 1 disruption or single-source |
| **People Impact** | No people dimension | Indirect or <100 people | Direct impact on >100 people |
| **Impact Materiality** | No outward ESG impact | Indirect impact on environment/society | Direct, measurable impact (emissions, community harm, resource depletion) |

**Double Materiality (CSRD/ESRS)**: The first 5 dimensions capture FINANCIAL MATERIALITY (how ESG issues affect the company). The 6th dimension captures IMPACT MATERIALITY (how the company affects ESG issues). Together they satisfy the EU double materiality requirement.

**Industry Materiality Weighting (SASB-Aligned)**: After base scoring, a SASB-aligned multiplier adjusts scores by industry relevance. A water scarcity article scores higher for manufacturing (materiality weight 1.0) than for software (weight 0.3). This prevents generic scoring that treats all industries equally.

**Feed Qualification**:
- **HOME** (score >= 8, ESG correlation > 0): Full analysis pipeline
- **SECONDARY** (score 5-7): Lightweight risk spotlight only
- **REJECTED** (score < 5): Excluded from all feeds

**Financial Services Adaptation**: For banks/NBFCs, "supply chain" includes lending portfolio exposure, counterparty ESG risk, financed emissions (Scope 3 Category 15), and borrower ESG compliance. "Financial impact" includes compliance costs, regulatory fines, carbon tax exposure, and insurance premium changes.

---

## Module 6: 10-Category Risk Matrix

A comprehensive risk assessment scoring 10 categories on a Probability x Exposure matrix.

### The 10 Risk Categories

| Category | What It Measures |
|----------|-----------------|
| **Physical Risk** | Climate hazards: floods, wildfire, heat stress, water scarcity |
| **Supply Chain Risk** | Supplier disruption, geographic concentration, logistics fragility |
| **Reputational Risk** | Brand damage, greenwashing allegations, social media backlash |
| **Regulatory Risk** | New ESG regulations, disclosure mandates, carbon pricing |
| **Litigation Risk** | ESG lawsuits, enforcement actions, class-action suits |
| **Transition Risk** | Stranded assets, demand shifts, technology obsolescence |
| **Human Capital Risk** | Talent retention, labor disputes, safety incidents |
| **Technological Risk** | Cybersecurity, AI governance, digital transformation failure |
| **Manpower Risk** | Workforce availability, productivity, demographic shifts |
| **Market & Uncertainty** | Volatility, geopolitical disruption, ESG fund flow shifts |

### Scoring

- **Probability** (1-5): Rare -> Unlikely -> Possible -> Likely -> Almost Certain
- **Exposure** (1-5): Negligible -> Minor -> Moderate -> Severe -> Critical
- **Risk Score** = Probability x Exposure (1-25 per category)
- **Aggregate** = Sum of all 10 / 250 (normalized 0-1)

### Classification Thresholds

| Score | Level | Meaning |
|-------|-------|---------|
| 20-25 | CRITICAL | Immediate board-level attention required |
| 12-19 | HIGH | Senior management action needed |
| 6-11 | MODERATE | Monitor and prepare contingency |
| 1-5 | LOW | Awareness only |

---

## Module 7: Priority Scoring Engine

Combines 8 weighted components into a single 0-100 priority score that determines article urgency.

### The 8 Components (v2.1)

| Component | Weight | Logic |
|-----------|--------|-------|
| **Sentiment Severity** | 0-25 | Negative sentiment x 25 (downside bias) |
| **Positive Opportunity** | 0-10 | Positive sentiment x 10 (upside signal) |
| **Urgency** | 0-25 | critical=25, high=18, medium=10, low=3 |
| **Structural Impact** | 0-20 | From causal chain impact score |
| **Financial Signal** | 0/15 | Binary: quantitative amount detected (revenue OR expense) = +15 |
| **Irreversibility** | 0-10 | irreversible=10, difficult=7, moderate=4, easy=1 |
| **Framework Breadth** | 0-5 | min(framework_count, 5) -- multi-framework = systemic |
| **Regulatory Deadline** | 0-20 | Proximity to known regulatory deadline: <=90 days=+20, <=180 days=+12, <=365 days=+5 |

**Regulatory Calendar**: The system maintains a jurisdiction-aware database of ESG regulatory deadlines (BRSR filing dates, CSRD phase-in, CDP submission windows, RBI disclosure dates). When an article references a regulation with an upcoming deadline, urgency is automatically boosted based on proximity.

**Formula**: `priority = min(sum(all_components) x role_multiplier, 100)`

### Priority Levels

| Score | Level | Action Required |
|-------|-------|----------------|
| >= 85 | CRITICAL | Immediate escalation |
| >= 70 | HIGH | Same-day review |
| >= 40 | MEDIUM | Weekly review |
| < 40 | LOW | Monitor |

### Role Multipliers

| Role | Multiplier | Rationale |
|------|-----------|-----------|
| Board Member | 1.3x | Highest urgency perception |
| CEO | 1.2x | Strategic priority |
| CFO | 1.1x | Financial materiality focus |
| CSO / Compliance | 1.0x | Standard |
| Member | 0.8x | Reduced noise |

---

## Module 8: Deep Insight Generation

The crown jewel. For HOME-tier articles only, generates a comprehensive intelligence brief.

### Specialist Agent Routing

Articles are routed to domain-specialist agents based on content type:

| Content Type | Specialist | Focus |
|---|---|---|
| Regulatory | Compliance | Filing deadlines, penalties, obligations |
| Financial | Executive | Capital markets, valuation, positioning |
| Operational | Supply Chain | Tier 1/2/3 impact, facility disruption |
| Reputational | Stakeholder | Brand, social license, community |
| Technical | Analytics | Data, metrics, benchmarks |
| Narrative | Content | Public communication, disclosure strategy |

### Deep Insight Structure

```
Headline         "BRSR compliance gap widens for Indian financial sector"
Impact Score     7.5/10 (Material, requires board attention)
Core Mechanism   2-3 sentence causal analysis (not a summary)
Translation      One-line plain-language summary

Impact Analysis (6 dimensions):
  - ESG Positioning: relative attractiveness shift
  - Capital Allocation: institutional flows, cost of capital
  - Valuation/Cashflow: P/E, margins, demand effects
  - Compliance/Regulatory: framework obligations triggered
  - Supply Chain: Tier 1/2/3 transmission pathway
  - People/Demand: employee, customer, community effects

Time Horizons:
  - Short-term (0-6 months)
  - Medium-term (6-24 months)
  - Long-term (2-5+ years)

Net Impact Summary: structural significance in 3-4 sentences
```

### Impact Score Calibration

| Score | Meaning | Financial Materiality |
|-------|---------|----------------------|
| 9-10 | Existential threat or transformation | >20% revenue/valuation impact |
| 7-8 | Material, requires board/CXO attention | 5-20% impact |
| 5-6 | Notable, departmental action needed | 1-5% impact |
| 3-4 | Awareness item, monitor quarterly | <1% impact |
| 1-2 | Noise, no action required | Negligible |

---

## REREACT: 3-Agent Recommendation Engine

Every HOME-tier article generates 3-5 actionable recommendations through a rigorous 3-agent validation pipeline.

### Agent 1: Generator

Produces initial recommendations using the specialist agent personality. Each recommendation includes:
- **Title**: Action-oriented (max 10 words)
- **Responsible Party**: Named role (e.g., "Chief Risk Officer", "Audit Committee")
- **Description**: Specific steps (max 40 words)
- **Framework Section**: Exact code (e.g., BRSR:P1:Q5, GRI:205-2)
- **Deadline**: Absolute calendar date (never "Q2" or "short_term")
- **Budget**: Estimated range or "Internal resources only"
- **Success Criterion**: Measurable outcome

**Language Rules**:
- Banned verbs: enhance, strengthen, improve, develop, bolster, foster
- Required verbs: commission, file, appoint, allocate, disclose, audit, terminate

### Agent 2: Analyzer

Stress-tests every recommendation:
- Verifies causal chain logic
- Checks for missing dimensions and second-order effects
- Validates proportionality of response
- Enriches with precedents and benchmarks
- Challenges framing assumptions

### Agent 3: Validator

Independent quality gate with binary PASS/REJECT:
- Is the recommendation grounded in article data?
- Does it name a specific responsible party?
- Does it include a framework section code?
- Does it have an absolute calendar deadline?
- Does it have a measurable success criterion?
- Are action verbs specific (not vague)?

Rejected recommendations are logged with reasons. Validated ones receive a confidence score (HIGH/MEDIUM/LOW).

---

## Causal Chain Engine

Maps how a news event propagates impact to the tracked company through the knowledge graph.

### 17 Relationship Types

| Type | Hops | Example |
|------|------|---------|
| directOperational | 0 | Article mentions the company directly |
| supplyChainUpstream | 1 | Supplier disruption affects company |
| supplyChainDownstream | 1 | Customer demand shift |
| workforceIndirect | 2 | Labor market change via contractor |
| regulatoryContagion | 1 | Regulation on peer affects sector |
| geographicProximity | 0 | Event near company facility |
| industrySpillover | 1 | Competitor event signals sector trend |
| commodityChain | 3 | Commodity price shift through chain |
| climateRiskExposure | 0 | Climate event in facility risk zone |
| waterSharedBasin | 1 | Shared water resource stress |
| competitiveIntelligence | 1 | Named competitor action |

### Impact Decay

Impact attenuates with each hop in the causal chain:
```
Hop 0: 1.00 (direct)
Hop 1: 0.70
Hop 2: 0.40
Hop 3: 0.20
Hop 4: 0.10
```

Geographic proximity and climate risk matches receive bonus boosts (+0.1 to +0.3).

---

## News Velocity & Reputational Amplification (v2.1)

When multiple sources report the same event, it signals media amplification — a reputational risk multiplier.

**How it works**: The system counts distinct sources reporting the same event within 48 hours using title similarity clustering. This velocity score feeds into the **Reputational Risk** category of the risk matrix only — NOT overall priority.

| Sources Reporting | Reputational Risk Boost | Rationale |
|---|---|---|
| 1-4 | No boost | Normal coverage |
| 5-9 | Probability +1 | Media attention building |
| 10+ | Probability +2 | Viral/crisis-level coverage |

**Why not a blanket priority booster**: An obscure RBI circular from a single Tier 1 source can be existentially important for every Indian bank. A celebrity greenwashing scandal covered by 50 outlets may be materially irrelevant. ESG importance comes from materiality, not media volume.

---

## Supply Chain Location Risk Monitoring (v2.1)

Beyond company-name-based news, the system monitors SPECIFIC RISK EVENTS in supply chain geographies.

**What's monitored**: Facility locations (city/country) and Tier 1 supplier names from the knowledge graph.

**Query strategy**: Not generic ESG news from those locations. Instead, targeted risk-event queries:
- Natural disasters: flood, drought, cyclone, wildfire in facility locations
- Labor events: strikes, protests, labor rights violations near operations
- Regulatory changes: new environmental or labor regulations in operating jurisdictions
- Pollution/contamination events near company facilities

**Why this matters**: BRSR Principle 5 and ESRS S2 require companies to monitor and report on supply chain ESG risks. This makes compliance-driven monitoring automatic.

Articles sourced from supply chain monitoring are tagged as `source_type: "supply_chain_monitor"` so feeds can distinguish direct-impact vs supply-chain news.

---

## Industry Materiality Weights (v2.1 — SASB-Aligned)

Not all ESG themes matter equally to all industries. The system applies SASB-aligned materiality weights that adjust relevance scores by industry.

**Examples**:
| Theme | Banking (weight) | Manufacturing (weight) | Consumer Goods (weight) |
|---|---|---|---|
| Emissions | 0.3 (low — indirect) | 1.0 (high — Scope 1/2) | 0.7 (moderate — Scope 3) |
| Ethics & Compliance | 1.0 (high — fiduciary) | 0.6 (moderate) | 0.6 (moderate) |
| Supply Chain Labor | 0.4 (low) | 0.8 (high — factories) | 1.0 (high — sourcing) |
| Water | 0.2 (low) | 1.0 (high — operations) | 0.8 (high — agriculture) |
| Risk Management | 1.0 (high — core) | 0.7 (moderate) | 0.5 (moderate) |

**Effect**: A water scarcity article with base relevance score 6 would remain 6 for a manufacturer (weight 1.0) but drop to 3 for a bank (weight 0.2 x 0.5 multiplier) — correctly reflecting that water is not material for banks but critical for manufacturers.

---

## Event Deduplication

When multiple articles cover the same event (e.g., 3 articles about the same fraud), the system:

1. **Detects clusters**: Title word similarity >= 35% within a 72-hour window
2. **Consolidates scores**: Uses highest priority score across the cluster
3. **Merges risk matrices**: Takes maximum score per risk category
4. **Links coverage**: Each article shows related articles in the cluster

This prevents the same event from inflating alert counts while preserving all coverage for reference.

---

## Role-Based Feed Personalization

The feed is personalized for 6 organizational roles, each with distinct content preferences.

| Role | Primary Focus | Alert Threshold | Key Frameworks |
|------|--------------|----------------|----------------|
| **Board Member** | Strategic risk, fiduciary duty | 85 (CRITICAL only) | TCFD, BRSR, CSRD |
| **CEO** | Competitive positioning, narrative | 80 | TCFD, IFRS S1/S2 |
| **CFO** | Valuation, cost of capital | 75 | TCFD, IFRS, SASB |
| **CSO** | ESG scoring, framework gaps | 60 | BRSR, GRI, ESRS, CDP |
| **Compliance** | Regulatory triggers, litigation | 55 | BRSR, CSRD, ESRS |
| **Supply Chain** | Supplier risk, disruption | 50 | GRI, BRSR |

**Personalization Score** (0-100) = content_type_match (40) + framework_overlap (30) + pillar_alignment (20) + base (10)

**User Preference Boosts**: +15 for preferred frameworks, +10 for preferred pillars, -20 for dismissed topics.

---

## AI Agent Chat System

An AI-powered conversational interface with 9 specialist agent personalities, built on LangGraph.

### How The Agent Works

1. **User asks a question** (free text or about a specific article)
2. **Intent classifier** routes to the best specialist agent
3. **Agent has access to tools**: database queries, SPARQL, predictions, article context
4. **Response is role-aware**: framed for the user's designation
5. **Memory**: Conversation history maintained via session state

### 9 Specialist Agents

| Agent | Expertise | When Routed |
|-------|-----------|-------------|
| **Executive** | Strategic positioning, market narrative | Default, financial questions |
| **Compliance** | Regulations, obligations, deadlines | Regulatory articles |
| **Supply Chain** | Operations, Tier 1/2/3, disruption | Operational articles |
| **Analytics** | Data, metrics, benchmarks | Technical/data articles |
| **Stakeholder** | Community, reputation, social license | Reputational events |
| **Opportunity** | Green bonds, sustainable capital, upside | Positive ESG events |
| **Trend** | Emerging patterns, long-term trajectories | Forward-looking analysis |
| **Content** | Public narrative, disclosure strategy | Communication questions |
| **Legal** | Litigation, enforcement, contracts | Legal risk questions |

### Agent Tools

- **Database Query**: Search articles, scores, companies, causal chains
- **SPARQL Query**: Query the knowledge graph for entity relationships
- **Prediction Trigger**: Launch MiroFish simulation on an article
- **Article Context**: Access full enrichment data for any article

---

## MiroFish Prediction Engine

A separate multi-agent simulation system for high-impact articles.

**Trigger Conditions**: Only articles with priority score > 70 AND financial exposure > Rs 10L

**How It Works**:
1. 20-50 AI agents simulate stakeholder responses
2. 10-40 rounds of interaction
3. Agents represent: investors, regulators, media, employees, competitors
4. Simulation produces probability distributions for outcomes
5. Results stored in prediction_reports table + Jena triples

**Architecture**: Runs as separate Docker service on port 5001 (AGPL-3.0 process isolation). Uses OASIS simulation framework + Zep Cloud for agent memory + GraphRAG for entity context.

---

## LLM Models Used

| Stage | Model | Purpose | Tokens |
|-------|-------|---------|--------|
| NLP Pipeline | gpt-4o | Sentiment, tone, narrative, entities | 800 |
| Translation | gpt-4o | Non-English content translation | 2000 |
| Entity Extraction | gpt-4o-mini | NER + classification | 1500 |
| ESG Theme Tagging | gpt-4o | 21-theme classification | 500 |
| Risk Matrix | gpt-4o | 10-category P x E scoring | 2000 |
| Deep Insight | gpt-4o | Full intelligence brief | 1500 |
| Risk Spotlight | gpt-4o-mini | Quick top-3 risk scan | 300 |
| Specialist Insight | gpt-4o-mini | Role-specific SME analysis | 300 |
| REREACT (x3) | gpt-4o | Generate + Analyze + Validate | 800 x 3 |
| Framework RAG | gpt-4o | Framework alignment (fallback) | varies |

---

## Data Pipeline Summary

### Per Article (Full Pipeline)

| Stage | Output | Storage |
|-------|--------|---------|
| Content Extraction | Article text + metadata | `articles.content` |
| NLP Pipeline | Sentiment, tone, narrative, source tier | `articles.nlp_extraction` |
| Entity Extraction | Entities + Jena URIs | `articles.entities` |
| ESG Themes | Primary + secondary themes | `articles.esg_themes` |
| Framework RAG | Matched frameworks + sections | `articles.framework_matches` |
| Relevance Scoring | 5D score + tier | `articles.relevance_score`, `relevance_breakdown` |
| Causal Chains | Impact paths | `causal_chains` table |
| Risk Matrix | 10-category scores | `articles.risk_matrix` |
| Priority Scoring | 0-100 score + level | `articles.priority_score`, `priority_level` |
| Deep Insight | Full brief | `articles.deep_insight` |
| REREACT | 3-5 recommendations | `articles.rereact_recommendations` |
| Executive Insight | C-suite summary | `articles.executive_insight` |
| Article Scores | Per-company relevance | `article_scores` table |
| Event Dedup | Cluster metadata | `articles.scoring_metadata` |

### Tenant Coverage (Current — as of 2026-03-31)

| Metric | Count |
|--------|-------|
| Total tenants | 8 |
| Total articles | 128 |
| NLP extracted | 128 (100%) |
| ESG themes tagged | 128 (100%) |
| Relevance scored | 128 (100%) |
| Risk matrix | 89 (70%) |
| Deep insight | 52 (41%) |
| REREACT recommendations | 35 (27%) |
| Frameworks matched | 42 (33%) |
| Users per tenant | 6 (Board, CEO, CFO, Compliance, Analyst, CSO) |
| Total users | 48 |

---

## Architecture

```
React 19 + Vite          FastAPI (port 8000)         Apache Jena (port 3030)
  (client/)                (backend/)                  Knowledge Graph
     |                        |                            |
     +-- Zustand stores       +-- 13 API routers           +-- Tenant named graphs
     +-- TanStack Query       +-- SQLAlchemy 2.0           +-- SPARQL endpoint
     +-- Radix UI             +-- Celery workers           +-- OWL2 ontology
     +-- Socket.IO            +-- LangGraph agents         |
                              |                        MiroFish (port 5001)
                          PostgreSQL 16                  Prediction Engine
                          + pgvector                       |
                              |                        Redis 7
                          MinIO (S3)                    Cache + Queue
                          File Storage
```

**Multi-Tenancy**: Every table has `tenant_id`. Every query filters by tenant. Every Jena graph is tenant-scoped. Zero data leakage between tenants.
