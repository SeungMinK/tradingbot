// #334 PnL Hero — 누적 손익 큰 강조 (코인 봇)
import { Card } from "@/components/ui";
import { cn } from "@/lib/utils";

interface PnLHeroProps {
  totalAsset: number | null;
  totalPnl: number | null;
  pnlPct: number | null;
  totalDeposits: number | null;
  positionCount: number;
}

export default function PnLHero({
  totalAsset,
  totalPnl,
  pnlPct,
  totalDeposits,
  positionCount,
}: PnLHeroProps) {
  const isPositive = totalPnl !== null && totalPnl > 0;
  const isNegative = totalPnl !== null && totalPnl < 0;

  return (
    <Card className={cn(
      "p-5 border-l-4",
      isPositive && "border-l-success",
      isNegative && "border-l-destructive",
      !isPositive && !isNegative && "border-l-muted",
    )}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
            🪙 코인 봇 — 누적 손익률
          </div>
          <div className={cn(
            "mt-2 text-3xl font-bold",
            isPositive && "text-success",
            isNegative && "text-destructive",
          )}>
            {pnlPct !== null
              ? `${pnlPct > 0 ? "+" : ""}${pnlPct.toFixed(2)}%`
              : <span className="text-xl text-muted-foreground">기준 미설정</span>}
          </div>
          {totalPnl !== null && (
            <div className="mt-1 text-sm text-muted-foreground">
              {totalPnl > 0 ? "+" : ""}₩{Math.round(Math.abs(totalPnl)).toLocaleString()}
              {totalPnl < 0 && " 손실"}
              {totalDeposits && ` · 누적 입금 ₩${Math.round(totalDeposits).toLocaleString()}`}
            </div>
          )}
        </div>

        <div className="text-right shrink-0">
          <div className="text-xs font-medium text-muted-foreground uppercase">총 자산</div>
          <div className="mt-1 text-xl font-bold">
            {totalAsset !== null ? `₩${Math.round(totalAsset).toLocaleString()}` : "-"}
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            {positionCount > 0 ? `${positionCount}종목 보유` : "포지션 없음"}
          </div>
        </div>
      </div>
    </Card>
  );
}
