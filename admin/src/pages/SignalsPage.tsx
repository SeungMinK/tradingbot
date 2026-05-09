import { useEffect, useState, useCallback } from "react";
import { getSignals, getSignalStats } from "../api/signals";
import type { SignalItem, SignalStats } from "../api/signals";
import StatCard from "../components/StatCard";
import Pagination from "../components/Pagination";
import { formatKRW, formatDateTime, formatNumber } from "../utils/format";
import { getParamDesc } from "../utils/paramDescriptions";
import { getIndicatorDesc, getMarketStateKR, MARKET_STATE_KR } from "../utils/indicatorDescriptions";
import { cn } from "@/lib/utils";

// 기본 뷰는 "매매만" (hold 제외). HOLD는 조건 미충족 기록이라 confidence=0이 정상이라
// 대시보드에 섞이면 buy/sell 신호가 묻히고 "신뢰도가 다 0%"처럼 보여 오해를 유발함.
const SIGNAL_FILTERS = [
  { label: "매매만", value: "trades" }, // buy + sell (exclude_hold=true)
  { label: "매수", value: "buy" },
  { label: "매도", value: "sell" },
  { label: "HOLD", value: "hold" },
  { label: "전체", value: "" },
] as const;

const STAT_PERIODS = [
  { label: "1시간", hours: 1 },
  { label: "6시간", hours: 6 },
  { label: "24시간", hours: 24 },
  { label: "7일", hours: 168 },
] as const;

export default function SignalsPage() {
  const [signals, setSignals] = useState<SignalItem[]>([]);
  const [stats, setStats] = useState<SignalStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState<string>("trades"); // 기본: 매매만
  const [excludeZeroConfidence, setExcludeZeroConfidence] = useState(true);
  const [statPeriod, setStatPeriod] = useState(1);
  const [selected, setSelected] = useState<SignalItem | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const queryParams: Parameters<typeof getSignals>[0] = { page, limit: 30 };
      if (filter === "trades") {
        queryParams.exclude_hold = true;
      } else if (filter) {
        queryParams.signal_type = filter;
      }
      if (excludeZeroConfidence) {
        // 0.001은 0%로 표시되지만 실제 0은 아닌 값까지 포함 — 0 초과만 남김
        queryParams.min_confidence = 0.001;
      }

      const [signalRes, statsRes] = await Promise.all([
        getSignals(queryParams),
        getSignalStats(statPeriod),
      ]);
      setSignals(signalRes.items);
      setTotalPages(signalRes.pages);
      setTotal(signalRes.total);
      setStats(statsRes);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [page, filter, excludeZeroConfidence, statPeriod]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // 자동 새로고침 (30초)
  useEffect(() => {
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, [fetchData]);

  if (loading) return <div className="loading">로딩 중...</div>;

  return (
    <div>
      <div className="page-header">
        <h1>Signals</h1>
        <p>매매 판단 이력 — 실시간 자동 갱신 (30초)</p>
      </div>

      {/* Stats (#351 Tailwind) */}
      <div className="flex gap-1 mb-3">
        {STAT_PERIODS.map((p) => (
          <button
            key={p.hours}
            onClick={() => setStatPeriod(p.hours)}
            className={cn(
              "px-2.5 py-1 text-xs rounded-md border-none cursor-pointer transition-colors",
              statPeriod === p.hours
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground hover:bg-accent"
            )}
          >
            {p.label}
          </button>
        ))}
      </div>

      {stats && (
        <div className="kpi-grid">
          <StatCard label="전체 신호" value={formatNumber(stats.total)} />
          <StatCard
            label="매수 신호"
            value={formatNumber(stats.buy_signals)}
            valueClass="positive"
          />
          <StatCard
            label="매도 신호"
            value={formatNumber(stats.sell_signals)}
            valueClass="negative"
          />
          <StatCard
            label="실행됨"
            value={formatNumber(stats.executed)}
            valueClass={stats.executed > 0 ? "positive" : undefined}
          />
        </div>
      )}

      {/* Filter (#351 Tailwind) */}
      <div className="card mb-4">
        <div className="flex justify-between items-center flex-wrap gap-2">
          <div className="flex gap-1.5 flex-wrap">
            {SIGNAL_FILTERS.map((f) => (
              <button
                key={f.value}
                onClick={() => { setFilter(f.value); setPage(1); }}
                className={cn(
                  "px-3.5 py-1.5 rounded-md border-none cursor-pointer text-sm transition-colors",
                  filter === f.value
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground hover:bg-accent"
                )}
              >
                {f.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-3">
            <label
              className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer"
              title="HOLD 신호는 조건 미충족 기록이라 confidence=0이 정상. 끄면 포함됨."
            >
              <input
                type="checkbox"
                checked={excludeZeroConfidence}
                onChange={(e) => { setExcludeZeroConfidence(e.target.checked); setPage(1); }}
                className="cursor-pointer"
              />
              신뢰도 0% 제외
            </label>
            <div className="text-xs text-muted-foreground">
              총 {formatNumber(total)}건
            </div>
          </div>
        </div>
      </div>

      {/* Signal List */}
      <div className="card">
        {signals.length > 0 ? (
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>시간</th>
                  <th>코인</th>
                  <th>신호</th>
                  <th>전략</th>
                  <th>판단 근거</th>
                  <th>신뢰도</th>
                  <th>가격</th>
                  <th>시장</th>
                  <th>실행</th>
                </tr>
              </thead>
              <tbody>
                {signals.map((s) => (
                  <tr
                    key={s.id}
                    onClick={() => setSelected(s)}
                    style={{ cursor: "pointer" }}
                  >
                    <td style={{ fontSize: 11, whiteSpace: "nowrap" }}>{formatDateTime(s.timestamp)}</td>
                    <td style={{ fontWeight: 600, fontSize: 12 }}>{s.coin?.replace("KRW-", "")}</td>
                    <td>
                      <span className={`badge ${s.signal_type === "buy" ? "badge-green" : s.signal_type === "sell" ? "badge-red" : "badge-yellow"}`}>
                        {s.signal_type === "buy" ? "매수" : s.signal_type === "sell" ? "매도" : "HOLD"}
                      </span>
                    </td>
                    <td style={{ fontSize: 12 }}>{s.strategy}</td>
                    <td style={{ fontSize: 12, maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {s.trigger_reason}
                    </td>
                    <td>
                      <ConfidenceBar value={s.confidence} />
                    </td>
                    <td style={{ fontSize: 12, whiteSpace: "nowrap" }}>{formatKRW(s.current_price)}</td>
                    <td>
                      {s.market_state && (
                        <span
                          className={`badge ${s.market_state === "bullish" ? "badge-green" : s.market_state === "bearish" ? "badge-red" : "badge-yellow"}`}
                          title={MARKET_STATE_KR[s.market_state]?.description || ""}
                        >
                          {getMarketStateKR(s.market_state)}
                        </span>
                      )}
                    </td>
                    <td>
                      {s.executed ? (
                        <span className="badge badge-green">O</span>
                      ) : s.skip_reason ? (
                        <span style={{ fontSize: 11, color: "var(--text-muted)" }} title={s.skip_reason}>-</span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty-state">신호 데이터 없음</div>
        )}

        {totalPages > 1 && (
          <Pagination page={page} pages={totalPages} onPageChange={setPage} />
        )}
      </div>

      {/* Detail Modal */}
      {selected && (
        <div
          className="modal-overlay"
          onClick={() => setSelected(null)}
        >
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <h3 style={{ margin: 0 }}>신호 상세 #{selected.id}</h3>
              <button onClick={() => setSelected(null)} style={{ background: "none", border: "none", color: "var(--text-secondary)", fontSize: 18, cursor: "pointer" }}>x</button>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              <DetailItem label="시간" value={formatDateTime(selected.timestamp)} />
              <DetailItem label="신호" value={selected.signal_type === "buy" ? "매수" : selected.signal_type === "sell" ? "매도" : "HOLD"} />
              <DetailItem label="전략" value={selected.strategy} />
              <DetailItem label="신뢰도" value={`${(selected.confidence * 100).toFixed(1)}%`} caption="매수/매도 신호의 확신 정도. HOLD일 때는 항상 0%." />
              <DetailItem label={`${selected.coin?.replace("KRW-", "") || "BTC"} 가격`} value={formatKRW(selected.current_price)} />
              <DetailItem
                label="시장 상태"
                value={selected.market_state ? getMarketStateKR(selected.market_state) : "-"}
                caption={selected.market_state ? MARKET_STATE_KR[selected.market_state]?.description : undefined}
              />
              <DetailItem label="판단 근거" value={selected.trigger_reason || "-"} full caption="봇이 이 판단을 내린 이유" />
              {selected.skip_reason && (
                <DetailItem label="스킵 사유" value={selected.skip_reason} full caption="매수/매도 신호가 있었지만 실행하지 않은 이유" />
              )}
            </div>

            {(selected.rsi_14 != null || selected.ma_5 != null) && (
              <div style={{ borderTop: "1px solid var(--border)", margin: "16px 0", paddingTop: 16 }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12 }}>지표 데이터</div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  {[
                    { key: "rsi_14", value: selected.rsi_14?.toFixed(1) },
                    { key: "ma_5", value: selected.ma_5 ? formatKRW(selected.ma_5) : null },
                    { key: "ma_20", value: selected.ma_20 ? formatKRW(selected.ma_20) : null },
                    { key: "bb_upper", value: selected.bb_upper ? formatKRW(selected.bb_upper) : null },
                    { key: "bb_lower", value: selected.bb_lower ? formatKRW(selected.bb_lower) : null },
                    { key: "atr_14", value: selected.atr_14 ? formatKRW(selected.atr_14) : null },
                  ].filter((i) => i.value != null).map((item) => {
                    const desc = getIndicatorDesc(item.key);
                    return (
                      <DetailItem key={item.key} label={desc.label} value={item.value!} caption={desc.description} />
                    );
                  })}
                </div>
              </div>
            )}

            {selected.trigger_value != null && (
              <DetailItem label="트리거 값" value={formatKRW(selected.trigger_value)} />
            )}

            {selected.strategy_params_json && (
              <div style={{ borderTop: "1px solid var(--border)", margin: "16px 0", paddingTop: 16 }}>
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 12 }}>적용된 전략 파라미터</div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                  {Object.entries((() => { try { return JSON.parse(selected.strategy_params_json || "{}") || {}; } catch { return {}; } })() as Record<string, number>).map(([key, value]) => {
                    const desc = getParamDesc(selected.strategy, key);
                    return (
                      <div key={key}>
                        <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{desc.label}</div>
                        <div style={{ fontSize: 13, fontWeight: 600 }}>{value}</div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const filled = Math.round(value * 10);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ display: "flex", gap: 1 }}>
        {Array.from({ length: 10 }).map((_, i) => (
          <div
            key={i}
            style={{
              width: 4,
              height: 14,
              borderRadius: 1,
              background: i < filled
                ? pct >= 70 ? "#22c55e" : pct >= 40 ? "#eab308" : "#4a9eff"
                : "#2a2d3e",
            }}
          />
        ))}
      </div>
      <span style={{ fontSize: 11, color: "var(--text-muted)", minWidth: 32 }}>{pct}%</span>
    </div>
  );
}

function DetailItem({ label, value, full, caption }: { label: string; value: string; full?: boolean; caption?: string }) {
  return (
    <div style={full ? { gridColumn: "1 / -1" } : undefined}>
      <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 13 }}>{value}</div>
      {caption && <div style={{ fontSize: 10, color: "var(--text-muted)", marginTop: 2, lineHeight: 1.4 }}>{caption}</div>}
    </div>
  );
}
