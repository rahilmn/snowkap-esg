import { useQuery } from "@tanstack/react-query";
import { admin } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";

export function AdminPage() {
  const permissions = useAuthStore((s) => s.permissions);
  const isPlatformAdmin = permissions.includes("platform_admin");

  const usersQuery = useQuery({
    queryKey: ["admin-users"],
    queryFn: admin.users,
    enabled: permissions.includes("manage_users"),
  });

  const usageQuery = useQuery({
    queryKey: ["admin-usage"],
    queryFn: admin.usage,
    enabled: isPlatformAdmin,
  });

  const tenantsQuery = useQuery({
    queryKey: ["admin-tenants"],
    queryFn: admin.tenants,
    enabled: isPlatformAdmin,
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Admin</h1>

      {/* Platform Usage (platform admin only) */}
      {isPlatformAdmin && usageQuery.data && (
        <Card>
          <CardHeader><CardTitle>Platform Usage</CardTitle></CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
              <Stat label="Total Tenants" value={usageQuery.data.total_tenants} />
              <Stat label="Active Tenants" value={usageQuery.data.active_tenants} />
              <Stat label="Total Users" value={usageQuery.data.total_users} />
              <Stat label="Active Users (30d)" value={usageQuery.data.active_users_30d} />
              <Stat label="Total Articles" value={usageQuery.data.total_articles} />
              <Stat label="Total Predictions" value={usageQuery.data.total_predictions} />
            </div>
            {Object.keys(usageQuery.data.tenants_by_industry).length > 0 && (
              <div className="mt-4">
                <p className="text-sm font-medium mb-2">Tenants by Industry</p>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(usageQuery.data.tenants_by_industry).map(([industry, count]) => (
                    <Badge key={industry} variant="secondary">
                      {industry}: {count}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Tenants (platform admin only) */}
      {isPlatformAdmin && tenantsQuery.data && (
        <Card>
          <CardHeader><CardTitle>Tenants</CardTitle></CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-2 font-medium">Name</th>
                    <th className="pb-2 font-medium">Domain</th>
                    <th className="pb-2 font-medium">Industry</th>
                    <th className="pb-2 font-medium">Users</th>
                    <th className="pb-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {tenantsQuery.data.map((t) => (
                    <tr key={t.id} className="border-b">
                      <td className="py-2 font-medium">{t.name}</td>
                      <td className="py-2 text-muted-foreground">{t.domain}</td>
                      <td className="py-2">{t.industry ?? "-"}</td>
                      <td className="py-2">{t.user_count}</td>
                      <td className="py-2">
                        <Badge variant={t.is_active ? "default" : "destructive"}>
                          {t.is_active ? "Active" : "Inactive"}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Tenant Users */}
      {usersQuery.data && (
        <Card>
          <CardHeader><CardTitle>Users</CardTitle></CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-2 font-medium">Email</th>
                    <th className="pb-2 font-medium">Designation</th>
                    <th className="pb-2 font-medium">Role</th>
                    <th className="pb-2 font-medium">Status</th>
                    <th className="pb-2 font-medium">Last Login</th>
                  </tr>
                </thead>
                <tbody>
                  {usersQuery.data.map((u) => (
                    <tr key={u.id} className="border-b">
                      <td className="py-2 font-medium">{u.email}</td>
                      <td className="py-2">{u.designation ?? "-"}</td>
                      <td className="py-2"><Badge variant="secondary">{u.role ?? "member"}</Badge></td>
                      <td className="py-2">
                        <Badge variant={u.is_active ? "default" : "destructive"}>
                          {u.is_active ? "Active" : "Inactive"}
                        </Badge>
                      </td>
                      <td className="py-2 text-muted-foreground text-xs">
                        {u.last_login ? new Date(u.last_login).toLocaleDateString() : "Never"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="text-center">
      <p className="text-2xl font-bold">{value}</p>
      <p className="text-xs text-muted-foreground">{label}</p>
    </div>
  );
}
