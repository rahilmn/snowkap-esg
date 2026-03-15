import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { news, agent } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { Card, CardContent } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { formatDate } from "@/lib/utils";
import type { Article, ArticleScore, ArticlePrediction } from "@/types";

export function NewsFeedPage() {
  const name = useAuthStore((s) => s.name);

  const { data: articles, isLoading } = useQuery({
    queryKey: ["news"],
    queryFn: () => news.list({ limit: 50 }),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">
            {name ? `Hello ${name}, here are news that might impact you` : "Smart News Feed"}
          </h1>
          {!name && (
            <p className="text-sm text-muted-foreground">
              Articles scored by causal chain impact to your companies
            </p>
          )}
        </div>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-12"><Spinner /></div>
      ) : articles?.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            No articles yet. News is automatically curated based on your company domain.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {articles?.map((article) => (
            <NewsCard key={article.id} article={article} />
          ))}
        </div>
      )}
    </div>
  );
}

function NewsCard({ article }: { article: Article }) {
  const pillarVariant = article.esg_pillar?.toLowerCase() === "environmental"
    ? "environmental"
    : article.esg_pillar?.toLowerCase() === "social"
      ? "social"
      : article.esg_pillar?.toLowerCase() === "governance"
        ? "governance"
        : "secondary";

  const topImpact = article.impact_scores?.[0];

  return (
    <Card className="hover:shadow-md transition-shadow">
      <CardContent className="p-5">
        <div className="flex gap-4">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-2">
              {article.esg_pillar && (
                <Badge variant={pillarVariant}>{article.esg_pillar}</Badge>
              )}
              {article.sentiment && (
                <Badge variant="outline" className="text-xs">
                  {article.sentiment}
                </Badge>
              )}
              {article.source && (
                <span className="text-xs text-muted-foreground">{article.source}</span>
              )}
              {article.published_at && (
                <span className="text-xs text-muted-foreground">{formatDate(article.published_at)}</span>
              )}
            </div>

            <h3 className="font-semibold text-sm leading-snug mb-1">
              {article.url ? (
                <a href={article.url} target="_blank" rel="noopener noreferrer" className="hover:text-primary">
                  {article.title}
                </a>
              ) : (
                article.title
              )}
            </h3>

            {article.summary && (
              <p className="text-sm text-muted-foreground line-clamp-2">{article.summary}</p>
            )}

            {/* Entities */}
            {article.entities?.length > 0 && (
              <div className="flex flex-wrap gap-1 mt-2">
                {article.entities.slice(0, 5).map((entity, i) => (
                  <span key={i} className="text-xs bg-muted px-2 py-0.5 rounded-full">
                    {entity}
                  </span>
                ))}
                {article.entities.length > 5 && (
                  <span className="text-xs text-muted-foreground">+{article.entities.length - 5} more</span>
                )}
              </div>
            )}
          </div>

          {/* Impact Score Panel */}
          {topImpact && <ImpactPanel score={topImpact} />}
        </div>

        {/* Framework Alignment */}
        {article.frameworks?.length > 0 && (
          <div className="mt-3 pt-3 border-t">
            <p className="text-xs font-medium text-muted-foreground mb-2">Framework Alignment</p>
            <div className="flex flex-wrap gap-1">
              {article.frameworks.map((fw) => (
                <Badge key={fw} variant="outline" className="text-[10px] bg-blue-50 text-blue-700 border-blue-200">
                  {fw}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {/* Causal Chain Preview */}
        {article.impact_scores?.length > 0 && (
          <div className="mt-3 pt-3 border-t">
            <p className="text-xs font-medium text-muted-foreground mb-2">Impact Analysis</p>
            <div className="space-y-1">
              {article.impact_scores.slice(0, 3).map((score, i) => (
                <CausalChainRow key={i} score={score} />
              ))}
            </div>
          </div>
        )}

        {/* Inline Predictions */}
        {article.predictions?.length > 0 && (
          <div className="mt-3 pt-3 border-t">
            <p className="text-xs font-medium text-muted-foreground mb-2">Predictions</p>
            <div className="space-y-2">
              {article.predictions.map((pred) => (
                <PredictionCard key={pred.id} prediction={pred} />
              ))}
            </div>
          </div>
        )}

        {/* Ask About This News — Phase 11 */}
        <AskAboutNewsButton articleId={article.id} />
      </CardContent>
    </Card>
  );
}

function ImpactPanel({ score }: { score: ArticleScore }) {
  const color =
    score.impact_score >= 70 ? "text-red-600 bg-red-50 border-red-200" :
    score.impact_score >= 40 ? "text-amber-600 bg-amber-50 border-amber-200" :
    "text-green-600 bg-green-50 border-green-200";

  return (
    <div className={`flex-shrink-0 w-20 rounded-md border p-2 text-center ${color}`}>
      <p className="text-xl font-bold">{score.impact_score}</p>
      <p className="text-[10px] font-medium uppercase">Impact</p>
      <p className="text-[10px] mt-1">{score.causal_hops} hop{score.causal_hops !== 1 ? "s" : ""}</p>
    </div>
  );
}

function CausalChainRow({ score }: { score: ArticleScore }) {
  return (
    <div className="flex items-center gap-2 text-xs flex-wrap">
      <span className="font-medium text-foreground">{score.company_name}</span>
      <span className="text-muted-foreground">via</span>
      <Badge variant="outline" className="text-[10px]">{score.relationship_type}</Badge>
      {score.frameworks?.length > 0 && score.frameworks.map((fw) => (
        <Badge key={fw} variant="outline" className="text-[10px] bg-blue-50 text-blue-700 border-blue-200">{fw}</Badge>
      ))}
      <span className="text-muted-foreground ml-auto">
        Score: {score.impact_score} | {score.causal_hops} hop{score.causal_hops !== 1 ? "s" : ""}
      </span>
    </div>
  );
}

function PredictionCard({ prediction }: { prediction: ArticlePrediction }) {
  const riskColor =
    prediction.risk_level === "high" ? "text-red-700 bg-red-50 border-red-200" :
    prediction.risk_level === "medium" ? "text-amber-700 bg-amber-50 border-amber-200" :
    "text-green-700 bg-green-50 border-green-200";

  return (
    <div className={`rounded-md border p-3 ${riskColor}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold">{prediction.title}</p>
          {prediction.summary && (
            <p className="text-[11px] mt-1 line-clamp-2">{prediction.summary}</p>
          )}
          {prediction.prediction_text && (
            <p className="text-[11px] mt-1 italic line-clamp-2">{prediction.prediction_text}</p>
          )}
        </div>
        <div className="flex flex-col items-end gap-1 flex-shrink-0">
          <span className="text-sm font-bold">{(prediction.confidence_score * 100).toFixed(0)}%</span>
          <span className="text-[10px] uppercase font-medium">{prediction.risk_level ?? "unknown"} risk</span>
          {prediction.time_horizon && (
            <Badge variant="outline" className="text-[10px]">{prediction.time_horizon}</Badge>
          )}
          {prediction.financial_impact != null && (
            <span className="text-[10px]">
              {prediction.financial_impact >= 10000000
                ? `₹${(prediction.financial_impact / 10000000).toFixed(1)}Cr`
                : `₹${(prediction.financial_impact / 100000).toFixed(1)}L`}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

function AskAboutNewsButton({ articleId }: { articleId: string }) {
  const [isOpen, setIsOpen] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [analysis, setAnalysis] = useState<{
    response: string;
    agent: { id: string; name: string };
    causal_chains: Array<{
      source_entity: string;
      target_entity: string;
      relationship_type: string;
      hops: number;
      impact_score: number;
    }>;
    prediction_available: boolean;
  } | null>(null);

  async function handleAsk() {
    setIsOpen(true);
    if (analysis) return; // Already loaded

    setIsLoading(true);
    try {
      const result = await agent.askAboutNews(articleId);
      setAnalysis(result);
    } catch {
      setAnalysis({
        response: "Failed to analyze this article. Please try again.",
        agent: { id: "analytics", name: "ESG Analytics Agent" },
        causal_chains: [],
        prediction_available: false,
      });
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <>
      <div className="mt-3 pt-3 border-t">
        <Button variant="outline" size="sm" onClick={handleAsk} className="text-xs">
          Ask AI about this news
        </Button>
      </div>

      {isOpen && (
        <div className="mt-3 rounded-md border bg-muted/50 p-3">
          {isLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground py-4">
              <Spinner className="h-4 w-4" />
              Agent is analyzing this article...
            </div>
          ) : analysis ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <Badge variant="outline" className="text-[10px]">
                  {analysis.agent.name}
                </Badge>
                {analysis.prediction_available && (
                  <Badge className="bg-blue-500 text-white text-[10px]">
                    Prediction Available
                  </Badge>
                )}
              </div>

              <p className="text-sm whitespace-pre-line">{analysis.response}</p>

              {analysis.causal_chains.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground mb-1">Causal Chains</p>
                  <div className="space-y-1">
                    {analysis.causal_chains.map((chain, i) => (
                      <div key={i} className="flex items-center gap-1 text-xs">
                        <span>{chain.source_entity}</span>
                        <span className="text-muted-foreground">-&gt;</span>
                        <span>{chain.target_entity}</span>
                        <Badge variant="outline" className="text-[10px] ml-1">
                          {chain.relationship_type}
                        </Badge>
                        <span className="text-muted-foreground ml-auto">
                          impact: {(chain.impact_score * 100).toFixed(0)}%
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <Button
                variant="ghost"
                size="sm"
                className="text-xs"
                onClick={() => { setIsOpen(false); }}
              >
                Close
              </Button>
            </div>
          ) : null}
        </div>
      )}
    </>
  );
}
