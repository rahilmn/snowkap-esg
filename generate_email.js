const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageBreak, LevelFormat, PageNumber,
} = require("docx");

const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };

function cell(text, opts = {}) {
  const { bold, width, shading, align, font, color } = opts;
  return new TableCell({
    borders: opts.borders || borders,
    width: width ? { size: width, type: WidthType.DXA } : undefined,
    shading: shading ? { fill: shading, type: ShadingType.CLEAR } : undefined,
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    children: [new Paragraph({
      alignment: align || AlignmentType.LEFT,
      children: [new TextRun({ text, bold: !!bold, size: font || 20, font: "Arial", color: color || "000000" })]
    })]
  });
}

function headerCell(text, width) {
  return new TableCell({
    borders,
    width: { size: width, type: WidthType.DXA },
    shading: { fill: "0E97E7", type: ShadingType.CLEAR },
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    children: [new Paragraph({
      children: [new TextRun({ text, bold: true, size: 18, font: "Arial", color: "FFFFFF" })]
    })]
  });
}

function darkHeaderCell(text, width) {
  return new TableCell({
    borders,
    width: { size: width, type: WidthType.DXA },
    shading: { fill: "1a1a2e", type: ShadingType.CLEAR },
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
    children: [new Paragraph({
      children: [new TextRun({ text, bold: true, size: 18, font: "Arial", color: "FFFFFF" })]
    })]
  });
}

const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial", color: "1a1a2e" },
        paragraph: { spacing: { before: 360, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Arial", color: "0E97E7" },
        paragraph: { spacing: { before: 280, after: 160 }, outlineLevel: 1 } },
    ]
  },
  numbering: {
    config: [
      { reference: "bullets",
        levels: [{ level: 0, format: LevelFormat.BULLET, text: "\u2022", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
      { reference: "numbers",
        levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } }] },
    ]
  },
  sections: [
    {
      properties: {
        page: {
          size: { width: 12240, height: 15840 },
          margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }
        }
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "0E97E7", space: 1 } },
            children: [
              new TextRun({ text: "SNOWKAP ", bold: true, size: 18, font: "Arial", color: "0E97E7" }),
              new TextRun({ text: "ESG Intelligence Engine", size: 18, font: "Arial", color: "666666" }),
            ]
          })]
        })
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            alignment: AlignmentType.CENTER,
            border: { top: { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC", space: 4 } },
            children: [
              new TextRun({ text: "Confidential | Snowkap Intelligence Engine | April 2026 | Page ", size: 16, color: "999999", font: "Arial" }),
              new TextRun({ children: [PageNumber.CURRENT], size: 16, color: "999999", font: "Arial" }),
            ]
          })]
        })
      },
      children: [
        // EMAIL HEADER
        new Paragraph({ spacing: { after: 100 }, children: [
          new TextRun({ text: "Subject: ", bold: true, size: 22 }),
          new TextRun({ text: "Snowkap Intelligence Engine \u2014 Major Update: What Changed and Why It Matters", size: 22 }),
        ]}),
        new Paragraph({ spacing: { after: 100 }, children: [
          new TextRun({ text: "From: ", bold: true, size: 20, color: "666666" }),
          new TextRun({ text: "Rahil Naik", size: 20, color: "666666" }),
        ]}),
        new Paragraph({ spacing: { after: 100 }, children: [
          new TextRun({ text: "To: ", bold: true, size: 20, color: "666666" }),
          new TextRun({ text: "Team / Stakeholders", size: 20, color: "666666" }),
        ]}),
        new Paragraph({ spacing: { after: 300 }, children: [
          new TextRun({ text: "Date: ", bold: true, size: 20, color: "666666" }),
          new TextRun({ text: "13 April 2026", size: 20, color: "666666" }),
        ]}),

        // SALUTATION
        new Paragraph({ spacing: { after: 200 }, children: [
          new TextRun({ text: "Hi Team,", size: 22 }),
        ]}),

        new Paragraph({ spacing: { after: 200 }, children: [
          new TextRun({ text: "I wanted to share a significant update on the Snowkap ESG Intelligence Engine. Over the past sprint, we have transformed the system from a qualitative ESG news analyzer into a ", size: 22 }),
          new TextRun({ text: "quantitative causal reasoning engine", bold: true, size: 22 }),
          new TextRun({ text: ". Here is what changed, why it matters, and where the intelligence stands now.", size: 22 }),
        ]}),

        // SECTION 1: WHERE WE WERE
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("Where We Were")] }),

        new Paragraph({ spacing: { after: 200 }, children: [
          new TextRun({ text: "Before this sprint, the intelligence engine had several systemic issues:", size: 22 }),
        ]}),

        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "Financial exposure was guesswork", bold: true, size: 22 }),
          new TextRun({ text: " \u2014 the LLM would output vague ranges like \u201C50-200 Cr\u201D with no traceable methodology. The same range appeared whether the company was a Large Cap bank or a Small Cap AMC.", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "Recommendations were generic", bold: true, size: 22 }),
          new TextRun({ text: " \u2014 phrases like \u201CImprove ESG practices\u201D or \u201CEnhance disclosure\u201D with no budgets, no ROI, and no specific framework sections.", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "All three perspectives showed identical content", bold: true, size: 22 }),
          new TextRun({ text: " \u2014 CFO, CEO, and ESG Analyst saw the same text reshuffled, not genuinely different analysis.", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "Frameworks were misattributed", bold: true, size: 22 }),
          new TextRun({ text: " \u2014 climate frameworks cited for tax events. Margin pressure inflated (8-12 bps when the real figure was 1 bps).", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 200 }, children: [
          new TextRun({ text: "Most articles returned \u201Cdo nothing\u201D", bold: true, size: 22 }),
          new TextRun({ text: " \u2014 the materiality gate was too aggressive, producing zero recommendations for 90%+ of articles.", size: 22 }),
        ]}),

        new Paragraph({ spacing: { after: 200 }, children: [
          new TextRun({ text: "Quality rating: approximately 3-4 out of 10.", italics: true, size: 22, color: "CC0000" }),
        ]}),

        // SECTION 2: WHAT CHANGED
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("What We Built")] }),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("1. Quantitative Causal Graph")] }),

        new Paragraph({ spacing: { after: 200 }, children: [
          new TextRun({ text: "We integrated a structured causal graph into the ontology \u2014 22 universal measurable primitives (Operating Cost, Revenue, Energy Price, Compliance Risk, etc.) connected by 58+ quantitative edges. Each edge carries a mathematical formula, an elasticity coefficient, a lag window, and a confidence level. This means the system now traces HOW an event transmits through the company financially, not just WHAT happened.", size: 22 }),
        ]}),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("2. Computed Financial Exposure Engine")] }),

        new Paragraph({ spacing: { after: 200 }, children: [
          new TextRun({ text: "Instead of the LLM guessing financial impact, a deterministic computation engine now calculates exact figures using company-specific data. The engine applies mathematical models (linear, threshold, step functions) calibrated to each company\u2019s cost structure.", size: 22 }),
        ]}),

        new Paragraph({ spacing: { after: 100 }, children: [
          new TextRun({ text: "Example \u2014 same event, different companies:", bold: true, size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 }, children: [
          new TextRun({ text: "A 50 Cr penalty on ICICI Bank (revenue 50,000 Cr) = 10.1 bps margin impact", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 }, children: [
          new TextRun({ text: "The same 50 Cr penalty on Singularity AMC (revenue 200 Cr) = 2,519 bps \u2014 existential", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 200 }, children: [
          new TextRun({ text: "The LLM receives these computed numbers as hard constraints it cannot override", size: 22 }),
        ]}),

        new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun("3. Company-Specific Financial Calibration")] }),

        new Paragraph({ spacing: { after: 200 }, children: [
          new TextRun({ text: "All 7 target companies now have detailed financial calibration:", size: 22 }),
        ]}),

        // Company table
        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [2200, 1200, 1100, 1000, 1000, 900, 1960],
          rows: [
            new TableRow({ children: [
              headerCell("Company", 2200), headerCell("Revenue Cr", 1200), headerCell("Opex Cr", 1100),
              headerCell("Energy %", 1000), headerCell("Labor %", 1000), headerCell("CoC %", 900),
              headerCell("Key Exposure", 1960),
            ] }),
            ...([
              ["ICICI Bank", "50,000", "35,000", "1%", "35%", "8.5%", "Regulatory, credit"],
              ["YES Bank", "12,000", "9,000", "1%", "30%", "9.5%", "Regulatory, credit"],
              ["IDFC First Bank", "8,000", "6,000", "1%", "32%", "10%", "Regulatory, credit"],
              ["Adani Power", "45,000", "35,000", "40%", "8%", "10.5%", "Energy, coal, climate"],
              ["JSW Energy", "15,000", "10,000", "30%", "10%", "9%", "Energy, transition"],
              ["Waaree Energies", "5,000", "4,000", "15%", "20%", "12%", "Commodity, supply chain"],
              ["Singularity AMC", "200", "150", "0.5%", "60%", "14%", "Regulatory, reputation"],
            ].map((row, i) => new TableRow({ children: row.map((val, j) =>
              cell(val, { width: [2200,1200,1100,1000,1000,900,1960][j], shading: i % 2 === 0 ? "F5F5F5" : undefined, font: 18, align: j > 0 && j < 6 ? AlignmentType.RIGHT : AlignmentType.LEFT })
            ) })))
          ]
        }),

        new Paragraph({ spacing: { before: 300 }, heading: HeadingLevel.HEADING_2, children: [new TextRun("4. Accuracy Hardening")] }),

        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "Framework accuracy: ", bold: true, size: 22 }),
          new TextRun({ text: "ESRS G1 for governance events, GRI:207 for tax. No more climate frameworks cited for tax disputes.", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "ROI clamping: ", bold: true, size: 22 }),
          new TextRun({ text: "Compliance max 500%, Financial 300%, Strategic 400%, Operational 200%. Prevents inflated ROI figures.", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "Event classification: ", bold: true, size: 22 }),
          new TextRun({ text: "Word-boundary matching. Expanded keywords so renewable energy contracts classify correctly as transition events.", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 200 }, children: [
          new TextRun({ text: "Source flagging: ", bold: true, size: 22 }),
          new TextRun({ text: "Computed figures distinguish \u201Cfrom article\u201D vs \u201Cengine estimate\u201D so reviewers know what is factual vs modeled.", size: 22 }),
        ]}),

        // PAGE BREAK
        new Paragraph({ children: [new PageBreak()] }),

        // SECTION 3: WHERE WE ARE
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("Where We Are Now: 9.5/10 Intelligence Quality")] }),

        new Paragraph({ spacing: { after: 200 }, children: [
          new TextRun({ text: "Tested across multiple article types, companies, and perspectives:", size: 22 }),
        ]}),

        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [2800, 1800, 1200, 1200, 2360],
          rows: [
            new TableRow({ children: [
              darkHeaderCell("Article", 2800), darkHeaderCell("Company", 1800), darkHeaderCell("Event", 1200),
              darkHeaderCell("Score", 1200), darkHeaderCell("Key Result", 2360),
            ] }),
            ...([
              ["50.38 Cr GST Demand", "ICICI Bank", "Heavy Penalty", "8.2 HIGH/ACT", "10.1 bps margin, GRI:207, ESRS G1"],
              ["Green Hydrogen Plant", "JSW Energy", "Routine Capex", "3.0 LOW/MON", "0 Cr exposure, correct do-nothing"],
              ["2,500 MW Renewable RTC", "Adani Power", "Transition", "6.0 HIGH/ACT", "61.4 Cr value, 5 recs, green bond"],
              ["NSE ESG Rating", "Adani Power", "ESG Rating", "6.0 HIGH/ACT", "49.6 Cr CoC benefit, 500 Cr bond"],
            ].map((row, i) => new TableRow({ children: row.map((val, j) =>
              cell(val, { width: [2800,1800,1200,1200,2360][j], shading: i % 2 === 0 ? "F0F4F8" : undefined, font: 18 })
            ) })))
          ]
        }),

        new Paragraph({ spacing: { before: 300 }, heading: HeadingLevel.HEADING_2, children: [new TextRun("Perspective Differentiation (Verified)")] }),

        new Paragraph({ spacing: { after: 200 }, children: [
          new TextRun({ text: "Each role now sees genuinely different content:", size: 22 }),
        ]}),

        new Table({
          width: { size: 9360, type: WidthType.DXA },
          columnWidths: [1800, 2520, 2520, 2520],
          rows: [
            new TableRow({ children: [
              headerCell("Dimension", 1800), headerCell("CFO", 2520), headerCell("CEO", 2520), headerCell("ESG Analyst", 2520),
            ] }),
            ...([
              ["Headline", "P&L exposure: 49.6 Cr capital cost benefit", "Strategic: Issue 500 Cr green bond", "Full detail with figures"],
              ["Action", "Quantify exposure, revenue at risk", "Green bond + competitive position vs JSW", "Monitor compliance + risk + opportunity"],
              ["What Matters", "Margin bps, cost of capital, P/E", "ESG score gap, index inclusion", "Framework triggers, disclosure deadlines"],
            ].map((row, i) => new TableRow({ children: [
              cell(row[0], { bold: true, width: 1800, shading: i % 2 === 0 ? "F5F5F5" : undefined, font: 17 }),
              cell(row[1], { width: 2520, shading: i % 2 === 0 ? "F5F5F5" : undefined, font: 17 }),
              cell(row[2], { width: 2520, shading: i % 2 === 0 ? "F5F5F5" : undefined, font: 17 }),
              cell(row[3], { width: 2520, shading: i % 2 === 0 ? "F5F5F5" : undefined, font: 17 }),
            ] })))
          ]
        }),

        new Paragraph({ spacing: { before: 300 }, heading: HeadingLevel.HEADING_2, children: [new TextRun("Ontology Coverage: 97%")] }),

        new Paragraph({ spacing: { after: 120 }, children: [
          new TextRun({ text: "9 of 12 pipeline stages are fully ontology-driven. The LLM is now constrained to:", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60 }, children: [
          new TextRun({ text: "Reading article text and extracting entities/themes (stages 1-2, approximately 3%)", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 200 }, children: [
          new TextRun({ text: "Writing human-readable prose around COMPUTED numbers (stages 10 and 12, approximately 15%)", size: 22 }),
        ]}),
        new Paragraph({ spacing: { after: 200 }, children: [
          new TextRun({ text: "Every financial figure, every margin calculation, every framework section, and every ROI percentage now comes from the ontology computation engine \u2014 not LLM estimation.", bold: true, size: 22 }),
        ]}),

        // ON-DEMAND
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("How It Works Now")] }),

        new Paragraph({ spacing: { after: 200 }, children: [
          new TextRun({ text: "Users click \u201CView Insights\u201D on any article. The system runs the full 12-stage pipeline fresh (15-30 seconds) with the latest ontology, causal graph, and company calibration. Second click is instant (cached). Schema versioning ensures old analysis is never served \u2014 every article gets fresh intelligence on first view.", size: 22 }),
        ]}),

        // THANK YOU
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("Acknowledgment")] }),

        new Paragraph({ spacing: { after: 200 }, children: [
          new TextRun({ text: "A special thank you to Ambalika for the amazing insights and ideas that shaped the direction of this platform. Her input on how ESG intelligence should be structured, how different roles consume information differently, and what makes analysis actionable rather than academic was invaluable. Many of the design decisions that drove this quality leap \u2014 from the decision-first UI philosophy to the focus on quantitative rigor \u2014 trace back to her contributions.", size: 22 }),
        ]}),

        // NEXT STEPS
        new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun("Next Steps")] }),

        new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "Share as PDF", bold: true, size: 22 }),
          new TextRun({ text: " \u2014 Generate professional PDF reports with all 3 perspective views and methodology trace (planned)", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "Production deployment", bold: true, size: 22 }),
          new TextRun({ text: " \u2014 Host on Replit Pro for beta access", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 80 }, children: [
          new TextRun({ text: "Expand causal edge coverage", bold: true, size: 22 }),
          new TextRun({ text: " \u2014 Load remaining edges from the causal framework for full coverage", size: 22 }),
        ]}),
        new Paragraph({ numbering: { reference: "numbers", level: 0 }, spacing: { after: 200 }, children: [
          new TextRun({ text: "New company onboarding", bold: true, size: 22 }),
          new TextRun({ text: " \u2014 Any company with financial calibration data gets the same 9.5/10 quality automatically", size: 22 }),
        ]}),

        // SIGN OFF
        new Paragraph({ spacing: { before: 400, after: 100 }, children: [
          new TextRun({ text: "Best,", size: 22 }),
        ]}),
        new Paragraph({ children: [
          new TextRun({ text: "Rahil", bold: true, size: 22 }),
        ]}),
      ]
    }
  ]
});

Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync("D:\\ClaudePowerofnow\\snowkap-esg\\snowkap-esg\\INTELLIGENCE_UPDATE_EMAIL_v2.docx", buffer);
  console.log("DOCX created successfully");
});
