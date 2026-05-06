import { useEffect, useState, useCallback } from "react";
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
  LineChart, Line, XAxis, YAxis, CartesianGrid, ReferenceLine,
} from "recharts";
import { formatPercent, formatDateTime } from "../utils/format";
import { getMarketStateKR } from "../utils/indicatorDescriptions";

const API = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api";

export default function PublicDashboardPage() {
  const [summary, setSummary] = useState<any>(null);
  const [trades, setTrades] = useState<any[]>([]);
  const [analysis, setAnalysis] = useState<any[]>([]);
  const [news, setNews] = useState<any[]>([]);
  const [fg, setFg] = useState<any>(null);
  const [portfolio, setPortfolio] = useState<any[]>([]);
  const [dailyReturns, setDailyReturns] = useState<any[]>([]);
  const [strategyStats, setStrategyStats] = useState<any[]>([]);
  const [monitoringCoins, setMonitoringCoins] = useState<any[]>([]);
  const [strategies, setStrategies] = useState<any[]>([]);
  // #233: 계좌 손익률 (KRW 비공개) + 일별 추이
  const [accountPnl, setAccountPnl] = useState<any>(null);
  const [pnlHistory, setPnlHistory] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAllTrades, setShowAllTrades] = useState(true);
  const [tradeFilter, setTradeFilter] = useState<string | null>(null);
  const [sideFilter, setSideFilter] = useState<string | null>(null);
  const [showAllDaily, setShowAllDaily] = useState(false);
  const [newsExpanded, setNewsExpanded] = useState(false);
  const [newsIndex, setNewsIndex] = useState(0);
  const [analysisIndex, setAnalysisIndex] = useState(0);
  const [showAllStrategies, setShowAllStrategies] = useState(false);

  const fetchAll = useCallback(() => {
    const base = API.replace(/\/api$/, "");
    Promise.all([
      fetch(`${base}/api/public/summary`).then(r => r.json()).catch(() => null),
      fetch(`${base}/api/public/trades?limit=100`).then(r => r.json()).catch(() => []),
      fetch(`${base}/api/public/analysis?limit=7`).then(r => r.json()).catch(() => []),
      fetch(`${base}/api/public/news?limit=20`).then(r => r.json()).catch(() => ({ news: [], fear_greed: null })),
      fetch(`${base}/api/public/portfolio`).then(r => r.json()).catch(() => ({ positions: [] })),
      fetch(`${base}/api/public/daily-returns?days=14`).then(r => r.json()).catch(() => []),
      fetch(`${base}/api/public/strategy-stats`).then(r => r.json()).catch(() => []),
      fetch(`${base}/api/public/monitoring-coins`).then(r => r.json()).catch(() => []),
      fetch(`${base}/api/public/strategies`).then(r => r.json()).catch(() => []),
      fetch(`${base}/api/public/account-pnl`).then(r => r.json()).catch(() => null),
      fetch(`${base}/api/public/account-pnl-history?days=30`).then(r => r.json()).catch(() => []),
    ]).then(([s, t, a, n, p, dr, ss, mc, st, ap, aph]) => {
      setSummary(s); setTrades(t); setAnalysis(a);
      setNews(n?.news || []); setFg(n?.fear_greed || null);
      setPortfolio(p?.positions || []);
      setDailyReturns(dr); setStrategyStats(ss);
      setMonitoringCoins(mc); setStrategies(st);
      setAccountPnl(ap); setPnlHistory(Array.isArray(aph) ? aph : []);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 60000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  // 뉴스 자동 롤링 (모든 훅은 early return 전에)
  useEffect(() => {
    if (news.length <= 1 || newsExpanded) return;
    const timer = setInterval(() => {
      setNewsIndex((prev) => (prev + 1) % news.length);
    }, 5000);
    return () => clearInterval(timer);
  }, [news.length, newsExpanded]);

  // AI 분석 롤링 (1번 고정, 2~3번 슬롯 순환)
  useEffect(() => {
    if (analysis.length <= 3) return;
    const timer = setInterval(() => {
      setAnalysisIndex((prev) => (prev + 1) % (analysis.length - 1));
    }, 12000);
    return () => clearInterval(timer);
  }, [analysis.length]);

  if (loading) return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "60vh", gap: 16 }}>
      <div style={{ display: "flex", gap: 6 }}>
        {[0, 1, 2].map((i) => (
          <div key={i} style={{
            width: 10, height: 10, borderRadius: "50%", background: "var(--accent-blue)",
            animation: `bounce 1.2s ease-in-out ${i * 0.15}s infinite`,
          }} />
        ))}
      </div>
      <span style={{ color: "var(--text-muted)", fontSize: 14 }}>AI가 시장을 분석하고 있습니다</span>
      <style>{`@keyframes bounce { 0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; } 40% { transform: scale(1); opacity: 1; } }`}</style>
    </div>
  );

  if (!summary && !loading) return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", minHeight: "60vh", gap: 12 }}>
      <div style={{ fontSize: 36, animation: "pulse 2s ease-in-out infinite" }}>📡</div>
      <span style={{ color: "var(--text-secondary)", fontSize: 16, fontWeight: 600 }}>서버와 연결 중입니다</span>
      <span style={{ color: "var(--text-muted)", fontSize: 13 }}>잠시만 기다려주세요 — 곧 실시간 데이터가 표시됩니다</span>
      <style>{`@keyframes pulse { 0%, 100% { transform: scale(1); } 50% { transform: scale(1.15); } }`}</style>
    </div>
  );

  const fgLabel = fg ? (fg.classification === "Extreme Fear" ? "극도 공포" : fg.classification === "Fear" ? "공포" : fg.classification === "Neutral" ? "중립" : fg.classification === "Greed" ? "탐욕" : "극도 탐욕") : "";
  const fgColor = fg && fg.value <= 25 ? "#f87171" : fg && fg.value >= 75 ? "#34d399" : "#fbbf24";


  // #233: 계좌 손익 색상 결정
  const totalPct = accountPnl?.pnl_pct ?? 0;
  const todayPct = accountPnl?.today_pct ?? 0;
  const isPos = totalPct >= 0;
  const accentColor = isPos ? "#34d399" : "#f87171";

  return (
    <div className="public-wrap">
      {/* #233 Hero: 계좌 손익률 + 광고 영역 */}
      <div className="public-hero-grid">
        <div className="pnl-hero">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
            <div>
              <div className="pnl-hero-title">CRYPTOBOT · 누적 손익률</div>
              <div className="pnl-hero-value" style={{ color: accentColor }}>
                {isPos ? "+" : ""}{totalPct.toFixed(2)}%
              </div>
              <div className="pnl-hero-sub">
                오늘 변동 <span style={{ color: todayPct >= 0 ? "#34d399" : "#f87171", fontWeight: 700 }}>
                  {todayPct >= 0 ? "+" : ""}{todayPct.toFixed(2)}%
                </span>
              </div>
            </div>
            {fg && (
              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 11, opacity: 0.6 }}>공포/탐욕</div>
                <div style={{ fontSize: 28, fontWeight: 700, color: fgColor }}>{fg.value}</div>
                <div style={{ fontSize: 11, color: fgColor }}>{fgLabel}</div>
              </div>
            )}
          </div>

          {summary && (
            <div className="pnl-hero-row">
              <div className="pnl-hero-stat">
                전체 승률<strong>{summary.win_rate.toFixed(1)}%</strong>
              </div>
              <div className="pnl-hero-stat">
                평균 수익<strong>{formatPercent(summary.avg_profit_pct)}</strong>
              </div>
              <div className="pnl-hero-stat">
                총 거래<strong>{summary.total_trades}건</strong>
              </div>
              <div className="pnl-hero-stat">
                오늘 거래<strong>{summary.today_trades}건</strong>
              </div>
            </div>
          )}

          {/* 일별 추이 차트 */}
          {pnlHistory.length > 1 && (
            <div style={{ marginTop: 20, height: 120 }}>
              <ResponsiveContainer>
                <LineChart data={pnlHistory} margin={{ top: 5, right: 10, left: -10, bottom: 0 }}>
                  <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
                  <XAxis dataKey="date" tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 10 }} tickFormatter={(v: string) => v.slice(5)} />
                  <YAxis tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 10 }} unit="%" width={40} />
                  <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" strokeDasharray="3 3" />
                  <Tooltip
                    contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8, fontSize: 12 }}
                    labelStyle={{ color: "#94a3b8" }}
                    formatter={(v: any) => [`${v >= 0 ? "+" : ""}${v}%`, "누적 손익"]}
                  />
                  <Line type="monotone" dataKey="pnl_pct" stroke={accentColor} strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        {/* 사이드 정보 카드 — 봇 운영 현황 (추후 광고로 교체 가능) */}
        <div className="side-info-card">
          <div className="side-info-title">봇 현황</div>
          <div className="side-info-row">
            <span>모니터링</span>
            <strong>{monitoringCoins.length}개 코인</strong>
          </div>
          <div className="side-info-row">
            <span>활성 전략</span>
            <strong>{strategies.find((s: any) => s.is_active)?.display_name || "-"}</strong>
          </div>
          <div className="side-info-row">
            <span>오늘 매매</span>
            <strong>{summary?.today_trades ?? 0}건</strong>
          </div>
          <div className="side-info-row">
            <span>보유 포지션</span>
            <strong>{portfolio.length}개</strong>
          </div>
          {fg && (
            <div className="side-info-row">
              <span>시장 심리</span>
              <strong style={{ color: fgColor }}>{fgLabel}</strong>
            </div>
          )}
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 16, lineHeight: 1.5 }}>
            업비트 API + Claude AI 기반 자동 매매. 60초마다 갱신.
          </div>
        </div>
      </div>

      {/* 뉴스 티커 */}
      {news.length > 0 && (
        <div style={{ marginBottom: 12, position: "relative" }}>
          <style>{`
            @keyframes slideUp { from { transform: translateY(100%); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
            .news-item { animation: slideUp 0.5s ease-out; }
            @keyframes fadeSlideIn {
              from { opacity: 0; transform: translateY(12px); }
              to { opacity: 1; transform: translateY(0); }
            }
            .analysis-enter-1 { animation: fadeSlideIn 0.8s ease-out; }
            .analysis-enter-2 { animation: fadeSlideIn 1.0s ease-out 1.2s both; }
          `}</style>

          {/* 한줄 티커 — 고정 높이, 연한 블루 배경 */}
          <div style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "10px 16px", borderRadius: 10,
            background: news[newsIndex]?.sentiment_keyword === "positive" ? "#f0fdf4" : news[newsIndex]?.sentiment_keyword === "negative" ? "#fef2f2" : "#fffbeb",
            border: `1px solid ${news[newsIndex]?.sentiment_keyword === "positive" ? "#bbf7d0" : news[newsIndex]?.sentiment_keyword === "negative" ? "#fecaca" : "#fde68a"}`,
            transition: "background 0.5s, border-color 0.5s",
            height: 42, overflow: "hidden",
          }}>
            <span className={`badge ${(news[newsIndex]?.sentiment_keyword === "positive" ? "badge-green" : news[newsIndex]?.sentiment_keyword === "negative" ? "badge-red" : "badge-yellow")}`} style={{ fontSize: 9, flexShrink: 0 }}>
              {news[newsIndex]?.sentiment_keyword === "positive" ? "긍정" : news[newsIndex]?.sentiment_keyword === "negative" ? "부정" : "중립"}
            </span>
            <span key={newsIndex} className="news-item" style={{ fontSize: 13, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {news[newsIndex]?.title}
            </span>
            <span style={{ fontSize: 10, color: "var(--text-muted)", flexShrink: 0, marginRight: 8 }}>{news[newsIndex]?.source}</span>
            <button onClick={() => setNewsExpanded(!newsExpanded)} style={{
              background: "none", border: "none", cursor: "pointer",
              fontSize: 22, color: "#6b7fa3", flexShrink: 0,
              transform: newsExpanded ? "rotate(-90deg)" : "rotate(90deg)",
              transition: "transform 0.3s",
              padding: "0 4px", lineHeight: 1,
            }}>›</button>
          </div>

          {/* 펼침 오버레이 — position absolute, 아래로 덮기 */}
          {newsExpanded && (
            <div style={{
              position: "absolute", top: 44, left: 0, right: 0, zIndex: 20,
              background: "#ffffff", border: "1px solid var(--border)", borderRadius: 12,
              boxShadow: "0 12px 40px rgba(0,0,0,0.12)",
            }}>
              {news.slice(0, 10).map((n: any, i: number) => (
                <a key={i} href={n.url} target="_blank" rel="noopener noreferrer" style={{
                  display: "flex", alignItems: "flex-start", gap: 10,
                  padding: "10px 16px", textDecoration: "none", color: "inherit",
                  borderBottom: i < Math.min(news.length, 10) - 1 ? "1px solid var(--border)" : "none",
                  transition: "background 0.15s",
                }} onMouseEnter={(e) => (e.currentTarget.style.background = "#f8fafc")}
                   onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
                  <span className={`badge ${n.sentiment_keyword === "positive" ? "badge-green" : n.sentiment_keyword === "negative" ? "badge-red" : "badge-yellow"}`} style={{ fontSize: 9, flexShrink: 0, marginTop: 2 }}>
                    {n.sentiment_keyword === "positive" ? "긍정" : n.sentiment_keyword === "negative" ? "부정" : "중립"}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, lineHeight: 1.5 }}>{n.title}</div>
                    <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2 }}>
                      {n.source} · {formatDateTime(n.published_at).replace(/\d{4}\. /, "")}
                    </div>
                  </div>
                </a>
              ))}
            </div>
          )}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 24, alignItems: "stretch" }}>
        {/* AI 분석 — 1번 고정 + 2~3번 롤링, 시장 상태 배경색 */}
        <div className="card" style={{ display: "flex", flexDirection: "column" }}>
          <div className="card-title">AI 시장 분석</div>
          {analysis.length > 0 ? (
            <div style={{ display: "flex", flexDirection: "column", flex: 1 }}>
              {(() => {
                const slots = [analysis[0]];
                if (analysis.length > 1) {
                  const pool = analysis.slice(1);
                  const idx2 = analysisIndex % pool.length;
                  const idx3 = (analysisIndex + 1) % pool.length;
                  slots.push(pool[idx2]);
                  if (pool.length > 1) slots.push(pool[idx3]);
                }
                const stateColor = (state: string) =>
                  state === "bullish" ? "#f0fdf4" : state === "bearish" ? "#fef2f2" : "#fffbeb";
                const stateBorder = (state: string) =>
                  state === "bullish" ? "#bbf7d0" : state === "bearish" ? "#fecaca" : "#fde68a";
                return slots.map((a: any, i: number) => (
                  <div key={`${i}-${a?.timestamp}`} className={i === 1 ? "analysis-enter-1" : i === 2 ? "analysis-enter-2" : ""} style={{
                    borderRadius: 10, overflow: "hidden", marginBottom: i < slots.length - 1 ? 10 : 0,
                    border: `1px solid ${stateBorder(a.market_state)}`,
                  }}>
                    {/* 제목 한줄 — 시장 상태 배경색 */}
                    <div style={{
                      display: "flex", justifyContent: "space-between", alignItems: "center",
                      padding: "8px 12px",
                      background: stateColor(a.market_state),
                    }}>
                      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                        <span className={`badge ${a.market_state === "bullish" ? "badge-green" : a.market_state === "bearish" ? "badge-red" : "badge-yellow"}`}>
                          {getMarketStateKR(a.market_state)}
                        </span>
                        {i === 0 && <span style={{ fontSize: 9, color: "var(--accent-blue)", fontWeight: 700 }}>LATEST</span>}
                      </div>
                      <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{formatDateTime(a.timestamp)}</span>
                    </div>
                    {/* 본문 */}
                    <div style={{
                      padding: "10px 12px", background: "#ffffff",
                      fontSize: 13, lineHeight: 1.7,
                      fontWeight: i === 0 ? 600 : 400,
                      color: i === 0 ? "var(--text-primary)" : "var(--text-muted)",
                    }}>{a.summary}</div>
                  </div>
                ));
              })()}
            </div>
          ) : <div className="empty-state">분석 데이터 없음</div>}
        </div>

        {/* 오른쪽: 포트폴리오 + 모니터링 — AI분석과 높이 맞춤 */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16, minHeight: 0 }}>
          {/* 포트폴리오 비중 — 도넛 차트 */}
          <div className="card" style={{ flex: 1 }}>
            <div className="card-title">포트폴리오 비중</div>
            {portfolio.length > 0 ? (
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <ResponsiveContainer width="55%" height={200}>
                  <PieChart>
                    <Pie
                      data={portfolio.map((p: any) => ({ name: p.coin?.replace("KRW-", ""), value: p.weight_pct }))}
                      cx="50%" cy="50%" innerRadius={40} outerRadius={68} dataKey="value"
                      label={({ name, value }: any) => value >= 5 ? `${name}` : ""}
                      labelLine={false} style={{ fontSize: 10 }}
                    >
                      {portfolio.map((_: any, i: number) => (
                        <Cell key={i} fill={["#94a3b8", "#2563eb", "#7c3aed", "#059669", "#d97706", "#dc2626", "#8b5cf6", "#ec4899", "#0891b2"][i % 9]} />
                      ))}
                    </Pie>
                    <Tooltip formatter={(value: any) => [`${value}%`, "비중"]} />
                  </PieChart>
                </ResponsiveContainer>
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {portfolio.map((p: any, i: number) => (
                    <div key={p.coin} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 11 }}>
                      <div style={{ width: 8, height: 8, borderRadius: 2, flexShrink: 0, background: ["#94a3b8", "#2563eb", "#7c3aed", "#059669", "#d97706", "#dc2626", "#8b5cf6", "#ec4899", "#0891b2"][i % 9] }} />
                      <span style={{ fontWeight: 600 }}>{p.coin?.replace("KRW-", "")}</span>
                      <span style={{ color: "var(--text-muted)" }}>{p.weight_pct}%</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : <div className="empty-state">보유 포지션 없음</div>}
          </div>

          {/* 모니터링 코인 */}
          {monitoringCoins.length > 0 && (
            <div className="card" style={{ flex: 1 }}>
              <div className="card-title">모니터링 중 ({monitoringCoins.length}개)</div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {monitoringCoins.map((c: any) => (
                  <div key={c.coin} style={{
                    padding: "5px 10px", borderRadius: 8, fontSize: 11,
                    background: c.market_state === "bullish" ? "#ecfdf5" : c.market_state === "bearish" ? "#fef2f2" : "#f8fafc",
                    border: `1px solid ${c.market_state === "bullish" ? "#a7f3d0" : c.market_state === "bearish" ? "#fecaca" : "var(--border)"}`,
                  }}>
                    <span style={{ fontWeight: 600 }}>{c.coin.replace("KRW-", "")}</span>
                    {c.rsi && <span style={{ marginLeft: 3, color: "var(--text-muted)", fontSize: 10 }}>RSI {c.rsi}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 블로그 배너 */}
      <a href="https://seung-min.tistory.com/61" target="_blank" rel="noopener noreferrer" style={{
        display: "block", marginBottom: 24, padding: "18px 24px", borderRadius: 12,
        background: "linear-gradient(135deg, #059669 0%, #0d9488 50%, #0891b2 100%)",
        color: "#ffffff", textDecoration: "none",
        boxShadow: "0 4px 16px rgba(5, 150, 105, 0.15)",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>개발 과정이 궁금하다면?</div>
            <div style={{ fontSize: 12, color: "rgba(255,255,255,0.6)" }}>
              AI 트레이딩 봇을 만들면서 겪은 시행착오, 버그 수정, 수익률 개선기를 블로그에 기록합니다
            </div>
          </div>
          <div style={{
            padding: "8px 20px", borderRadius: 8, fontSize: 13, fontWeight: 600,
            background: "rgba(255,255,255,0.15)", border: "1px solid rgba(255,255,255,0.25)",
            whiteSpace: "nowrap",
          }}>Blog →</div>
        </div>
      </a>

      {/* 최근 매매 */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div className="card-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span>최근 매매</span>
            {tradeFilter && (
              <span style={{ fontSize: 11, color: "var(--accent-blue)", fontWeight: 600, cursor: "pointer" }}
                onClick={() => setTradeFilter(null)}>
                {tradeFilter.replace("KRW-", "")} ✕
              </span>
            )}
            {sideFilter && (
              <span style={{ fontSize: 11, fontWeight: 600, cursor: "pointer" }}
                className={sideFilter === "buy" ? "positive" : "negative"}
                onClick={() => setSideFilter(null)}>
                {sideFilter === "buy" ? "매수" : "매도"} ✕
              </span>
            )}
          </div>
          {trades.length > 5 && (
            <button onClick={() => setShowAllTrades(!showAllTrades)} style={{
              background: "none", border: "none", cursor: "pointer",
              fontSize: 22, color: "#6b7fa3", lineHeight: 1,
              transform: showAllTrades ? "rotate(-90deg)" : "rotate(90deg)",
              transition: "transform 0.3s",
            }}>›</button>
          )}
        </div>
        {trades.length > 0 ? (
          <div style={{ overflowY: showAllTrades ? "auto" : "hidden", maxHeight: showAllTrades ? 240 : 240, overflowX: "hidden" }}>
            <table style={{ width: "100%", tableLayout: "fixed" }}>
              <colgroup>
                <col style={{ width: "18%" }} />
                <col style={{ width: "10%" }} />
                <col style={{ width: "8%" }} />
                <col style={{ width: "14%" }} />
                <col style={{ width: "14%" }} />
                <col style={{ width: "12%" }} />
                <col style={{ width: "10%" }} />
              </colgroup>
              <thead><tr><th>시간</th><th>종목</th><th>방향</th><th>전략</th><th>단가</th><th>수익률</th><th>보유</th></tr></thead>
              <tbody>
                {(() => {
                  let filtered = trades;
                  if (tradeFilter) filtered = filtered.filter((t: any) => t.coin === tradeFilter);
                  if (sideFilter) filtered = filtered.filter((t: any) => t.side === sideFilter);
                  return (showAllTrades ? filtered.slice(0, 50) : filtered.slice(0, 5)).map((t: any, i: number) => (
                  <tr key={i}>
                    <td style={{ fontSize: 11, color: "var(--text-muted)" }}>{formatDateTime(t.timestamp).replace(/\d{4}\. /, "")}</td>
                    <td style={{ fontWeight: 600, cursor: "pointer", color: tradeFilter === t.coin ? "var(--accent-blue)" : "inherit" }}
                      onClick={() => setTradeFilter(tradeFilter === t.coin ? null : t.coin)}>{t.coin?.replace("KRW-", "")}</td>
                    <td><span className={`badge ${t.side === "buy" ? "badge-green" : "badge-red"}`} style={{ fontSize: 10, cursor: "pointer", opacity: sideFilter && sideFilter !== t.side ? 0.4 : 1 }}
                      onClick={() => setSideFilter(sideFilter === t.side ? null : t.side)}>{t.side === "buy" ? "매수" : "매도"}</span></td>
                    <td style={{ fontSize: 11, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.strategy?.replace(/_/g, " ")}</td>
                    <td style={{ fontSize: 11 }}>{t.price ? Number(t.price).toLocaleString() : "-"}</td>
                    <td className={t.profit_pct != null ? (t.profit_pct >= 0 ? "positive" : "negative") : ""} style={{ fontWeight: 600 }}>{t.profit_pct != null ? formatPercent(t.profit_pct) : "-"}</td>
                    <td style={{ fontSize: 11, color: "var(--text-muted)" }}>{t.hold_minutes != null ? `${t.hold_minutes}분` : "-"}</td>
                  </tr>
                ));
                })()}
              </tbody>
            </table>
          </div>
        ) : <div className="empty-state">매매 내역 없음</div>}
      </div>

      {/* GitHub 배너 */}
      <a href="https://github.com/SeungMinK/cryptobot" target="_blank" rel="noopener noreferrer" style={{
        display: "block", marginBottom: 24, padding: "18px 24px", borderRadius: 12,
        background: "linear-gradient(135deg, #0f172a 0%, #1e3a5f 50%, #312e81 100%)",
        color: "#ffffff", textDecoration: "none",
        boxShadow: "0 4px 16px rgba(15, 23, 42, 0.15)",
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 4 }}>100% 오픈소스 · 직접 만든 AI 트레이딩 봇</div>
            <div style={{ fontSize: 12, color: "rgba(255,255,255,0.55)" }}>
              Claude AI 시장분석 · {strategies.length}개 매매 전략 · 실시간 파라미터 자동 조절 · Python + React
            </div>
          </div>
          <div style={{
            padding: "8px 20px", borderRadius: 8, fontSize: 13, fontWeight: 600,
            background: "rgba(255,255,255,0.12)", border: "1px solid rgba(255,255,255,0.2)",
            whiteSpace: "nowrap",
          }}>GitHub →</div>
        </div>
      </a>

      {/* 매매 전략 */}
      {strategies.length > 0 && (() => {
        const actives = strategies.filter((s: any) => s.is_active);
        const others = strategies.filter((s: any) => !s.is_active);
        return (
          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span>매매 전략</span>
              {others.length > 0 && (
                <button onClick={() => setShowAllStrategies(!showAllStrategies)} style={{
                  background: "none", border: "none", cursor: "pointer",
                  fontSize: 22, color: "#6b7fa3", lineHeight: 1,
                  transform: showAllStrategies ? "rotate(-90deg)" : "rotate(90deg)",
                  transition: "transform 0.3s",
                }}>›</button>
              )}
            </div>

            {/* 운영 중 전략 (N개 가능) */}
            {actives.map((active: any) => {
              const activeStat = strategyStats.find((ss: any) => ss.strategy === active.name);
              return (
              <div style={{
                padding: 16, borderRadius: 12, marginBottom: 16,
                background: "linear-gradient(135deg, #eff6ff 0%, #f0fdf4 100%)",
                border: "1px solid #bfdbfe",
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div style={{ width: 8, height: 8, borderRadius: "50%", background: "#22c55e", boxShadow: "0 0 6px rgba(34,197,94,0.5)" }} />
                    <span style={{ fontWeight: 700, fontSize: 16 }}>{active.display_name}</span>
                    <span style={{ fontSize: 10, color: "#22c55e", fontWeight: 600 }}>운영 중</span>
                  </div>
                  <div style={{ display: "flex", gap: 4 }}>
                    <span className="badge badge-purple" style={{ fontSize: 9 }}>{active.category}</span>
                    <span className="badge badge-yellow" style={{ fontSize: 9 }}>{active.difficulty}</span>
                  </div>
                </div>
                <div style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.6, marginBottom: 10 }}>{active.description}</div>
                {activeStat && (
                  <div style={{ display: "flex", gap: 20, fontSize: 13 }}>
                    <div>
                      <span style={{ color: "var(--text-muted)", fontSize: 11 }}>거래 </span>
                      <span style={{ fontWeight: 700 }}>{activeStat.trades}건</span>
                    </div>
                    <div>
                      <span style={{ color: "var(--text-muted)", fontSize: 11 }}>승률 </span>
                      <span style={{ fontWeight: 700 }} className={activeStat.win_rate >= 50 ? "positive" : "negative"}>{activeStat.win_rate}%</span>
                    </div>
                    <div>
                      <span style={{ color: "var(--text-muted)", fontSize: 11 }}>평균 </span>
                      <span style={{ fontWeight: 700 }} className={activeStat.avg_pct >= 0 ? "positive" : "negative"}>{formatPercent(activeStat.avg_pct)}</span>
                    </div>
                  </div>
                )}
              </div>
              );
            })}

            {/* 나머지 전략 — 전체보기 시만 표시 */}
            {showAllStrategies && (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 8, marginTop: 12 }}>
              {others.map((s: any) => {
                const stat = strategyStats.find((ss: any) => ss.strategy === s.name);
                return (
                  <div key={s.name} style={{
                    padding: "10px 12px", borderRadius: 8,
                    background: "#f8fafc", border: "1px solid var(--border)",
                  }}>
                    <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 3 }}>{s.display_name}</div>
                    <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 6 }}>{s.description.slice(0, 40)}{s.description.length > 40 ? "..." : ""}</div>
                    <div style={{ display: "flex", gap: 8, fontSize: 11 }}>
                      {stat ? (
                        <>
                          <span>{stat.trades}건</span>
                          <span className={stat.win_rate >= 50 ? "positive" : "negative"} style={{ fontWeight: 600 }}>{stat.win_rate}%</span>
                        </>
                      ) : (
                        <span style={{ color: "var(--text-muted)" }}>대기 중</span>
                      )}
                      <span className="badge badge-purple" style={{ fontSize: 8 }}>{s.category}</span>
                    </div>
                  </div>
                );
              })}
            </div>
            )}
          </div>
        );
      })()}

      {/* 일별 성과 */}
      {dailyReturns.length > 0 && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div className="card-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>일별 성과</span>
            {dailyReturns.length > 3 && (
              <button onClick={() => setShowAllDaily(!showAllDaily)} style={{
                background: "none", border: "none", cursor: "pointer",
                fontSize: 22, color: "#6b7fa3", lineHeight: 1,
                transform: showAllDaily ? "rotate(-90deg)" : "rotate(90deg)",
                transition: "transform 0.3s",
              }}>›</button>
            )}
          </div>
          <div style={{ overflowY: showAllDaily ? "auto" : "hidden", maxHeight: 200, overflowX: "hidden" }}>
            <table style={{ width: "100%", tableLayout: "fixed" }}>
              <colgroup>
                <col style={{ width: "30%" }} />
                <col style={{ width: "15%" }} />
                <col style={{ width: "18%" }} />
                <col style={{ width: "20%" }} />
                <col style={{ width: "17%" }} />
              </colgroup>
              <thead><tr><th>날짜</th><th>거래</th><th>승률</th><th>수익률</th><th>손익비</th></tr></thead>
              <tbody>
                {(showAllDaily ? [...dailyReturns].reverse().slice(0, 50) : [...dailyReturns].reverse().slice(0, 3)).map((d: any) => (
                  <tr key={d.date}>
                    <td>{d.date}</td>
                    <td>{d.total_trades || "-"}</td>
                    <td className={(d.win_rate || 0) >= 50 ? "positive" : d.win_rate ? "negative" : ""}>{d.win_rate != null ? `${d.win_rate.toFixed(0)}%` : "-"}</td>
                    <td className={d.daily_pnl_pct >= 0 ? "positive" : "negative"} style={{ fontWeight: 600 }}>{formatPercent(d.daily_pnl_pct)}</td>
                    <td style={{ color: "var(--text-muted)" }}>{d.risk_reward ? `1:${d.risk_reward}` : "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 면책 조항 + 푸터 */}
      <div style={{ textAlign: "center", padding: "48px 24px 24px", color: "var(--text-muted)", fontSize: 11, lineHeight: 1.8 }}>
        <div style={{ borderTop: "1px solid var(--border)", paddingTop: 24, marginBottom: 16 }}>
          <p style={{ margin: "0 0 8px", fontWeight: 600, fontSize: 12, color: "var(--text-secondary)", letterSpacing: 1 }}>
            Disclaimer
          </p>
          <p style={{ margin: 0, maxWidth: 700, marginInline: "auto" }}>
            본 서비스는 학습·포트폴리오 목적으로 제작된 자동매매 실험 프로젝트이며,
            투자 조언이나 특정 자산의 매수·매도를 권유하지 않습니다.<br />
            모든 투자 판단과 그에 따른 손익의 책임은 이용자 본인에게 있으며,
            개발자는 본 서비스 이용으로 발생한 어떠한 손실에도 책임을 지지 않습니다.
          </p>
        </div>
        <div style={{ marginTop: 8 }}>
          Powered by Claude AI + {strategies.length} Trading Strategies
        </div>
      </div>

      {/* 푸터 disclaimer — 추후 광고 영역으로 교체 가능 */}
      <div style={{
        marginTop: 24, padding: "16px 20px", borderRadius: 12,
        background: "#f8fafc", border: "1px solid var(--border)",
        textAlign: "center", fontSize: 11, color: "var(--text-muted)", lineHeight: 1.6,
      }}>
        본 사이트는 자동매매 봇의 운영 결과를 공개하는 데모입니다.
        투자 권유나 수익 보장이 아니며, 실제 투자는 본인 책임입니다.
      </div>
    </div>
  );
}
