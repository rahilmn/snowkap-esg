import { useQuery } from "@tanstack/react-query";
import { companies } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";

export function CompaniesPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["companies"],
    queryFn: () => companies.list(),
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Companies</h1>
      <p className="text-sm text-muted-foreground">
        ESG analysis targets tracked in your tenant's knowledge graph.
      </p>

      {isLoading ? (
        <div className="flex justify-center py-12"><Spinner /></div>
      ) : data?.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            No companies added yet. Companies are auto-provisioned when your tenant is created.
          </CardContent>
        </Card>
      ) : (
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
          {data?.map((company) => (
            <Card key={company.id} className="hover:shadow-md transition-shadow">
              <CardContent className="p-5">
                <h3 className="font-semibold">{company.name}</h3>
                <p className="text-sm text-muted-foreground mt-1">{company.domain}</p>
                <div className="flex flex-wrap gap-2 mt-3">
                  {company.industry && (
                    <Badge variant="secondary">{company.industry}</Badge>
                  )}
                  {company.sasb_category && (
                    <Badge variant="outline">{company.sasb_category}</Badge>
                  )}
                  <Badge variant={company.status === "active" ? "default" : "secondary"}>
                    {company.status}
                  </Badge>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
