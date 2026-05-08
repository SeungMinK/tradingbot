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
import { formatKRW, formatPercent, formatDateTime } from "../utils/format";
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
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h1>대시보드</h1>
          <p>전체 현황 요약 (60초 자동 갱신)</p>
        </div>
        <button onClick={fetchAll} style={{ padding: "8px 16px", borderRadius: 8, border: "none", background: "#4a9eff", color: "#fff", cursor: "pointer", fontSize: 13 }}>
          새로고침
        </button>
      </div>

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
          <div className="kpi-grid">
            <StatCard
              label="총 보유 자산 (코인 봇)"
              value={balance ? formatKRW(totalAsset) : "-"}
              sub={`KRW ${formatKRW(balance?.krw_balance || 0)} + 코인 ${formatKRW(totalValue)}`}
            />
            <StatCard
              label="총 손익 (코인)"
              value={totalPnl !== null && pnlPct !== null
                ? `${formatKRW(totalPnl)} (${formatPercent(pnlPct)})`
                : "기준 미설정"}
              valueClass={totalPnl !== null ? (totalPnl >= 0 ? "positive" : "negative") : ""}
              sub={totalDeposits !== null
                ? `누적 입금 ₩${totalDeposits.toLocaleString()} 기준`
                : "입금 이력 없음"}
            />
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
          </div>
        );
      })()}

      {/* 시장 현황 (넓게) + 최근 매매 */}
      <div className="grid-2">
        {/* 시장 현황 — 넓게 */}
        <div className="card" style={{ gridColumn: market && !recentTrades.length ? "1 / -1" : undefined }}>
          <div className="card-title">시장 현황</div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, marginBottom: 16 }}>
            <div>
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>BTC</div>
              <div style={{ fontSize: 18, fontWeight: 600 }}>{market ? formatKRW(market.price) : "-"}</div>
              {market && (
                <div className={market.change_pct_24h >= 0 ? "positive" : "negative"} style={{ fontSize: 12 }}>
                  {formatPercent(market.change_pct_24h)} (24h)
                </div>
              )}
            </div>
            <div>
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>공포/탐욕</div>
              <div style={{ fontSize: 18, fontWeight: 600, color: fg && fg.value <= 25 ? "#ef4444" : fg && fg.value >= 75 ? "#22c55e" : "var(--text-primary)" }}>
                {fg ? fg.value : "-"}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                {fg ? (fg.classification === "Extreme Fear" ? "극도 공포" : fg.classification === "Fear" ? "공포" : fg.classification === "Neutral" ? "중립" : fg.classification === "Greed" ? "탐욕" : "극도 탐욕") : ""}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>시장 심리</div>
              {newsStats && (
                <>
                  <div style={{ fontSize: 14, fontWeight: 600 }} className={newsStats.negative > newsStats.positive ? "negative" : newsStats.positive > newsStats.negative ? "positive" : ""}>
                    {newsStats.negative > newsStats.positive ? "부정적" : newsStats.positive > newsStats.negative ? "긍정적" : "중립"}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
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
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>시간(KST)</th>
                    <th>종목</th>
                    <th>방향</th>
                    <th>금액</th>
                    <th>사유</th>
                  </tr>
                </thead>
                <tbody>
                  {recentTrades.map((t) => (
                    <tr key={t.id}>
                      <td style={{ fontSize: 10 }}>{formatDateTime(t.timestamp).replace(/\d{4}\. /, "").replace(/:(\d{2})$/, "")}</td>
                      <td style={{ fontSize: 12 }}>{t.coin?.replace("KRW-", "")}</td>
                      <td>
                        <span className={`badge ${t.side === "buy" ? "badge-green" : "badge-red"}`} style={{ fontSize: 10 }}>
                          {t.side === "buy" ? "매수" : "매도"}
                        </span>
                      </td>
                      <td style={{ fontSize: 11 }}>{formatKRW(t.total_krw)}</td>
                      <td style={{ fontSize: 10, color: "var(--text-muted)", maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {t.trigger_reason || "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
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

      {/* === KIS/공통: 시장별 모니터링 카드 (탭별 필터) === */}
      {marketUniverse?.markets?.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-title">
            {tab === "coin" ? "코인 전략" : tab === "kis" ? "KIS 주식 전략" : "시장별 모니터링 + 전략"}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 12 }}>
            {marketUniverse.markets.filter((m: any) => {
              if (tab === "coin") return m.market === "upbit";
              if (tab === "kis") return m.market === "kis_kr" || m.market === "kis_us";
              return true;
            }).map((m: any) => (
              <div key={m.market} style={{ border: "1px solid var(--border)", borderRadius: 10, padding: 14 }}>
                <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>{m.display_name}</div>
                <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 6 }}>
                  <strong>전략:</strong> {m.strategy.display_name}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 10, lineHeight: 1.5 }}>
                  {m.rules.description}
                  <br />
                  <span style={{ color: m.rules.type === "strategy_module" ? "#10b981" : "#f59e0b" }}>
                    {m.rules.type === "strategy_module" ? "✅ 자동 매수+매도" : "⚠️ 매도만 자동 (매수 수동)"}
                  </span>
                </div>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 6 }}>
                  <strong>모니터링 ({m.symbol_count}종목):</strong>
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {m.symbols.map((s: string) => (
                    <span key={s} style={{
                      fontSize: 10, padding: "2px 6px", borderRadius: 4,
                      background: "#f1f5f9", border: "1px solid var(--border)",
                    }}>
                      {s.replace("KRW-", "")}
                    </span>
                  ))}
                </div>
                {m.strategy.params_json && (
                  <details style={{ marginTop: 8 }}>
                    <summary style={{ fontSize: 11, cursor: "pointer", color: "var(--text-muted)" }}>전략 파라미터</summary>
                    <pre style={{ fontSize: 10, background: "#f8fafc", padding: 6, borderRadius: 4, marginTop: 4, overflow: "auto" }}>
                      {JSON.stringify(JSON.parse(m.strategy.params_json), null, 2)}
                    </pre>
                  </details>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* === KIS 섹션: 자본 + 단타모드 표시 === */}
      {(tab === "all" || tab === "kis") && marketCapital?.markets?.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>KIS 시장별 자본</span>
            <div style={{ display: "flex", gap: 8 }}>
              <button onClick={async () => {
                const a = prompt("입금 금액 (KRW). 50:50 자동 분배.");
                if (!a) return;
                const note = prompt("메모 (선택)") || "";
                await client.post("/market-capital/deposit", { amount: Number(a), split: true, note });
                fetchAll();
              }} style={{ padding: "6px 12px", fontSize: 12, borderRadius: 6, border: "1px solid var(--border)", cursor: "pointer", background: "#fff" }}>
                💰 입금 (50:50)
              </button>
              <button onClick={async () => {
                const from = prompt("이동 출처 (kis_kr | kis_us)");
                if (!from) return;
                const to = prompt("이동 대상 (kis_kr | kis_us)");
                if (!to) return;
                const a = prompt("금액 (KRW)");
                if (!a) return;
                const note = prompt("메모 (선택)") || "";
                await client.post("/market-capital/transfer", { from_market: from, to_market: to, amount: Number(a), note });
                fetchAll();
              }} style={{ padding: "6px 12px", fontSize: 12, borderRadius: 6, border: "1px solid var(--border)", cursor: "pointer", background: "#fff" }}>
                ↔️ 이동
              </button>
              <button onClick={async () => {
                const market = prompt("출금 시장 (kis_kr | kis_us)");
                if (!market) return;
                const a = prompt("금액 (KRW)");
                if (!a) return;
                const note = prompt("메모 (선택)") || "";
                await client.post("/market-capital/withdraw", { market, amount: Number(a), note });
                fetchAll();
              }} style={{ padding: "6px 12px", fontSize: 12, borderRadius: 6, border: "1px solid var(--border)", cursor: "pointer", background: "#fff" }}>
                💸 출금
              </button>
            </div>
          </div>
          <table style={{ width: "100%", fontSize: 13 }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: 6 }}>시장</th>
                <th style={{ textAlign: "right", padding: 6 }}>실 잔고 (API)</th>
                <th style={{ textAlign: "right", padding: 6 }}>실현 손익</th>
                <th style={{ textAlign: "right", padding: 6 }}>보유 원가</th>
                <th style={{ textAlign: "left", padding: 6 }}>봇 상태</th>
              </tr>
            </thead>
            <tbody>
              {marketCapital.markets.map((m: any) => {
                const tradingEnabled = m.market === "kis_us"; // TODO: market-universe API에서 실제 토글 가져오기
                return (
                  <tr key={m.market} style={{ opacity: tradingEnabled ? 1 : 0.6 }}>
                    <td style={{ padding: 6, fontWeight: 600 }}>
                      {m.market === "kis_kr" ? "🇰🇷 한국주식" : "🇺🇸 미국주식"}
                    </td>
                    <td style={{ textAlign: "right", padding: 6, fontWeight: 700, color: "var(--accent)" }}>
                      {m.live?.available != null
                        ? `${Number(m.live.available).toLocaleString(undefined, { maximumFractionDigits: 2 })} ${m.live.currency || ""}`
                        : <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>조회불가</span>}
                    </td>
                    <td style={{ textAlign: "right", padding: 6 }} className={m.realized_pnl > 0 ? "positive" : m.realized_pnl < 0 ? "negative" : ""}>
                      {m.realized_pnl !== 0
                        ? `${m.realized_pnl > 0 ? "+" : ""}${Number(m.realized_pnl).toLocaleString()}원`
                        : <span style={{ color: "var(--text-muted)" }}>-</span>}
                    </td>
                    <td style={{ textAlign: "right", padding: 6 }}>
                      {m.held_cost > 0
                        ? `${Number(m.held_cost).toLocaleString()}원`
                        : <span style={{ color: "var(--text-muted)" }}>-</span>}
                    </td>
                    <td style={{ padding: 6, fontSize: 11 }}>
                      {tradingEnabled
                        ? <span style={{ color: "#10b981" }}>✅ 봇 거래 활성</span>
                        : <span style={{ color: "var(--text-muted)" }}>⏸️ 거래 OFF</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div style={{ marginTop: 8, fontSize: 11, color: "var(--text-muted)" }}>
            💡 <strong>실 잔고 (API)</strong>가 봇이 매수에 쓰는 예산. 입출금 이력은 위 버튼으로 기록.
          </div>
        </div>
      )}

      {/* #297-2: KIS 봇 틱별 매수 판단 결과 (사용자 가시성) */}
      {(tab === "all" || tab === "kis") && kisEvals.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>🔍 봇 매수 판단 (최근 20건)</span>
            <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 400 }}>
              30초마다 평가 — 매수 신호 충족하면 ✅
            </span>
          </div>
          <div className="table-container" style={{ marginTop: 8 }}>
            <table style={{ width: "100%", fontSize: 12 }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left", padding: 4 }}>시간</th>
                  <th style={{ textAlign: "left", padding: 4 }}>종목</th>
                  <th style={{ textAlign: "right", padding: 4 }}>가격</th>
                  <th style={{ textAlign: "right", padding: 4 }}>RSI</th>
                  <th style={{ textAlign: "right", padding: 4 }}>MA20</th>
                  <th style={{ textAlign: "right", padding: 4 }}>MA60</th>
                  <th style={{ textAlign: "center", padding: 4 }}>매수?</th>
                  <th style={{ textAlign: "left", padding: 4 }}>사유 / 신뢰도</th>
                </tr>
              </thead>
              <tbody>
                {kisEvals.slice(0, 20).map((e: any) => (
                  <tr key={e.id}>
                    <td style={{ padding: 4, fontSize: 10, color: "var(--text-muted)" }}>
                      {(e.evaluated_at || "").slice(11, 19)}
                    </td>
                    <td style={{ padding: 4, fontWeight: 600 }}>{e.ticker}</td>
                    <td style={{ padding: 4, textAlign: "right" }}>
                      {e.price != null ? `$${Number(e.price).toFixed(2)}` : "-"}
                    </td>
                    <td style={{
                      padding: 4, textAlign: "right",
                      color: e.rsi != null && e.rsi <= 35 ? "#10b981" : e.rsi != null && e.rsi >= 70 ? "#ef4444" : "var(--text-muted)",
                      fontWeight: e.rsi != null && (e.rsi <= 35 || e.rsi >= 70) ? 700 : 400,
                    }}>
                      {e.rsi != null ? Number(e.rsi).toFixed(1) : "-"}
                    </td>
                    <td style={{ padding: 4, textAlign: "right", color: "var(--text-muted)" }}>
                      {e.ma20 != null ? Number(e.ma20).toFixed(2) : "-"}
                    </td>
                    <td style={{ padding: 4, textAlign: "right", color: "var(--text-muted)" }}>
                      {e.ma60 != null ? Number(e.ma60).toFixed(2) : "-"}
                    </td>
                    <td style={{ padding: 4, textAlign: "center" }}>
                      {e.should_buy
                        ? <span style={{ color: "#10b981", fontWeight: 700 }}>✅</span>
                        : <span style={{ color: "var(--text-muted)" }}>-</span>}
                    </td>
                    <td style={{ padding: 4, fontSize: 10, color: "var(--text-secondary)" }}>
                      {e.reason}
                      {e.confidence > 0 ? ` (conf ${Number(e.confidence).toFixed(2)})` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ marginTop: 6, fontSize: 11, color: "var(--text-muted)" }}>
            💡 RSI 초록(≤35) = 매수 임계 충족. 4중 조건 모두 만족해야 ✅. 미충족 사유는 "사유" 컬럼에 표시.
          </div>
        </div>
      )}

      {/* #297: KIS 미국주식 종목 풀 관리 */}
      {(tab === "all" || tab === "kis") && kisSymbols.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>🇺🇸 KIS 미국주식 종목 풀</span>
            <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 400 }}>
              활성 {kisSymbols.filter((s: any) => s.enabled).length}/{kisSymbols.length}종목 — 봇 5분마다 풀 재로딩 (재시작 불요)
            </span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 6, marginTop: 8 }}>
            {kisSymbols.map((s: any) => {
              const supported = s.minute_supported !== false;
              const disabled = !supported;
              return (
                <label
                  key={s.ticker}
                  title={disabled ? "KIS 분봉 미지원 — 활성화해도 봇이 평가 못함" : ""}
                  style={{
                    display: "flex", alignItems: "center", gap: 8,
                    padding: "6px 10px", borderRadius: 6,
                    background: disabled
                      ? "var(--bg-secondary)"
                      : s.enabled ? "rgba(74,158,255,0.12)" : "var(--bg-secondary)",
                    border: disabled
                      ? "1px dashed var(--border)"
                      : s.enabled ? "1px solid #4a9eff" : "1px solid var(--border)",
                    cursor: disabled ? "not-allowed" : "pointer",
                    fontSize: 12,
                    opacity: disabled ? 0.4 : 1,
                  }}
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
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 600 }}>
                      {s.ticker}
                      {s.is_integer_only ? <span style={{ fontSize: 9, marginLeft: 4, color: "#f59e0b" }}>1주</span> : null}
                      {disabled ? <span style={{ fontSize: 9, marginLeft: 4, color: "#ef4444" }}>분봉 미지원</span> : null}
                    </div>
                    <div style={{ fontSize: 10, color: "var(--text-muted)" }}>
                      {s.display_name}{s.note ? ` — ${s.note}` : ""}
                    </div>
                  </div>
                </label>
              );
            })}
          </div>
          <div style={{ marginTop: 8, fontSize: 11, color: "var(--text-muted)" }}>
            💡 체크하면 봇 모니터링 풀에 추가. 종목당 한도 100% (자금 부족 시 신뢰도 높은 것 우선).
            <br />
            ⚠️ <span style={{ color: "#ef4444" }}>분봉 미지원</span> 종목은 KIS API 제약으로 봇이 평가 불가 (활성화 X).
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
