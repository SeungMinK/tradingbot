import { useEffect, useState, useCallback } from "react";
import { getTrades, getTradeStats } from "../api/trades";
import type { Trade, TradesResponse, TradeStats, TradeFiltersState } from "../types/trades";
import StatCard from "../components/StatCard";
import Pagination from "../components/Pagination";
import { formatKRW, formatPercent, formatDateTime } from "../utils/format";

function TradeDetailModal({ trade, onClose }: { trade: Trade; onClose: () => void }) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>매매 상세 #{trade.id}</h2>
        <div className="grid grid-cols-2 gap-3">
          {[
            ["시간", formatDateTime(trade.timestamp)],
            ["종목", trade.coin],
            ["방향", trade.side === "buy" ? "매수" : "매도"],
            ["가격", formatKRW(trade.price)],
            ["수량", trade.amount.toFixed(8)],
            ["총액", formatKRW(trade.total_krw)],
            ["수수료", formatKRW(trade.fee_krw)],
            ["전략", trade.strategy],
            ["시장 상태", trade.market_state_at_trade],
            ["BTC 가격", formatKRW(trade.btc_price_at_trade)],
            ["RSI", trade.rsi_at_trade?.toFixed(1) ?? "-"],
            ["K 값", trade.param_k_value?.toString() ?? "-"],
            ["손절", trade.param_stop_loss ? formatPercent(trade.param_stop_loss * 100) : "-"],
            ["트레일링 스탑", trade.param_trailing_stop ? formatPercent(trade.param_trailing_stop * 100) : "-"],
            ["수익률", trade.profit_pct != null ? formatPercent(trade.profit_pct) : "-"],
            ["수익금", trade.profit_krw != null ? formatKRW(trade.profit_krw) : "-"],
            ["보유 시간", trade.hold_duration_minutes != null ? `${trade.hold_duration_minutes}분` : "-"],
            ["트리거", trade.trigger_reason],
          ].map(([label, value]) => (
            <div key={label}>
              <div className="text-xs text-muted-foreground">{label}</div>
              <div className="text-sm">{value}</div>
            </div>
          ))}
        </div>
        <div className="modal-actions">
          <button className="btn" onClick={onClose}>닫기</button>
        </div>
      </div>
    </div>
  );
}

export default function TradesPage() {
  const [data, setData] = useState<TradesResponse | null>(null);
  const [stats, setStats] = useState<TradeStats | null>(null);
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState<TradeFiltersState>({
    coin: "", side: "", strategy: "", market: "", date_from: "", date_to: "",
  });
  const [selectedTrade, setSelectedTrade] = useState<Trade | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchTrades = useCallback(async () => {
    setLoading(true);
    try {
      const result = await getTrades({
        page,
        limit: 20,
        coin: filters.coin || undefined,
        side: filters.side || undefined,
        strategy: filters.strategy || undefined,
        market: filters.market || undefined,
        date_from: filters.date_from || undefined,
        date_to: filters.date_to || undefined,
      });
      setData(result);
    } finally {
      setLoading(false);
    }
  }, [page, filters]);

  useEffect(() => {
    fetchTrades();
  }, [fetchTrades]);

  useEffect(() => {
    getTradeStats(30).then(setStats).catch(() => null);
  }, []);

  const handleFilterChange = (key: keyof TradeFiltersState, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(1);
  };

  return (
    <div>
      <div className="page-header">
        <h1>매매 내역</h1>
        <p>전체 매매 기록 조회 및 분석</p>
      </div>

      {/* Stats */}
      {stats && (
        <div className="kpi-grid">
          <StatCard label="총 거래" value={stats.total_trades.toString()} sub={`매수 ${stats.buys} / 매도 ${stats.sells}`} />
          <StatCard label="승률" value={formatPercent(stats.win_rate).replace("+", "")} valueClass={stats.win_rate >= 50 ? "positive" : "negative"} sub={`승 ${stats.wins} / 패 ${stats.losses}`} />
          <StatCard
            label="매도 수익 합계"
            value={formatKRW(stats.total_profit_krw)}
            valueClass={stats.total_profit_krw >= 0 ? "positive" : "negative"}
            sub="매도 완료 건 (수수료 포함)"
          />
          <StatCard
            label="총 수수료"
            value={formatKRW(stats.total_fees)}
            sub="매수+매도 수수료 합계"
          />
        </div>
      )}

      {/* Filters */}
      <div className="filters-bar">
        <div className="filter-group">
          <label>기간 (시작)</label>
          <input type="date" value={filters.date_from} onChange={(e) => handleFilterChange("date_from", e.target.value)} />
        </div>
        <div className="filter-group">
          <label>기간 (종료)</label>
          <input type="date" value={filters.date_to} onChange={(e) => handleFilterChange("date_to", e.target.value)} />
        </div>
        <div className="filter-group">
          <label>시장</label>
          <select value={filters.market} onChange={(e) => handleFilterChange("market", e.target.value)}>
            <option value="">전체</option>
            <option value="upbit">🪙 코인 (Upbit)</option>
            <option value="kis_kr">🇰🇷 한국주식</option>
            <option value="kis_us">🇺🇸 미국주식</option>
          </select>
        </div>
        <div className="filter-group">
          <label>방향</label>
          <select value={filters.side} onChange={(e) => handleFilterChange("side", e.target.value)}>
            <option value="">전체</option>
            <option value="buy">매수</option>
            <option value="sell">매도</option>
          </select>
        </div>
        <div className="filter-group">
          <label>종목</label>
          <input type="text" placeholder="KRW-BTC" value={filters.coin} onChange={(e) => handleFilterChange("coin", e.target.value)} className="w-[120px]" />
        </div>
        <div className="filter-group">
          <label>전략</label>
          <input type="text" placeholder="전략명" value={filters.strategy} onChange={(e) => handleFilterChange("strategy", e.target.value)} className="w-[140px]" />
        </div>
      </div>

      {/* Table */}
      <div className="card">
        {loading ? (
          <div className="loading">로딩 중...</div>
        ) : data && data.items.length > 0 ? (
          <>
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>시간</th>
                    <th>종목</th>
                    <th>방향</th>
                    <th>가격</th>
                    <th>수량</th>
                    <th>금액</th>
                    <th>전략</th>
                    <th>신뢰도</th>
                    <th>수익률</th>
                    <th>순수익</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((t: any) => {
                    const isKis = t.market === "kis_us" || t.market === "kis_kr";
                    const marketIcon = t.market === "kis_us" ? "🇺🇸" : t.market === "kis_kr" ? "🇰🇷" : "🪙";
                    const priceFormatted = t.market === "kis_us"
                      ? `$${Number(t.price).toFixed(2)}`
                      : formatKRW(t.price);
                    return (
                    <tr key={t.id} style={{ cursor: "pointer" }} onClick={() => setSelectedTrade(t)}>
                      <td style={{ fontSize: 12 }}>{formatDateTime(t.timestamp)}</td>
                      <td>
                        <span style={{ marginRight: 4, fontSize: 11 }}>{marketIcon}</span>
                        {t.coin}
                      </td>
                      <td>
                        <span className={`badge ${t.side === "buy" ? "badge-green" : "badge-red"}`}>
                          {t.side === "buy" ? "매수" : "매도"}
                        </span>
                      </td>
                      <td>{priceFormatted}</td>
                      <td>{isKis ? Number(t.amount).toFixed(t.amount === Math.floor(t.amount) ? 0 : 4) : t.amount.toFixed(8)}</td>
                      <td>{formatKRW(t.total_krw)}</td>
                      <td><span className="badge badge-purple">{t.strategy}</span></td>
                      <td style={{ fontSize: 12 }}>
                        {t.signal_confidence != null ? `${(t.signal_confidence * 100).toFixed(1)}%` : "-"}
                      </td>
                      <td className={t.profit_pct != null ? (t.profit_pct >= 0 ? "positive" : "negative") : ""}>
                        {t.profit_pct != null ? formatPercent(t.profit_pct) : "-"}
                      </td>
                      <td className={t.profit_krw != null ? (t.profit_krw >= 0 ? "positive" : "negative") : ""}>
                        {t.profit_krw != null ? formatKRW(t.profit_krw) : "-"}
                      </td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <Pagination page={data.page} pages={data.pages} onPageChange={setPage} />
          </>
        ) : (
          <div className="empty-state">매매 내역이 없습니다</div>
        )}
      </div>

      {selectedTrade && (
        <TradeDetailModal trade={selectedTrade} onClose={() => setSelectedTrade(null)} />
      )}
    </div>
  );
}
