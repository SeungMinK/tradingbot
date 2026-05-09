// #332 Phase 1 PoC: 기존 inline class → shadcn/ui Card 기반
import { Card, CardContent } from "@/components/ui";
import { cn } from "@/lib/utils";

interface StatCardProps {
  label: string;
  value: string;
  sub?: string;
  valueClass?: string;
}

export default function StatCard({ label, value, sub, valueClass }: StatCardProps) {
  return (
    <Card>
      <CardContent className="pt-6">
        <div className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </div>
        <div className={cn(
          "mt-2 text-2xl font-bold",
          valueClass === "positive" && "text-success",
          valueClass === "negative" && "text-destructive",
        )}>
          {value}
        </div>
        {sub && (
          <div className="mt-1 text-xs text-muted-foreground">{sub}</div>
        )}
      </CardContent>
    </Card>
  );
}
