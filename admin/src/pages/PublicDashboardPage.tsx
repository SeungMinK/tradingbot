import { useEffect, useState, useCallback } from "react";
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
  LineChart, Line, XAxis, YAxis, CartesianGrid, ReferenceLine,
  BarChart, Bar,
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
  // #241: Dark mode (localStorage 영속)
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    try { return (localStorage.getItem("cryptobot-theme") as any) || "light"; } catch { return "light"; }
  });
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem("cryptobot-theme", theme); } catch {}
  }, [theme]);
  const [showAllTrades, setShowAllTrades] = useState(true);
  const [tradeFilter, setTradeFilter] = useState<string | null>(null);
  const [sideFilter, setSideFilter] = useState<string | null>(null);
  const [showAllDaily, setShowAllDaily] = useState(false);
  const [newsExpanded, setNewsExpanded] = useState(false);
  const [newsIndex, setNewsIndex] = useState(0);
  const [analysisIndex, setAnalysisIndex] = useState(0);
  const [showAllStrategies, setShowAllStrategies] = useState(false);
  // #287 탭 (코인/KIS 분리)
  const [tab, setTab] = useState<"coin" | "kis">("coin");

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

  // #240: 방문자 추적 ping (sessionStorage 기반)
  useEffect(() => {
    try {
      const KEY = "cryptobot-session-id";
      let sid = sessionStorage.getItem(KEY);
      if (!sid) {
        sid = (crypto as any).randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2);
        sessionStorage.setItem(KEY, sid);
      }
      const base = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api").replace(/\/api$/, "");
      fetch(`${base}/api/public/visit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid, page: "/" }),
      }).catch(() => {});
    } catch {}
  }, []);

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

  // #241: Skeleton loading — 점프/깜빡임 줄이기
  if (loading) return (
    <div className="public-wrap">
      <div className="public-header">
        <div className="public-header-brand">
          <div className="skel" style={{ width: 36, height: 36, borderRadius: 10 }} />
          <div>
            <div className="skel skel-line" style={{ width: 100 }} />
            <div className="skel skel-line" style={{ width: 140, height: 11 }} />
          </div>
        </div>
        <div className="skel" style={{ width: 70, height: 28, borderRadius: 20 }} />
      </div>
      <div className="skel" style={{ height: 320, marginBottom: 24, borderRadius: 20 }} />
      <div className="kpi-grid-public" style={{ gridTemplateColumns: "repeat(6, 1fr)" }}>
        {[...Array(6)].map((_, i) => (<div key={i} className="skel skel-card" />))}
      </div>
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

  const activeStrategy = strategies.find((s: any) => s.is_active)?.display_name || "-";

  // #242: 운영 시작일 — accountPnl.started_at (서버에서 정확히 계산)
  const startDate = accountPnl?.started_at ?? null;
  const operatingDays = startDate ? Math.floor((Date.now() - new Date(startDate).getTime()) / 86400000) : 0;

  // 최대 일일 손실폭(MDD) 계산 (pnlHistory에서)
  const mddPct = pnlHistory.length > 0
    ? Math.min(...pnlHistory.map((p: any) => p.pnl_pct ?? 0))
    : 0;

  return (
    <div className="public-wrap">
      {/* #239: 상단 헤더 — 로고 + 봇 이름 + 운영 상태 배지 */}
      <div className="public-header">
        <div className="public-header-brand">
          <div className="public-header-logo">T</div>
          <div>
            <div className="public-header-name">TradingBot</div>
            <div className="public-header-tag">코인 + 주식 자동매매 · {operatingDays}일째 운영</div>
          </div>
        </div>
        <div className="flex items-center gap-2.5">
          <button
            className="theme-toggle"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            title={theme === "dark" ? "라이트 모드" : "다크 모드"}
          >
            {theme === "dark" ? "☀️" : "🌙"}
          </button>
          <div className="status-badge">
            <span className="status-badge-dot" />
            LIVE
          </div>
        </div>
      </div>

      {/* #287 탭 — 코인 / KIS */}
      <div style={{ display: "flex", gap: 4, margin: "12px 0 16px", borderBottom: "1px solid var(--border-color, #ddd)" }}>
        {([
          { id: "coin", label: "🪙 코인 (Upbit)" },
          { id: "kis", label: "📈 KIS 미국주식" },
        ] as const).map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            style={{
              padding: "10px 18px", border: "none", cursor: "pointer", fontSize: 14,
              fontWeight: tab === t.id ? 700 : 500,
              borderBottom: tab === t.id ? "2px solid #4a9eff" : "2px solid transparent",
              background: "transparent",
              color: tab === t.id ? "#4a9eff" : "var(--text-secondary, #666)",
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "kis" && (() => {
        const kisTrades = trades.filter((t: any) => t.market === "kis_us" || t.market === "kis_kr");
        const kisSells = kisTrades.filter((t: any) => t.side === "sell" && t.profit_pct != null);
        const totalKisPct = kisSells.reduce((s: number, t: any) => s + (t.profit_pct || 0), 0);
        const winCount = kisSells.filter((t: any) => (t.profit_pct || 0) > 0).length;
        const lossCount = kisSells.filter((t: any) => (t.profit_pct || 0) < 0).length;
        const winRate = kisSells.length > 0 ? (winCount / kisSells.length) * 100 : 0;
        return (
          <div>
            {/* KIS 손익 요약 */}
            <div className="pnl-hero">
              <div className="pnl-hero-top">
                <div>
                  <div className="pnl-hero-title">KIS 미국주식 — 누적 손익률 (단타/EOD)</div>
                  <div className="pnl-hero-value" style={{ color: totalKisPct > 0 ? "#34d399" : totalKisPct < 0 ? "#f87171" : "var(--text-secondary)" }}>
                    {kisSells.length > 0
                      ? `${totalKisPct >= 0 ? "+" : ""}${totalKisPct.toFixed(2)}%`
                      : <span className="text-2xl">매매 대기 중</span>}
                  </div>
                  <div className="pnl-hero-sub">
                    체결 {kisSells.length}건 · 승률 {winRate.toFixed(0)}% (승 {winCount} / 패 {lossCount})
                  </div>
                </div>
                <div className="pnl-hero-meta">
                  <div className="pnl-hero-meta-item">
                    <span>전략</span>
                    <strong>Zarattini ORB</strong>
                  </div>
                  <div className="pnl-hero-meta-item">
                    <span>봉 단위</span>
                    <strong>5분봉</strong>
                  </div>
                  <div className="pnl-hero-meta-item">
                    <span>모드</span>
                    <strong>단타 (EOD 청산)</strong>
                  </div>
                </div>
              </div>
            </div>

            {/* KIS 매매 내역 */}
            <div className="card mt-4">
              <div className="card-title">KIS 매매 내역 (최근 30건)</div>
              {kisTrades.length === 0 ? (
                <div className="empty-state" style={{ padding: 32, textAlign: "center", color: "var(--text-muted, #888)" }}>
                  아직 매매 없음. 봇이 ORB 돌파 + VWAP + 거래량 spike 신호 대기 중.
                </div>
              ) : (
                <div className="table-container">
                  <table>
                    <colgroup>
                      <col style={{ width: "20%" }} />
                      <col style={{ width: "18%" }} />
                      <col style={{ width: "12%" }} />
                      <col style={{ width: "15%" }} />
                      <col style={{ width: "35%" }} />
                    </colgroup>
                    <thead>
                      <tr>
                        <th>시간 (KST)</th>
                        <th>종목</th>
                        <th>방향</th>
                        <th>수익률</th>
                        <th>사유</th>
                      </tr>
                    </thead>
                    <tbody>
                      {kisTrades.slice(0, 30).map((t: any, i: number) => (
                        <tr key={t.id || i}>
                          <td style={{ fontSize: 11 }}>{formatDateTime(t.timestamp).replace(/\d{4}\. /, "")}</td>
                          <td className="font-semibold">{t.coin}</td>
                          <td>
                            <span className={`badge ${t.side === "buy" ? "badge-green" : "badge-red"}`} style={{ fontSize: 10 }}>
                              {t.side === "buy" ? "매수" : "매도"}
                            </span>
                          </td>
                          <td className={t.profit_pct > 0 ? "positive" : t.profit_pct < 0 ? "negative" : ""}>
                            {t.profit_pct != null ? `${t.profit_pct > 0 ? "+" : ""}${t.profit_pct.toFixed(2)}%` : "-"}
                          </td>
                          <td className="text-xs text-muted-foreground">
                            {(t.trigger_reason || "").slice(0, 80)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div style={{ padding: 16, marginTop: 16, fontSize: 12, color: "var(--text-muted, #888)", textAlign: "center" }}>
              📚 학술 근거: Zarattini & Aziz (2023) "Can Day Trading Really Be Profitable?"
              <br />
              QQQ 5분 ORB → 8년 누적 +1,484%. 현재 SOXL로 검증 진행 중.
            </div>
          </div>
        );
      })()}

      {tab === "coin" && <>
      {/* #237 Hero: 누적 손익률 + 큰 차트 (전체 폭) */}
      <div className="pnl-hero">
        <div className="pnl-hero-top">
          <div>
            <div className="pnl-hero-title">누적 손익률</div>
            <div className="pnl-hero-value" style={{ color: accentColor }}>
              {isPos ? "+" : ""}{totalPct.toFixed(2)}%
            </div>
            <div className="pnl-hero-sub">
              오늘 변동 <span style={{ color: todayPct >= 0 ? "#34d399" : "#f87171", fontWeight: 700 }}>
                {todayPct >= 0 ? "+" : ""}{todayPct.toFixed(2)}%
              </span>
            </div>
          </div>
          <div className="pnl-hero-meta">
            <div className="pnl-hero-meta-item">
              <span>활성 전략</span>
              <strong>{activeStrategy}</strong>
            </div>
            <div className="pnl-hero-meta-item">
              <span>모니터링</span>
              <strong>{monitoringCoins.length}개 코인</strong>
            </div>
            {fg && (
              <div className="pnl-hero-meta-item" style={{ minWidth: 140 }}>
                <span>공포/탐욕 · {fgLabel}</span>
                <strong style={{ color: fgColor, fontSize: 18 }}>{fg.value}<span style={{ fontSize: 11, opacity: 0.6 }}>/100</span></strong>
                <div className="fg-gauge">
                  <div className="fg-gauge-track">
                    <div className="fg-gauge-marker" style={{ left: `${fg.value}%` }} />
                  </div>
                  <div className="fg-gauge-labels">
                    <span>공포</span><span>탐욕</span>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* #241: 일별 추이 + BTC HODL 벤치마크 */}
        {pnlHistory.length > 1 && (
          <div style={{ marginTop: 24, height: 240 }}>
            <div style={{ display: "flex", gap: 18, fontSize: 11, marginBottom: 6, opacity: 0.85 }}>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ width: 14, height: 2.5, background: accentColor, display: "inline-block", borderRadius: 2 }} />
                내 봇
              </span>
              <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ width: 14, borderTop: "2px dashed #fbbf24", display: "inline-block" }} />
                BTC 단순 보유
              </span>
            </div>
            <ResponsiveContainer>
              <LineChart data={pnlHistory} margin={{ top: 5, right: 8, left: -16, bottom: 0 }}>
                <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
                <XAxis dataKey="date" tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 11 }} tickFormatter={(v: string) => v.slice(5)} />
                <YAxis tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 11 }} unit="%" width={48} />
                <ReferenceLine y={0} stroke="rgba(255,255,255,0.18)" strokeDasharray="3 3" />
                <Tooltip
                  contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8, fontSize: 12 }}
                  labelStyle={{ color: "#94a3b8" }}
                  formatter={(v: any, name: any) => [`${v >= 0 ? "+" : ""}${v}%`, name === "pnl_pct" ? "내 봇" : "BTC HODL"]}
                />
                <Line type="monotone" dataKey="btc_hodl_pct" stroke="#fbbf24" strokeWidth={1.5} strokeDasharray="4 4" dot={false} />
                <Line type="monotone" dataKey="pnl_pct" stroke={accentColor} strokeWidth={2.5} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* #237/#239 KPI 그리드 — Hero 아래 (6개로 확장) */}
      {summary && (
        <div className="kpi-grid-public" style={{ gridTemplateColumns: "repeat(6, 1fr)" }}>
          <div className="kpi-card-public">
            <div className="kpi-label-public">전체 승률</div>
            <div className="kpi-value-public" style={{ color: summary.win_rate >= 50 ? "#10b981" : "#ef4444" }}>
              {summary.win_rate.toFixed(1)}%
            </div>
            <div className="kpi-sub-public">오늘 {summary.today_win_rate.toFixed(0)}%</div>
          </div>
          <div className="kpi-card-public">
            <div className="kpi-label-public">평균 수익</div>
            <div className="kpi-value-public" style={{ color: summary.avg_profit_pct >= 0 ? "#10b981" : "#ef4444" }}>
              {formatPercent(summary.avg_profit_pct)}
            </div>
            <div className="kpi-sub-public">오늘 {formatPercent(summary.today_avg_pct)}</div>
          </div>
          <div className="kpi-card-public">
            <div className="kpi-label-public">오늘 매매</div>
            <div className="kpi-value-public">{summary.today_trades}건</div>
            <div className="kpi-sub-public">총 {summary.total_trades}건</div>
          </div>
          <div className="kpi-card-public">
            <div className="kpi-label-public">최대 손실폭</div>
            <div className="kpi-value-public" style={{ color: "#ef4444" }}>
              {mddPct.toFixed(2)}%
            </div>
            <div className="kpi-sub-public">기간 중 최저점</div>
          </div>
          <div className="kpi-card-public">
            <div className="kpi-label-public">운영 기간</div>
            <div className="kpi-value-public">{operatingDays}일</div>
            <div className="kpi-sub-public">{startDate?.slice(5) ?? "-"}부터</div>
          </div>
          <div className="kpi-card-public">
            <div className="kpi-label-public">보유 포지션</div>
            <div className="kpi-value-public">{portfolio.length}개</div>
            <div className="kpi-sub-public">{monitoringCoins.length}개 메이저 모니터링</div>
          </div>
        </div>
      )}

      {/* #242: 일별 손익 막대 차트 (캘린더 블록 → 막대 차트로 변경, 더 명확) */}
      {pnlHistory.length > 0 && (() => {
        const dailyData = pnlHistory.map((d: any, i: number) => ({
          date: d.date,
          change: i > 0 ? Number((d.pnl_pct - pnlHistory[i - 1].pnl_pct).toFixed(2)) : Number(d.pnl_pct.toFixed(2)),
        }));
        const wins = dailyData.filter(d => d.change > 0).length;
        const losses = dailyData.filter(d => d.change < 0).length;
        const flat = dailyData.length - wins - losses;
        return (
          <div className="card mb-6">
            <div className="section-title-row" style={{ margin: "0 0 12px" }}>
              <h2>일별 손익</h2>
              <span className="section-meta">
                <span style={{ color: "#10b981" }}>이익 {wins}일</span> ·
                <span style={{ color: "#ef4444", marginLeft: 6 }}>손실 {losses}일</span>
                {flat > 0 && <span style={{ color: "var(--text-muted)", marginLeft: 6 }}>· 보합 {flat}일</span>}
              </span>
            </div>
            <div style={{ height: 180 }}>
              <ResponsiveContainer>
                <BarChart data={dailyData} margin={{ top: 5, right: 8, left: -16, bottom: 0 }}>
                  <CartesianGrid stroke="var(--border)" vertical={false} strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fill: "var(--text-muted)", fontSize: 10 }} tickFormatter={(v: string) => v.slice(5)} />
                  <YAxis tick={{ fill: "var(--text-muted)", fontSize: 10 }} unit="%" width={40} />
                  <ReferenceLine y={0} stroke="var(--border)" />
                  <Tooltip
                    contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 8, fontSize: 12, color: "#fff" }}
                    formatter={(v: any) => [`${v >= 0 ? "+" : ""}${v}%`, "변동"]}
                  />
                  <Bar dataKey="change" radius={[3, 3, 0, 0]}>
                    {dailyData.map((d, i) => (
                      <Cell key={i} fill={d.change >= 0 ? "#10b981" : "#ef4444"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        );
      })()}

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
              <div className="flex items-center gap-2">
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
                      <span className="font-semibold">{p.coin?.replace("KRW-", "")}</span>
                      <span className="text-muted-foreground">{p.weight_pct}%</span>
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
                    <span className="font-semibold">{c.coin.replace("KRW-", "")}</span>
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
        <div className="flex justify-between items-center">
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
      <div className="card mb-6">
        <div className="card-title flex justify-between items-center">
          <div className="flex items-center gap-2">
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
                    <td className="text-xs text-muted-foreground">{formatDateTime(t.timestamp).replace(/\d{4}\. /, "")}</td>
                    <td style={{ fontWeight: 600, cursor: "pointer", color: tradeFilter === t.coin ? "var(--accent-blue)" : "inherit" }}
                      onClick={() => setTradeFilter(tradeFilter === t.coin ? null : t.coin)}>{t.coin?.replace("KRW-", "")}</td>
                    <td><span className={`badge ${t.side === "buy" ? "badge-green" : "badge-red"}`} style={{ fontSize: 10, cursor: "pointer", opacity: sideFilter && sideFilter !== t.side ? 0.4 : 1 }}
                      onClick={() => setSideFilter(sideFilter === t.side ? null : t.side)}>{t.side === "buy" ? "매수" : "매도"}</span></td>
                    <td style={{ fontSize: 11, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.strategy?.replace(/_/g, " ")}</td>
                    <td style={{ fontSize: 11 }}>{t.price ? Number(t.price).toLocaleString() : "-"}</td>
                    <td className={`font-semibold ${t.profit_pct != null ? (t.profit_pct >= 0 ? "positive" : "negative") : ""}`}>{t.profit_pct != null ? formatPercent(t.profit_pct) : "-"}</td>
                    <td className="text-xs text-muted-foreground">{t.hold_minutes != null ? `${t.hold_minutes}분` : "-"}</td>
                  </tr>
                ));
                })()}
              </tbody>
            </table>
          </div>
        ) : <div className="empty-state">매매 내역 없음</div>}
      </div>

      {/* GitHub 배너 */}
      <a href="https://github.com/SeungMinK/tradingbot" target="_blank" rel="noopener noreferrer" style={{
        display: "block", marginBottom: 24, padding: "18px 24px", borderRadius: 12,
        background: "linear-gradient(135deg, #0f172a 0%, #1e3a5f 50%, #312e81 100%)",
        color: "#ffffff", textDecoration: "none",
        boxShadow: "0 4px 16px rgba(15, 23, 42, 0.15)",
      }}>
        <div className="flex justify-between items-center">
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
          <div className="card mb-6">
            <div className="card-title flex justify-between items-center">
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
                  <div className="flex items-center gap-2">
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
                      <span className="text-xs text-muted-foreground">거래 </span>
                      <span className="font-bold">{activeStat.trades}건</span>
                    </div>
                    <div>
                      <span className="text-xs text-muted-foreground">승률 </span>
                      <span className={`font-bold ${activeStat.win_rate >= 50 ? "positive" : "negative"}`}>{activeStat.win_rate}%</span>
                    </div>
                    <div>
                      <span className="text-xs text-muted-foreground">평균 </span>
                      <span className={`font-bold ${activeStat.avg_pct >= 0 ? "positive" : "negative"}`}>{formatPercent(activeStat.avg_pct)}</span>
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
                          <span className={`font-semibold ${stat.win_rate >= 50 ? "positive" : "negative"}`}>{stat.win_rate}%</span>
                        </>
                      ) : (
                        <span className="text-muted-foreground">대기 중</span>
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
        <div className="card mb-6">
          <div className="card-title flex justify-between items-center">
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
                    <td className={`font-semibold ${d.daily_pnl_pct >= 0 ? "positive" : "negative"}`}>{formatPercent(d.daily_pnl_pct)}</td>
                    <td className="text-muted-foreground">{d.risk_reward ? `1:${d.risk_reward}` : "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      </>}

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

    </div>
  );
}
