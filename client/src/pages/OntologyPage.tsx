import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ontology } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";

export function OntologyPage() {
  const [sparqlQuery, setSparqlQuery] = useState(
    "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 20",
  );
  const [entityName, setEntityName] = useState("");

  const statsQuery = useQuery({
    queryKey: ["ontology-stats"],
    queryFn: ontology.stats,
  });

  const [sparqlResult, setSparqlResult] = useState<Record<string, unknown> | null>(null);
  const [sparqlLoading, setSparqlLoading] = useState(false);

  const [causalResult, setCausalResult] = useState<Record<string, unknown> | null>(null);
  const [causalLoading, setCausalLoading] = useState(false);

  async function runSparql() {
    setSparqlLoading(true);
    try {
      const result = await ontology.sparql(sparqlQuery);
      setSparqlResult(result);
    } catch (e) {
      setSparqlResult({ error: e instanceof Error ? e.message : "Query failed" });
    } finally {
      setSparqlLoading(false);
    }
  }

  async function runCausalExplorer() {
    setCausalLoading(true);
    try {
      const result = await ontology.causalExplorer(entityName);
      setCausalResult(result);
    } catch (e) {
      setCausalResult({ error: e instanceof Error ? e.message : "Explorer failed" });
    } finally {
      setCausalLoading(false);
    }
  }

  const stats = statsQuery.data;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Ontology Explorer</h1>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {Object.entries(stats).map(([key, value]) => (
            <Card key={key}>
              <CardContent className="p-3 text-center">
                <p className="text-xl font-bold">{value as number}</p>
                <p className="text-[10px] text-muted-foreground uppercase">
                  {key.replace(/_/g, " ")}
                </p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* SPARQL Query */}
      <Card>
        <CardHeader>
          <CardTitle>SPARQL Query</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <textarea
            className="w-full h-32 rounded-md border bg-transparent p-3 font-mono text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            value={sparqlQuery}
            onChange={(e) => setSparqlQuery(e.target.value)}
          />
          <Button onClick={runSparql} disabled={sparqlLoading}>
            {sparqlLoading ? <Spinner className="mr-2 h-4 w-4" /> : null}
            Run Query
          </Button>
          {sparqlResult && (
            <pre className="mt-3 max-h-80 overflow-auto rounded-md bg-muted p-3 text-xs font-mono">
              {JSON.stringify(sparqlResult, null, 2)}
            </pre>
          )}
        </CardContent>
      </Card>

      {/* Causal Chain Explorer */}
      <Card>
        <CardHeader>
          <CardTitle>Causal Chain Explorer</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2">
            <Input
              placeholder="Enter entity name (e.g., LPG, water scarcity, coal)"
              value={entityName}
              onChange={(e) => setEntityName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && runCausalExplorer()}
            />
            <Button onClick={runCausalExplorer} disabled={!entityName.trim() || causalLoading}>
              {causalLoading ? <Spinner className="mr-2 h-4 w-4" /> : null}
              Explore
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Finds all causal paths from a news entity to your tracked companies (max 4 hops).
          </p>
          {causalResult && (
            <CausalResultDisplay result={causalResult} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function CausalResultDisplay({ result }: { result: Record<string, unknown> }) {
  if ("error" in result) {
    return <p className="text-sm text-destructive">{String(result.error)}</p>;
  }

  const impacts = (result.impacts as Array<Record<string, unknown>>) ?? [];

  if (impacts.length === 0) {
    return <p className="text-sm text-muted-foreground">No causal chains found for this entity.</p>;
  }

  return (
    <div className="space-y-3">
      {impacts.map((impact, i) => (
        <div key={i} className="rounded-md border p-3">
          <div className="flex items-center gap-2 mb-2">
            <Badge variant="default">{String(impact.company_name)}</Badge>
            <span className="text-xs text-muted-foreground">
              {(impact.paths as unknown[])?.length ?? 0} path(s)
            </span>
          </div>
          {(impact.paths as Array<Record<string, unknown>>)?.map((path, j) => (
            <div key={j} className="flex items-center gap-1 text-xs mt-1">
              {(path.nodes as string[])?.map((node, k, arr) => (
                <span key={k} className="flex items-center gap-1">
                  <span className="font-medium bg-muted px-1.5 py-0.5 rounded">{node}</span>
                  {k < arr.length - 1 && <span className="text-muted-foreground">→</span>}
                </span>
              ))}
              <span className="ml-2 text-muted-foreground">
                (Score: {String(path.impact_score)})
              </span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
