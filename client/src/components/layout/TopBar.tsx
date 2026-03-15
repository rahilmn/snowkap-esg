import { useAuthStore } from "@/stores/authStore";
import { Button } from "@/components/ui/Button";

export function TopBar() {
  const { domain, designation, name, logout } = useAuthStore();

  return (
    <header className="flex h-14 items-center justify-between border-b px-6">
      <div />
      <div className="flex items-center gap-4">
        <div className="text-right text-sm">
          <p className="font-medium">{name || domain}</p>
          <p className="text-xs text-muted-foreground">{designation}</p>
        </div>
        <Button variant="ghost" size="sm" onClick={logout}>
          Sign Out
        </Button>
      </div>
    </header>
  );
}
