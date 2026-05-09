import { useEffect, useState, useCallback } from "react";
import { Link } from "react-router-dom";
import { getBalance, getPositions, getBalanceHistory } from "../api/balance";
import client from "../api/client";
import { getCurrentMarket } from "../api/market";
import { getTrades } from "../api/trades";
import type { BalanceResponse, PositionsResponse, BalanceHistory } from "../types/balance";
import type { MarketSnapshot } from "../types/market";
import type { Trade } from "../types/trades";
import StatCard from "../components/StatCard";
import BotStatusBanner from "../components/BotStatusBanner";
import PnLHero from "../components/PnLHero";
import TradeRow from "../components/TradeRow";
import { formatKRW, formatPercent, formatDateTime } from "../utils/format";
import { cn } from "@/lib/utils";
import { getMarketStateKR } from "../utils/indicatorDescriptions";

interface LLMDecision {
  id: number;
  timestamp: string;
  output_market_state: string;
  output_reasoning: string;
  cost_usd: number;
}

export default function DashboardPage() {
  const [balance, setBalance] = useState<BalanceResponse | null>(null);
  const [positions, setPositions] = useState<PositionsResponse | null>(null);
  const [, setHistory] = useState<BalanceHistory[]>([]);
  const [market, setMarket] = useState<MarketSnapshot | null>(null);
  const [recentTrades, setRecentTrades] = useState<Trade[]>([]);
  const [monitoredCoins, setMonitoredCoins] = useState<any[]>([]);
  const [newsStats, setNewsStats] = useState<any>(null);
  const [recentNews, setRecentNews] = useState<any[]>([]);
  const [llmDecisions, setLlmDecisions] = useState<LLMDecision[]>([]);
  const [llmTab, setLlmTab] = useState(0);
  const [visitStats, setVisitStats] = useState<any>(null);  // #240
  const [marketStats, setMarketStats] = useState<any>(null);  // #254 6단계
  const [marketCapital, setMarketCapital] = useState<any>(null);  // #277
  const [marketUniverse, setMarketUniverse] = useState<any>(null);  // #278
  const [tab, setTab] = useState<"all" | "coin" | "kis">("all");  // #287 탭
  const [kisSymbols, setKisSymbols] = useState<any[]>([]);  // #297
  const [kisEvals, setKisEvals] = useState<any[]>([]);  // #297-2
  const [loading, setLoading] = useState(true);

  const fetchAll = useCallback(() => {
    Promise.all([
      getBalance().catch(() => null),
      getPositions().catch(() => null),
      getBalanceHistory(30).catch(() => []),
      getCurrentMarket().catch(() => null),
      getTrades({ limit: 10 }).catch(() => ({ items: [] })),
      client.get("/market/coins").then((r) => r.data).catch(() => []),
      client.get("/news/stats", { params: { hours: 24 } }).then((r) => r.data).catch(() => null),
      client.get("/news?limit=6&sort=latest").then((r) => r.data?.items || r.data || []).catch(() => []),
      client.get("/llm/decisions?limit=6").then((r) => r.data).catch(() => []),
      client.get("/visits/stats?days=30").then((r) => r.data).catch(() => null),
      client.get("/market-stats").then((r) => r.data).catch(() => null),
      client.get("/market-capital/status").then((r) => r.data).catch(() => null),
      client.get("/market-universe").then((r) => r.data).catch(() => null),
      client.get("/kis-symbols").then((r) => r.data).catch(() => []),
      client.get("/kis-symbols/evaluations?limit=20").then((r) => r.data).catch(() => []),
    ]).then(([bal, pos, hist, mkt, trades, coins, nStats, news, llm, vs, ms, mc, mu, ks, kev]) => {
      setBalance(bal);
      setPositions(pos as PositionsResponse | null);
      setHistory(hist as BalanceHistory[]);
      setMarket(mkt as MarketSnapshot | null);
      setRecentTrades((trades as { items: Trade[] }).items);
      setMonitoredCoins(coins as any[]);
      setNewsStats(nStats);
      setRecentNews(news as any[]);
      setLlmDecisions(llm as LLMDecision[]);
      setVisitStats(vs);
      setMarketStats(ms);
      setMarketCapital(mc);
      setMarketUniverse(mu);
      setKisSymbols((ks as any[]) || []);
      setKisEvals((kev as any[]) || []);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 60000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  if (loading) return <div className="loading">로딩 중...</div>;

  const fg = newsStats?.fear_greed;

  return (
    <div>
      <div className="page-header flex justify-between items-center">
        <div>
          <h1>대시보드</h1>
          <p className="text-muted-foreground">전체 현황 요약 (60초 자동 갱신)</p>
        </div>
        <button
          onClick={fetchAll}
          className="px-4 py-2 rounded-md bg-primary text-primary-foreground text-sm font-medium hover:opacity-90 transition-opacity"
        >
          새로고침
        </button>
      </div>

      {/* #333 봇 상태 배너 — 한눈에 봇 상황 파악 */}
      <BotStatusBanner
        marketCapital={marketCapital}
        marketUniverse={marketUniverse}
        recentTrades={recentTrades}
      />

      {/* #287 탭: 전체 / 코인 / KIS */}
      <div style={{ display: "flex", gap: 4, marginBottom: 16, borderBottom: "1px solid var(--border)" }}>
        {([
          { id: "all", label: "전체" },
          { id: "coin", label: "🪙 코인 (Upbit)" },
          { id: "kis", label: "📈 KIS 주식" },
        ] as const).map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            style={{
              padding: "8px 16px", border: "none", cursor: "pointer", fontSize: 13,
              fontWeight: tab === t.id ? 700 : 400,
              borderBottom: tab === t.id ? "2px solid #4a9eff" : "2px solid transparent",
              background: "transparent",
              color: tab === t.id ? "#4a9eff" : "var(--text-secondary)",
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* === 코인 섹션 === */}
      {(tab === "all" || tab === "coin") && <>
      {/* KPI Cards */}
      {(() => {
        const pos = positions?.positions || [];
        const totalCost = pos.reduce((s: number, p: any) => s + (p.total_krw || 0), 0);
        const totalValue = pos.reduce((s: number, p: any) => s + (p.amount || 0) * (p.current_price || 0), 0);
        const totalAsset = (balance?.krw_balance || 0) + totalValue;
        // #218 + #307: 누적 입금 (capital_deposits 합산, 출금 -감산). 0이면 손익 계산 불가.
        const totalDeposits = balance?.total_deposits_krw && balance.total_deposits_krw > 0
          ? balance.total_deposits_krw
          : null;
        const totalPnl = totalDeposits !== null ? totalAsset - totalDeposits : null;
        const pnlPct = totalDeposits && totalDeposits > 0 && totalPnl !== null ? (totalPnl / totalDeposits) * 100 : null;
        return (
          <>
            {/* #335 PnL Hero — 코인 손익 큰 강조 */}
            <PnLHero
              totalAsset={balance ? totalAsset : null}
              totalPnl={totalPnl}
              pnlPct={pnlPct}
              totalDeposits={totalDeposits}
              positionCount={pos.length}
            />
            {/* 기존 KPI 그리드 — 매수/평가 정보 */}
            <div className="kpi-grid mt-4">
              <StatCard
                label="매수 금액"
                value={formatKRW(totalCost)}
                sub={pos.length > 0 ? `${pos.length}종목 보유` : "보유 없음"}
              />
              <StatCard
                label="평가 금액"
                value={formatKRW(totalValue)}
                valueClass={totalValue > totalCost ? "positive" : totalValue < totalCost ? "negative" : ""}
                sub={totalCost > 0 ? `${formatPercent((totalValue - totalCost) / totalCost * 100)} 수익률` : "포지션 없음"}
              />
              <StatCard
                label="현금 (KRW)"
                value={formatKRW(balance?.krw_balance || 0)}
                sub="다음 매수 가용"
              />
              <StatCard
                label="총 보유 자산"
                value={balance ? formatKRW(totalAsset) : "-"}
                sub="KRW + 코인 평가"
              />
            </div>
          </>
        );
      })()}

      {/* 시장 현황 (넓게) + 최근 매매 */}
      <div className="grid-2">
        {/* 시장 현황 — Tailwind 마이그레이션 (#339) */}
        <div className="card" style={{ gridColumn: market && !recentTrades.length ? "1 / -1" : undefined }}>
          <div className="card-title">시장 현황</div>
          <div className="grid grid-cols-3 gap-3 mb-4">
            <div>
              <div className="text-xs text-muted-foreground">BTC</div>
              <div className="text-lg font-semibold">{market ? formatKRW(market.price) : "-"}</div>
              {market && (
                <div className={cn(
                  "text-xs font-medium",
                  market.change_pct_24h >= 0 ? "text-success" : "text-destructive"
                )}>
                  {formatPercent(market.change_pct_24h)} (24h)
                </div>
              )}
            </div>
            <div>
              <div className="text-xs text-muted-foreground">공포/탐욕</div>
              <div className={cn(
                "text-lg font-semibold",
                fg && fg.value <= 25 && "text-destructive",
                fg && fg.value >= 75 && "text-success",
              )}>
                {fg ? fg.value : "-"}
              </div>
              <div className="text-xs text-muted-foreground">
                {fg ? (fg.classification === "Extreme Fear" ? "극도 공포" : fg.classification === "Fear" ? "공포" : fg.classification === "Neutral" ? "중립" : fg.classification === "Greed" ? "탐욕" : "극도 탐욕") : ""}
              </div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">시장 심리</div>
              {newsStats && (
                <>
                  <div className={cn(
                    "text-sm font-semibold",
                    newsStats.negative > newsStats.positive && "text-destructive",
                    newsStats.positive > newsStats.negative && "text-success",
                  )}>
                    {newsStats.negative > newsStats.positive ? "부정적" : newsStats.positive > newsStats.negative ? "긍정적" : "중립"}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    긍정 {newsStats.positive} / 부정 {newsStats.negative}
                  </div>
                </>
              )}
            </div>
          </div>

          {/* LLM 시장 요약 */}
          {llmDecisions.length > 0 && (
            <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <div style={{ fontSize: 13, fontWeight: 600 }}>
                  AI 시장 분석
                  {llmDecisions[llmTab] && (
                    <span style={{ fontSize: 11, fontWeight: 400, color: "var(--text-muted)", marginLeft: 8 }}>
                      {formatDateTime(llmDecisions[llmTab].timestamp)} · {getMarketStateKR(llmDecisions[llmTab].output_market_state || "")}
                    </span>
                  )}
                </div>
                <div style={{ display: "flex", gap: 2 }}>
                  {llmDecisions.map((_, i) => (
                    <button
                      key={i}
                      onClick={() => setLlmTab(i)}
                      style={{
                        width: 24, height: 24, borderRadius: 4, border: "none", cursor: "pointer", fontSize: 11,
                        background: llmTab === i ? "#4a9eff" : "#2a2d3e",
                        color: llmTab === i ? "#fff" : "#8b8fa3",
                      }}
                    >
                      {i + 1}
                    </button>
                  ))}
                </div>
              </div>
              {(() => {
                const d = llmDecisions[llmTab];
                if (!d) return null;
                const parts = (d.output_reasoning || "").split("\n\n");
                const summary = parts[0] || "";
                const reasoning = parts[1] || "";
                return (
                  <div>
                    <div style={{ fontSize: 13, lineHeight: 1.6, marginBottom: 8 }}>{summary}</div>
                    {reasoning && <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>{reasoning}</div>}
                  </div>
                );
              })()}
            </div>
          )}
        </div>

        {/* 최근 매매 */}
        <div className="card">
          <div className="card-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>최근 매매</span>
            <Link to="/trades" style={{ fontSize: 12 }}>전체 보기</Link>
          </div>
          {recentTrades.length > 0 ? (
            <div className="flex flex-col gap-1">
              {recentTrades.slice(0, 10).map((t: any) => (
                <TradeRow key={t.id} trade={t} />
              ))}
            </div>
          ) : (
            <div className="empty-state">매매 내역 없음</div>
          )}
        </div>
      </div>

      {/* 현재 포지션 */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div className="card-title">현재 포지션</div>
        {positions?.has_position && positions.positions?.length > 0 ? (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))", gap: 12 }}>
            {positions.positions.map((p) => (
              <div key={p.id} style={{ padding: 12, borderRadius: 8, background: "var(--bg-secondary)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <span style={{ fontWeight: 600, fontSize: 15 }}>{p.coin?.replace("KRW-", "")}</span>
                    {p.strategy && <span className="badge badge-purple" style={{ fontSize: 9 }}>{p.strategy}</span>}
                  </div>
                  <span className={p.unrealized_pnl_pct >= 0 ? "positive" : "negative"} style={{ fontWeight: 600 }}>
                    {formatPercent(p.unrealized_pnl_pct)}
                  </span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4, fontSize: 12 }}>
                  <div><span style={{ color: "var(--text-muted)" }}>투자 </span>{formatKRW(p.total_krw)}</div>
                  <div><span style={{ color: "var(--text-muted)" }}>현재 </span>{formatKRW(p.amount * p.current_price)}</div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="empty-state">보유 포지션 없음</div>
        )}
      </div>

      {/* 최근 뉴스 */}
      {recentNews.length > 0 && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div className="card-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>최근 뉴스</span>
            <Link to="/news" style={{ fontSize: 12 }}>전체 보기</Link>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {recentNews.map((n: any, i: number) => (
              <div key={n.id || i} style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", padding: "8px 0", borderBottom: i < recentNews.length - 1 ? "1px solid var(--border)" : "none" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
                    <span className={`badge ${n.sentiment === "positive" ? "badge-green" : n.sentiment === "negative" ? "badge-red" : "badge-yellow"}`} style={{ fontSize: 9 }}>
                      {n.sentiment === "positive" ? "긍정" : n.sentiment === "negative" ? "부정" : "중립"}
                    </span>
                    <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{n.source}</span>
                  </div>
                  <a href={n.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 13, color: "var(--text-primary)", textDecoration: "none", display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {n.title}
                  </a>
                </div>
                <span style={{ fontSize: 10, color: "var(--text-muted)", whiteSpace: "nowrap", marginLeft: 12 }}>
                  {formatDateTime(n.published_at || n.collected_at).replace(/\d{4}\. /, "")}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 모니터링 코인 현황 (맨 아래) */}
      {monitoredCoins.length > 0 && (
        <div className="card">
          <div className="card-title">모니터링 코인 현황</div>
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>코인</th>
                  <th>현재가</th>
                  <th>전략</th>
                  <th>최근 신호</th>
                  <th>시장</th>
                  <th>보유</th>
                </tr>
              </thead>
              <tbody>
                {monitoredCoins.map((c: any) => (
                  <tr key={c.coin}>
                    <td style={{ fontWeight: 600 }}>{c.coin?.replace("KRW-", "")}</td>
                    <td>{formatKRW(c.current_price || 0)}</td>
                    <td><span className="badge badge-purple" style={{ fontSize: 10 }}>{c.strategy}</span></td>
                    <td>
                      <span className={`badge ${c.signal_type === "buy" ? "badge-green" : c.signal_type === "sell" ? "badge-red" : "badge-yellow"}`}>
                        {c.signal_type === "buy" ? "매수" : c.signal_type === "sell" ? "매도" : "HOLD"}
                      </span>
                    </td>
                    <td>
                      <span className={`badge ${c.market_state === "bullish" ? "badge-green" : c.market_state === "bearish" ? "badge-red" : "badge-yellow"}`}>
                        {getMarketStateKR(c.market_state || "")}
                      </span>
                    </td>
                    <td>{c.holding ? <span className="badge badge-green">보유중</span> : "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 코인 섹션 끝 */}
      </>}

      {/* === 시장별 모니터링 카드 (#340 Tailwind) === */}
      {marketUniverse?.markets?.length > 0 && (
        <div className="card mt-4">
          <div className="card-title">
            {tab === "coin" ? "코인 전략" : tab === "kis" ? "KIS 주식 전략" : "시장별 모니터링 + 전략"}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {marketUniverse.markets.filter((m: any) => {
              if (tab === "coin") return m.market === "upbit";
              if (tab === "kis") return m.market === "kis_kr" || m.market === "kis_us";
              return true;
            }).map((m: any) => (
              <div key={m.market} className="border border-border rounded-lg p-3.5 bg-card">
                <div className="text-sm font-bold mb-2">{m.display_name}</div>
                <div className="text-xs text-muted-foreground mb-1.5">
                  <strong className="text-foreground">전략:</strong> {m.strategy.display_name}
                </div>
                <div className="text-xs text-muted-foreground mb-2.5 leading-relaxed">
                  {m.rules.description}
                  <br />
                  <span className={cn(
                    m.rules.type === "strategy_module" ? "text-success" : "text-warning"
                  )}>
                    {m.rules.type === "strategy_module" ? "✅ 자동 매수+매도" : "⚠️ 매도만 자동 (매수 수동)"}
                  </span>
                </div>
                <div className="text-xs text-muted-foreground mb-1.5">
                  <strong className="text-foreground">모니터링 ({m.symbol_count}종목):</strong>
                </div>
                <div className="flex flex-wrap gap-1">
                  {m.symbols.map((s: string) => (
                    <span key={s} className="text-[10px] px-1.5 py-0.5 rounded bg-muted border border-border">
                      {s.replace("KRW-", "")}
                    </span>
                  ))}
                </div>
                {m.strategy.params_json && (
                  <details className="mt-2">
                    <summary className="text-xs text-muted-foreground cursor-pointer">전략 파라미터</summary>
                    <pre className="text-[10px] bg-muted p-1.5 rounded mt-1 overflow-auto">
                      {JSON.stringify(JSON.parse(m.strategy.params_json), null, 2)}
                    </pre>
                  </details>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* === KIS 자본 카드 (#343 Tailwind) === */}
      {(tab === "all" || tab === "kis") && marketCapital?.markets?.length > 0 && (
        <div className="card mt-4">
          <div className="card-title flex justify-between items-center">
            <span>KIS 시장별 자본</span>
            <div className="flex gap-2">
              <button
                onClick={async () => {
                  const a = prompt("입금 금액 (KRW). 50:50 자동 분배.");
                  if (!a) return;
                  const note = prompt("메모 (선택)") || "";
                  await client.post("/market-capital/deposit", { amount: Number(a), split: true, note });
                  fetchAll();
                }}
                className="px-3 py-1.5 text-xs rounded-md border border-border bg-background hover:bg-accent transition-colors"
              >
                💰 입금 (50:50)
              </button>
              <button
                onClick={async () => {
                  const from = prompt("이동 출처 (kis_kr | kis_us)");
                  if (!from) return;
                  const to = prompt("이동 대상 (kis_kr | kis_us)");
                  if (!to) return;
                  const a = prompt("금액 (KRW)");
                  if (!a) return;
                  const note = prompt("메모 (선택)") || "";
                  await client.post("/market-capital/transfer", { from_market: from, to_market: to, amount: Number(a), note });
                  fetchAll();
                }}
                className="px-3 py-1.5 text-xs rounded-md border border-border bg-background hover:bg-accent transition-colors"
              >
                ↔️ 이동
              </button>
              <button
                onClick={async () => {
                  const market = prompt("출금 시장 (kis_kr | kis_us)");
                  if (!market) return;
                  const a = prompt("금액 (KRW)");
                  if (!a) return;
                  const note = prompt("메모 (선택)") || "";
                  await client.post("/market-capital/withdraw", { market, amount: Number(a), note });
                  fetchAll();
                }}
                className="px-3 py-1.5 text-xs rounded-md border border-border bg-background hover:bg-accent transition-colors"
              >
                💸 출금
              </button>
            </div>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr>
                <th className="text-left p-1.5">시장</th>
                <th className="text-right p-1.5">실 잔고 (API)</th>
                <th className="text-right p-1.5">실현 손익</th>
                <th className="text-right p-1.5">보유 원가</th>
                <th className="text-left p-1.5">봇 상태</th>
              </tr>
            </thead>
            <tbody>
              {marketCapital.markets.map((m: any) => {
                const tradingEnabled = m.market === "kis_us";
                return (
                  <tr key={m.market} className={cn(!tradingEnabled && "opacity-60")}>
                    <td className="p-1.5 font-semibold">
                      {m.market === "kis_kr" ? "🇰🇷 한국주식" : "🇺🇸 미국주식"}
                    </td>
                    <td className="text-right p-1.5 font-bold text-primary">
                      {m.live?.available != null
                        ? `${Number(m.live.available).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${m.live.currency || ""}`
                        : <span className="text-muted-foreground font-normal">조회불가</span>}
                    </td>
                    <td className={cn(
                      "text-right p-1.5",
                      m.realized_pnl > 0 && "text-success",
                      m.realized_pnl < 0 && "text-destructive",
                    )}>
                      {m.realized_pnl !== 0
                        ? `${m.realized_pnl > 0 ? "+" : ""}${Number(m.realized_pnl).toLocaleString()}원`
                        : <span className="text-muted-foreground">-</span>}
                    </td>
                    <td className="text-right p-1.5">
                      {m.held_cost > 0
                        ? `${Number(m.held_cost).toLocaleString()}원`
                        : <span className="text-muted-foreground">-</span>}
                    </td>
                    <td className="p-1.5 text-xs">
                      {tradingEnabled
                        ? <span className="text-success">✅ 봇 거래 활성</span>
                        : <span className="text-muted-foreground">⏸️ 거래 OFF</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="mt-2 text-xs text-muted-foreground">
            💡 <strong>실 잔고 (API)</strong>가 봇이 매수에 쓰는 예산. 입출금 이력은 위 버튼으로 기록.
          </div>
        </div>
      )}

      {/* #341 KIS 봇 매수 판단 표 — Tailwind 변환 */}
      {(tab === "all" || tab === "kis") && kisEvals.length > 0 && (
        <div className="card mt-4">
          <div className="card-title flex justify-between items-center">
            <span>🔍 봇 매수 판단 (최근 20건)</span>
            <span className="text-xs text-muted-foreground font-normal">
              30초마다 평가 — 매수 신호 충족하면 ✅
            </span>
          </div>
          <div className="table-container mt-2">
            <table className="w-full text-xs">
              <thead>
                <tr>
                  <th className="text-left p-1">시간</th>
                  <th className="text-left p-1">종목</th>
                  <th className="text-right p-1">가격</th>
                  <th className="text-right p-1">RSI</th>
                  <th className="text-right p-1">MA20</th>
                  <th className="text-right p-1">MA60</th>
                  <th className="text-center p-1">매수?</th>
                  <th className="text-left p-1">사유 / 신뢰도</th>
                </tr>
              </thead>
              <tbody>
                {kisEvals.slice(0, 20).map((e: any) => (
                  <tr key={e.id}>
                    <td className="p-1 text-[10px] text-muted-foreground">
                      {(e.evaluated_at || "").slice(11, 19)}
                    </td>
                    <td className="p-1 font-semibold">{e.ticker}</td>
                    <td className="p-1 text-right">
                      {e.price != null ? `$${Number(e.price).toFixed(2)}` : "-"}
                    </td>
                    <td className={cn(
                      "p-1 text-right",
                      e.rsi != null && e.rsi <= 35 && "text-success font-bold",
                      e.rsi != null && e.rsi >= 70 && "text-destructive font-bold",
                      e.rsi != null && e.rsi > 35 && e.rsi < 70 && "text-muted-foreground",
                      e.rsi == null && "text-muted-foreground",
                    )}>
                      {e.rsi != null ? Number(e.rsi).toFixed(1) : "-"}
                    </td>
                    <td className="p-1 text-right text-muted-foreground">
                      {e.ma20 != null ? Number(e.ma20).toFixed(2) : "-"}
                    </td>
                    <td className="p-1 text-right text-muted-foreground">
                      {e.ma60 != null ? Number(e.ma60).toFixed(2) : "-"}
                    </td>
                    <td className="p-1 text-center">
                      {e.should_buy
                        ? <span className="text-success font-bold">✅</span>
                        : <span className="text-muted-foreground">-</span>}
                    </td>
                    <td className="p-1 text-[10px] text-muted-foreground">
                      {e.reason}
                      {e.confidence > 0 ? ` (conf ${Number(e.confidence).toFixed(2)})` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-1.5 text-xs text-muted-foreground">
            💡 RSI 초록(≤35) = 매수 임계 충족. 4중 조건 모두 만족해야 ✅. 미충족 사유는 "사유" 컬럼에 표시.
          </div>
        </div>
      )}

      {/* #342 KIS 종목 풀 — Tailwind 변환 */}
      {(tab === "all" || tab === "kis") && kisSymbols.length > 0 && (
        <div className="card mt-4">
          <div className="card-title flex justify-between items-center">
            <span>🇺🇸 KIS 미국주식 종목 풀</span>
            <span className="text-xs text-muted-foreground font-normal">
              활성 {kisSymbols.filter((s: any) => s.enabled).length}/{kisSymbols.length}종목 — 봇 5분마다 풀 재로딩 (재시작 불요)
            </span>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-1.5 mt-2">
            {kisSymbols.map((s: any) => {
              const supported = s.minute_supported !== false;
              const disabled = !supported;
              return (
                <label
                  key={s.ticker}
                  title={disabled ? "KIS 분봉 미지원 — 활성화해도 봇이 평가 못함" : ""}
                  className={cn(
                    "flex items-center gap-2 px-2.5 py-1.5 rounded-md text-xs transition-colors",
                    disabled
                      ? "bg-muted border border-dashed border-border opacity-40 cursor-not-allowed"
                      : s.enabled
                        ? "bg-primary/10 border border-primary cursor-pointer"
                        : "bg-muted border border-border cursor-pointer hover:bg-accent"
                  )}
                >
                  <input
                    type="checkbox"
                    checked={!!s.enabled}
                    disabled={disabled}
                    onChange={async (e) => {
                      if (disabled) return;
                      await client.post("/kis-symbols/toggle", { ticker: s.ticker, enabled: e.target.checked });
                      fetchAll();
                    }}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="font-semibold flex items-center gap-1 flex-wrap">
                      {s.ticker}
                      {s.is_integer_only && <span className="text-[9px] text-warning">1주</span>}
                      {disabled && <span className="text-[9px] text-destructive">분봉 미지원</span>}
                    </div>
                    <div className="text-[10px] text-muted-foreground truncate">
                      {s.display_name}{s.note ? ` — ${s.note}` : ""}
                    </div>
                  </div>
                </label>
              );
            })}
          </div>
          <div className="mt-2 text-xs text-muted-foreground">
            💡 체크하면 봇 모니터링 풀에 추가. 종목당 한도 100% (자금 부족 시 신뢰도 높은 것 우선).
            <br />
            ⚠️ <span className="text-destructive">분봉 미지원</span> 종목은 KIS API 제약으로 봇이 평가 불가 (활성화 X).
          </div>
        </div>
      )}

      {/* #254 6단계: 시장별 PnL */}
      {marketStats?.markets?.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-title">시장별 PnL</div>
          <table style={{ width: "100%", fontSize: 13 }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: 6 }}>시장</th>
                <th style={{ textAlign: "right", padding: 6 }}>매수</th>
                <th style={{ textAlign: "right", padding: 6 }}>매도</th>
                <th style={{ textAlign: "right", padding: 6 }}>승률</th>
                <th style={{ textAlign: "right", padding: 6 }}>평균 수익</th>
                <th style={{ textAlign: "right", padding: 6 }}>실현 PnL</th>
              </tr>
            </thead>
            <tbody>
              {marketStats.markets.map((m: any) => (
                <tr key={m.market}>
                  <td style={{ padding: 6, fontWeight: 600 }}>
                    {m.market === "upbit" ? "🪙 코인" : m.market === "kis_kr" ? "🇰🇷 한국주식" : m.market === "kis_us" ? "🇺🇸 미국주식" : m.market}
                  </td>
                  <td style={{ textAlign: "right", padding: 6 }}>{m.buys}</td>
                  <td style={{ textAlign: "right", padding: 6 }}>{m.sells}</td>
                  <td style={{ textAlign: "right", padding: 6 }}>{m.win_rate}%</td>
                  <td style={{ textAlign: "right", padding: 6 }} className={m.avg_profit_pct >= 0 ? "positive" : "negative"}>
                    {m.avg_profit_pct >= 0 ? "+" : ""}{m.avg_profit_pct}%
                  </td>
                  <td style={{ textAlign: "right", padding: 6, fontWeight: 600 }} className={m.total_pnl_krw >= 0 ? "positive" : "negative"}>
                    {m.total_pnl_krw >= 0 ? "+" : ""}{Number(m.total_pnl_krw).toLocaleString()}원
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* #240: 방문자 통계 */}
      {visitStats && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-title">방문자 통계</div>
          <div className="kpi-grid">
            <StatCard label="오늘 PV" value={`${visitStats.today?.pv ?? 0}`} sub={`UV ${visitStats.today?.uv ?? 0}`} />
            <StatCard label="어제 PV" value={`${visitStats.yesterday?.pv ?? 0}`} sub={`UV ${visitStats.yesterday?.uv ?? 0}`} />
            <StatCard label="최근 7일 PV" value={`${visitStats.last_7_days?.pv ?? 0}`} sub={`UV ${visitStats.last_7_days?.uv ?? 0}`} />
            <StatCard label="누적 PV" value={`${visitStats.total?.pv ?? 0}`} sub={`UV ${visitStats.total?.uv ?? 0}`} />
          </div>
          {visitStats.daily?.length > 1 && (
            <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-muted)" }}>
              일별: {visitStats.daily.slice(-14).map((d: any) => `${d.date.slice(5)}:${d.pv}`).join(" · ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
