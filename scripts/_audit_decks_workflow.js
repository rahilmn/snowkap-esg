export const meta = {
  name: 'demo-deck-audit',
  description: 'Adversarially audit each rebuilt company deck for demo-readiness',
  phases: [
    { title: 'Audit', detail: 'one skeptic per company — genuine ESG? recs? noise?' },
    { title: 'Synthesize', detail: 'aggregate into a demo-readiness verdict' },
  ],
}

// args = [{slug, file}] — each `file` is an absolute path to that company's
// deck JSON (produced by scripts/_dump_deck.py <slug> --json).
const companies = typeof args === 'string' ? JSON.parse(args) : args

const VERDICT = {
  type: 'object',
  required: ['slug', 'demo_ready', 'genuine_critical_count', 'noise_in_critical', 'recs_ok', 'issues', 'one_line'],
  properties: {
    slug: { type: 'string' },
    demo_ready: { type: 'boolean' },
    genuine_critical_count: { type: 'integer', description: 'criticals that are GENUINE ESG-material events (not market/analyst/stock noise)' },
    noise_in_critical: { type: 'array', items: { type: 'string' }, description: 'titles of any market/stock/analyst-noise articles wrongly in the critical tier' },
    recs_ok: { type: 'boolean', description: 'every critical has >=1 recommendation with a named peer, a framework section, a Rs budget, and >=2 audit_trail entries' },
    fabrication_suspected: { type: 'array', items: { type: 'string' }, description: 'any lede/why/rec claim that looks fabricated or ungrounded' },
    issues: { type: 'array', items: { type: 'string' } },
    one_line: { type: 'string', description: 'one-sentence demo verdict for this company' },
  },
}

const verdicts = await parallel(companies.map((c) => async () => {
  const prompt = `You are a SKEPTICAL ESG editor doing final demo QA on one company's news deck.
Read the deck JSON at: ${c.file}

The product promise: each company shows 3 CRITICAL articles that are GENUINE, material ESG/sustainability/governance events (with recommendations), and the rest as lighter watch-list cards. Market/stock/analyst noise ("X vs Y better bet", "broker raises target", "shares gain", "top picks", "N stocks to watch") must NOT be in the critical tier.

Adversarially verify for slug "${c.slug}":
1. How many of the CRITICAL-tier articles are GENUINE ESG-material events vs market/stock/analyst NOISE? List any noise titles sitting in critical.
2. Does every critical carry >=1 real recommendation (named peer proper-noun, a framework section like BRSR/GRI/TCFD/SASB, a Rs budget that is not TBD/0, and >=2 audit_trail entries)?
3. Do any lede / why_it_matters / rec claims look fabricated or ungrounded (invented dates, Rs figures with no basis, claims the company did something a sector article never says)?
4. Net: is this deck demo_ready (>=3 genuine-or-defensible criticals, recs present, no glaring noise/fabrication)?

Be harsh — it is better to flag a weak deck now than to be embarrassed in the demo. Return the structured verdict.`
  return agent(prompt, { label: `audit:${c.slug}`, phase: 'Audit', schema: VERDICT })
}))

const clean = verdicts.filter(Boolean)
log(`audited ${clean.length}/${companies.length} decks`)

const summary = await agent(
  `You are the demo lead. Here are per-company deck audit verdicts (JSON):\n\n${JSON.stringify(clean, null, 2)}\n\n` +
  `Write a crisp demo-readiness report:\n` +
  `- A table: company | demo_ready | genuine criticals | noise-in-critical count | recs_ok.\n` +
  `- Call out the STRONGEST 2-3 companies to lead the demo with.\n` +
  `- Call out any company NOT demo-ready and the single most important fix.\n` +
  `- An overall verdict: is the set demo-ready, and the top 3 things to fix if not.\n` +
  `Be concrete and honest; do not inflate.`,
  { label: 'synthesize', phase: 'Synthesize' },
)

return { verdicts: clean, report: summary }
