// #333 봇 상태 배너 — 페이지 최상단, 한눈에 보는 핵심 정보
import { Card } from "@/components/ui";
import { cn } from "@/lib/utils";

interface BotStatusBannerProps {
  marketCapital: any;
  marketUniverse: any;
  recentTrades: any[];
}

export default function BotStatusBanner({
  marketCapital,
  marketUniverse,
  recentTrades,
}: BotStatusBannerProps) {
  // KIS US 잔고 추출
  const kisUs = marketCapital?.markets?.find((m: any) => m.market === "kis_us");
  const usdBalance = kisUs?.live?.available;
  const usdCurrency = kisUs?.live?.currency;

  // 활성 종목 수
  const kisUsRow = marketUniverse?.markets?.find((m: any) => m.market === "kis_us");
  const kisUsTradingOn = kisUsRow?.trading_enabled;
  const kisUsCount = kisUsRow?.symbol_count || 0;

  // 코인 활성 전략
  const upbit = marketUniverse?.markets?.find((m: any) => m.market === "upbit");
  const coinStrategy = upbit?.strategy?.display_name || "비활성";

  // 오늘 거래 수
  const today = new Date().toISOString().slice(0, 10);
  const todayTrades = recentTrades.filter((t: any) =>
    (t.timestamp || "").startsWith(today)
  );
  const todayTradeCount = todayTrades.length;
  const todayPnl = todayTrades
    .filter((t: any) => t.profit_pct != null)
    .reduce((s: number, t: any) => s + (t.profit_pct || 0), 0);

  return (
    <Card className="mb-4 border-l-4 border-l-primary">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-4">
        {/* 1. KIS 미국주식 봇 */}
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-medium text-muted-foreground">🇺🇸 미국주식 봇</span>
            <span className={cn(
              "inline-flex items-center px-1.5 py-0.5 text-[10px] font-semibold rounded-md",
              kisUsTradingOn ? "bg-success text-white" : "bg-muted text-muted-foreground"
            )}>
              {kisUsTradingOn ? "ON" : "OFF"}
            </span>
          </div>
          <div className="text-xl font-bold">
            {usdBalance != null
              ? `${Number(usdBalance).toFixed(2)} ${usdCurrency || "USD"}`
              : <span className="text-base text-muted-foreground">조회불가</span>}
          </div>
          <div className="text-xs text-muted-foreground mt-1">
            모니터링 {kisUsCount}종목
          </div>
        </div>

        {/* 2. 코인 봇 */}
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-medium text-muted-foreground">🪙 코인 봇</span>
            <span className="inline-flex items-center px-1.5 py-0.5 text-[10px] font-semibold rounded-md bg-success text-white">
              ON
            </span>
          </div>
          <div className="text-xl font-bold truncate" title={coinStrategy}>
            {coinStrategy}
          </div>
          <div className="text-xs text-muted-foreground mt-1">
            8종목 화이트리스트
          </div>
        </div>

        {/* 3. 오늘 매매 */}
        <div>
          <div className="text-xs font-medium text-muted-foreground mb-1">📊 오늘 매매</div>
          <div className="text-xl font-bold">{todayTradeCount}건</div>
          <div className={cn(
            "text-xs mt-1",
            todayPnl > 0 && "text-success",
            todayPnl < 0 && "text-destructive",
            todayPnl === 0 && "text-muted-foreground",
          )}>
            {todayPnl !== 0 ? `누적 ${todayPnl > 0 ? "+" : ""}${todayPnl.toFixed(2)}%` : "체결 대기"}
          </div>
        </div>

        {/* 4. 다음 이벤트 */}
        <div>
          <div className="text-xs font-medium text-muted-foreground mb-1">⏰ 다음 EOD</div>
          <div className="text-xl font-bold">
            <NextEodCountdown />
          </div>
          <div className="text-xs text-muted-foreground mt-1">
            코인 자정 / 미국 NY 15:50
          </div>
        </div>
      </div>
    </Card>
  );
}

function NextEodCountdown() {
  // KST 자정까지 남은 시간 (코인 EOD)
  const now = new Date();
  const kstNow = new Date(now.getTime() + (now.getTimezoneOffset() * 60000) + (9 * 3600000));
  const tomorrow = new Date(kstNow);
  tomorrow.setHours(24, 0, 0, 0);
  const diffMs = tomorrow.getTime() - kstNow.getTime();
  const hours = Math.floor(diffMs / 3600000);
  const minutes = Math.floor((diffMs % 3600000) / 60000);
  return <span>{hours}h {minutes}m</span>;
}
