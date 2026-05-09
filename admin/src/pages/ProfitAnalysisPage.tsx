import { useEffect, useState } from "react";
import {
  AreaChart, Area,
  XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, PieChart, Pie,
} from "recharts";
import { getBalanceHistory } from "../api/balance";
import { getTradeStats, getDailyReturns } from "../api/trades";
import type { BalanceHistory } from "../types/balance";
import type { TradeStats, DailyReturn } from "../types/trades";
import StatCard from "../components/StatCard";
import { formatKRW, formatPercent } from "../utils/format";

const PERIODS = [
  { label: "7일", value: 7 },
  { label: "30일", value: 30 },
  { label: "90일", value: 90 },
];

const CHART_TOOLTIP_STYLE = {
  contentStyle: { background: "#1e2130", border: "1px solid #2a2d3e", borderRadius: 8, color: "#e4e6f0" },
};

export default function ProfitAnalysisPage() {
  const [days, setDays] = useState(30);
  const [stats, setStats] = useState<TradeStats | null>(null);
  const [daily, setDaily] = useState<DailyReturn[]>([]);
  const [, setHistory] = useState<BalanceHistory[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      getTradeStats(days).catch(() => null),
      getDailyReturns(days).catch(() => []),
      getBalanceHistory(days).catch(() => []),
    ]).then(([s, d, h]) => {
      setStats(s);
      setDaily(d as DailyReturn[]);
      setHistory(h as BalanceHistory[]);
      setLoading(false);
    });
  }, [days]);

  if (loading) return <div className="loading">로딩 중...</div>;

  const winLossData = stats
    ? [
        { name: "승", value: stats.wins, color: "#34d399" },
        { name: "패", value: stats.losses, color: "#f87171" },
      ]
    : [];

  // 오늘 데이터
  const today = daily.length > 0 ? daily[daily.length - 1] : null;

  return (
    <div>
      <div className="page-header">
        <h1>수익률 분석</h1>
        <p>매매 성과 및 수익률 추이</p>
      </div>

      {/* Period Selector */}
      <div className="period-selector">
        {PERIODS.map((p) => (
          <button key={p.value} className={days === p.value ? "active" : ""} onClick={() => setDays(p.value)}>
            {p.label}
          </button>
        ))}
      </div>

      {/* KPI — 전체 + 오늘 비교 */}
      {stats && (
        <div className="kpi-grid">
          <StatCard
            label="실질 수익"
            value={formatKRW(stats.total_profit_krw)}
            sub={`수수료 제외 후 순수익 (수수료 ${formatKRW(stats.total_fees)})`}
            valueClass={stats.total_profit_krw >= 0 ? "positive" : "negative"}
          />
          <StatCard
            label="승률"
            value={`${stats.win_rate.toFixed(1)}%`}
            sub={`${stats.wins}승 ${stats.losses}패`}
            valueClass={stats.win_rate >= 50 ? "positive" : "negative"}
          />
          <StatCard
            label="평균 수익률"
            value={formatPercent(stats.avg_profit_pct)}
            sub={`${stats.total_trades}건 거래`}
            valueClass={stats.avg_profit_pct >= 0 ? "positive" : "negative"}
          />
          <StatCard
            label="오늘"
            value={today ? formatPercent(today.daily_return_pct) : "-"}
            sub={today ? `${today.total_trades}건, 승률 ${today.win_rate?.toFixed(0) || 0}%` : "거래 없음"}
            valueClass={today && today.daily_return_pct >= 0 ? "positive" : "negative"}
          />
        </div>
      )}

      {/* 승/패 비율 — 전체 + 오늘 나란히 */}
      <div className="grid-2 mb-6">
        <div className="card">
          <div className="card-title">전체 승/패</div>
          {winLossData.length > 0 && (stats?.wins ?? 0) + (stats?.losses ?? 0) > 0 ? (
            <div className="flex items-center gap-6">
              <ResponsiveContainer width="50%" height={160}>
                <PieChart>
                  <Pie data={winLossData} cx="50%" cy="50%" innerRadius={40} outerRadius={65} dataKey="value" label={({ name, value }) => `${name} ${value}`}>
                    {winLossData.map((entry, index) => (
                      <Cell key={index} fill={entry.color} />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <div>
                <div style={{ fontSize: 28, fontWeight: 700 }} className={stats!.win_rate >= 50 ? "positive" : "negative"}>
                  {stats!.win_rate.toFixed(1)}%
                </div>
                <div style={{ fontSize: 13, color: "var(--text-muted)" }}>{stats!.wins}승 {stats!.losses}패</div>
              </div>
            </div>
          ) : (
            <div className="empty-state">데이터 없음</div>
          )}
        </div>

        <div className="card">
          <div className="card-title">오늘 승/패</div>
          {today && today.total_trades > 0 ? (
            <div className="flex items-center gap-6">
              <ResponsiveContainer width="50%" height={160}>
                <PieChart>
                  <Pie
                    data={[
                      { name: "승", value: Math.round((today.win_rate || 0) / 100 * (today.total_trades || 0)), color: "#34d399" },
                      { name: "패", value: (today.total_trades || 0) - Math.round((today.win_rate || 0) / 100 * (today.total_trades || 0)), color: "#f87171" },
                    ].filter(d => d.value > 0)}
                    cx="50%" cy="50%" innerRadius={40} outerRadius={65} dataKey="value"
                    label={({ name, value }) => `${name} ${value}`}
                  >
                    {[
                      { color: "#34d399" },
                      { color: "#f87171" },
                    ].map((entry, index) => (
                      <Cell key={index} fill={entry.color} />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
              <div>
                <div style={{ fontSize: 28, fontWeight: 700 }} className={(today.win_rate || 0) >= 50 ? "positive" : "negative"}>
                  {today.win_rate?.toFixed(1) || 0}%
                </div>
                <div style={{ fontSize: 13, color: "var(--text-muted)" }}>{today.total_trades}건 거래</div>
                <div style={{ fontSize: 13, marginTop: 4 }} className={today.daily_pnl_krw >= 0 ? "positive" : "negative"}>
                  {formatKRW(today.daily_pnl_krw)} ({formatPercent(today.daily_return_pct)})
                </div>
              </div>
            </div>
          ) : (
            <div className="empty-state">오늘 거래 없음</div>
          )}
        </div>
      </div>

      {/* 일별 손익 */}
      <div className="card mb-6">
        <div className="card-title">일별 손익</div>
        {daily.length > 0 ? (
          <>
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>날짜</th>
                    <th>거래</th>
                    <th>승률</th>
                    <th>수익률</th>
                    <th>손익</th>
                  </tr>
                </thead>
                <tbody>
                  {[...daily].reverse().map((d) => (
                    <tr key={d.date}>
                      <td>{d.date}</td>
                      <td>{d.total_trades || "-"}</td>
                      <td className={(d.win_rate || 0) >= 50 ? "positive" : d.win_rate ? "negative" : ""}>
                        {d.win_rate != null ? `${d.win_rate.toFixed(0)}%` : "-"}
                      </td>
                      <td className={d.daily_return_pct >= 0 ? "positive" : "negative"} style={{ fontWeight: 600 }}>
                        {d.daily_return_pct != null ? formatPercent(d.daily_return_pct) : "-"}
                      </td>
                      <td className={d.daily_pnl_krw >= 0 ? "positive" : "negative"}>
                        {d.daily_pnl_krw != null ? formatKRW(d.daily_pnl_krw) : "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <div className="empty-state">데이터 없음</div>
        )}
      </div>

      {/* 자산 잔고 추이 */}
      <div className="card">
        <div className="card-title">누적 수익률</div>
        {daily.length > 0 ? (
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={daily.map((d, i) => ({
              ...d,
              cumulative_pct: daily.slice(0, i + 1).reduce((sum, x) => sum + (x.daily_return_pct || 0), 0),
            }))}>
              <defs>
                <linearGradient id="cumGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#4a9eff" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="#4a9eff" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="date" tick={{ fill: "#8b8fa3", fontSize: 10 }} tickFormatter={(v) => v.slice(5)} />
              <YAxis tick={{ fill: "#8b8fa3", fontSize: 11 }} tickFormatter={(v) => formatPercent(v)} />
              <Tooltip {...CHART_TOOLTIP_STYLE} formatter={(value) => [formatPercent(Number(value)), "누적 수익률"]} />
              <Area type="monotone" dataKey="cumulative_pct" stroke="#4a9eff" fill="url(#cumGradient)" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="empty-state">데이터 없음</div>
        )}
      </div>
    </div>
  );
}
