// #334 거래 한 줄 카드 — TradesPage / DashboardPage 재사용
import { Badge } from "@/components/ui";
import { cn } from "@/lib/utils";

interface TradeRowProps {
  trade: any;
  compact?: boolean;
}

export default function TradeRow({ trade: t, compact = false }: TradeRowProps) {
  const isKis = t.market === "kis_us" || t.market === "kis_kr";
  const marketIcon = t.market === "kis_us" ? "🇺🇸" : t.market === "kis_kr" ? "🇰🇷" : "🪙";
  const isBuy = t.side === "buy";
  const profitPct = t.profit_pct;

  const priceFormatted = isKis
    ? `$${Number(t.price).toFixed(2)}`
    : `₩${Number(t.price).toLocaleString()}`;

  const time = (t.timestamp || "").slice(11, 16);
  const date = (t.timestamp || "").slice(5, 10);

  return (
    <div className={cn(
      "flex items-center gap-3 py-2 px-3 rounded-md hover:bg-accent transition-colors",
      compact && "py-1.5 px-2 text-sm",
    )}>
      <span className="text-base">{marketIcon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-semibold">{t.coin?.replace("KRW-", "")}</span>
          <Badge variant={isBuy ? "default" : "destructive"} className="text-[10px]">
            {isBuy ? "매수" : "매도"}
          </Badge>
          {profitPct != null && (
            <span className={cn(
              "text-xs font-semibold",
              profitPct > 0 && "text-success",
              profitPct < 0 && "text-destructive",
            )}>
              {profitPct > 0 ? "+" : ""}{profitPct.toFixed(2)}%
            </span>
          )}
        </div>
        {!compact && (
          <div className="text-xs text-muted-foreground truncate mt-0.5">
            {t.trigger_reason || "-"}
          </div>
        )}
      </div>
      <div className="text-right">
        <div className="text-sm font-medium">{priceFormatted}</div>
        <div className="text-[10px] text-muted-foreground">
          {date} {time}
        </div>
      </div>
    </div>
  );
}
