import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { campaigns, CampaignItem } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";

const CAMPAIGN_TYPES = [
  { value: "newsletter", label: "Newsletter" },
  { value: "peer_comparison", label: "Peer Comparison" },
  { value: "leadership_brief", label: "Leadership Brief" },
  { value: "disclosure_draft", label: "Disclosure Draft" },
] as const;

export function CampaignsPage() {
  const queryClient = useQueryClient();
  const [selectedType, setSelectedType] = useState("");
  const [genType, setGenType] = useState("newsletter");
  const [genTopic, setGenTopic] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const listQuery = useQuery({
    queryKey: ["campaigns", selectedType],
    queryFn: () => campaigns.list(selectedType || undefined),
  });

  const generateMutation = useMutation({
    mutationFn: () => campaigns.generate(genType, genTopic || undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
      setGenTopic("");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => campaigns.delete(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["campaigns"] }),
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Campaigns</h1>

      {/* Generator */}
      <Card>
        <CardHeader><CardTitle>Generate Campaign</CardTitle></CardHeader>
        <CardContent>
          <div className="flex flex-col sm:flex-row gap-3">
            <select
              value={genType}
              onChange={(e) => setGenType(e.target.value)}
              className="rounded-md border border-border bg-background px-3 py-2 text-sm"
            >
              {CAMPAIGN_TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
            <input
              type="text"
              placeholder="Topic (optional)"
              value={genTopic}
              onChange={(e) => setGenTopic(e.target.value)}
              className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm"
            />
            <button
              onClick={() => generateMutation.mutate()}
              disabled={generateMutation.isPending}
              className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {generateMutation.isPending ? "Generating..." : "Generate"}
            </button>
          </div>
          {generateMutation.isError && (
            <p className="mt-2 text-sm text-destructive">
              Failed to generate campaign. Check AI service configuration.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Filter */}
      <div className="flex gap-2">
        <button
          onClick={() => setSelectedType("")}
          className={`rounded-full px-3 py-1 text-xs font-medium ${!selectedType ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`}
        >
          All
        </button>
        {CAMPAIGN_TYPES.map((t) => (
          <button
            key={t.value}
            onClick={() => setSelectedType(t.value)}
            className={`rounded-full px-3 py-1 text-xs font-medium ${selectedType === t.value ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground"}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Campaign List */}
      {listQuery.isLoading && <p className="text-sm text-muted-foreground">Loading campaigns...</p>}

      {listQuery.data && listQuery.data.campaigns.length === 0 && (
        <p className="text-sm text-muted-foreground">No campaigns yet. Generate your first one above.</p>
      )}

      {listQuery.data?.campaigns.map((c: CampaignItem) => (
        <Card key={c.id}>
          <CardHeader className="cursor-pointer" onClick={() => setExpandedId(expandedId === c.id ? null : c.id)}>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-base">{c.title}</CardTitle>
                <div className="flex gap-2 mt-1">
                  <Badge variant="secondary">{c.type.replace("_", " ")}</Badge>
                  <Badge variant={c.status === "sent" ? "default" : "outline"}>{c.status}</Badge>
                  {c.topic && <span className="text-xs text-muted-foreground">{c.topic}</span>}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">
                  {c.created_at ? new Date(c.created_at).toLocaleDateString() : ""}
                </span>
                <button
                  onClick={(e) => { e.stopPropagation(); deleteMutation.mutate(c.id); }}
                  className="text-xs text-destructive hover:underline"
                >
                  Delete
                </button>
              </div>
            </div>
          </CardHeader>
          {expandedId === c.id && (
            <CardContent>
              <div className="prose prose-sm max-w-none whitespace-pre-wrap text-sm">
                {c.content}
              </div>
              {c.frameworks_referenced.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1">
                  {c.frameworks_referenced.map((fw) => (
                    <Badge key={fw} variant="outline" className="text-xs">{fw}</Badge>
                  ))}
                </div>
              )}
              <p className="mt-2 text-xs text-muted-foreground">
                Based on {c.articles_used} articles
              </p>
            </CardContent>
          )}
        </Card>
      ))}
    </div>
  );
}
