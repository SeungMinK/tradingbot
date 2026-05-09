import { useEffect, useState, useCallback } from "react";
import client from "../api/client";
import StatCard from "../components/StatCard";
import Pagination from "../components/Pagination";
import { formatDateTime, formatNumber } from "../utils/format";
import { cn } from "@/lib/utils";

interface NewsItem {
  id: number;
  source: string;
  title: string;
  summary: string;
  url: string;
  published_at: string;
  collected_at: string;
  category: string;
  coins_mentioned: string;
  sentiment_keyword: string;
}

interface NewsStats {
  total: number;
  positive: number;
  negative: number;
  neutral: number;
  coin_tagged: number;
  fear_greed: { value: number; classification: string; timestamp: string } | null;
}

const SENTIMENT_COLORS: Record<string, string> = {
  positive: "badge-green",
  negative: "badge-red",
  neutral: "badge-yellow",
};

const SENTIMENT_KR: Record<string, string> = {
  positive: "긍정",
  negative: "부정",
  neutral: "중립",
};

const CATEGORY_KR: Record<string, string> = {
  market: "시장",
  regulation: "규제",
  security: "보안",
  technology: "기술",
  listing: "상장",
  maintenance: "점검",
  delisting: "폐지",
};

const SOURCE_LABELS: Record<string, string> = {
  coindesk: "CoinDesk",
  cointelegraph: "CoinTelegraph",
  upbit: "업비트",
};

const FILTERS = [
  { label: "전체", value: "" },
  { label: "긍정", value: "positive" },
  { label: "부정", value: "negative" },
  { label: "중립", value: "neutral" },
] as const;

export default function NewsPage() {
  const [news, setNews] = useState<NewsItem[]>([]);
  const [stats, setStats] = useState<NewsStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState("");
  const [coinFilter, setCoinFilter] = useState("");

  const fetchData = useCallback(async () => {
    try {
      const params: Record<string, string | number> = { page, limit: 15 };
      if (filter) params.sentiment = filter;
      if (coinFilter) params.coin = coinFilter;

      const [newsRes, statsRes] = await Promise.all([
        client.get("/news", { params }).then((r) => r.data),
        client.get("/news/stats", { params: { hours: 24 } }).then((r) => r.data),
      ]);
      setNews(newsRes.items);
      setTotalPages(newsRes.pages);
      setTotal(newsRes.total);
      setStats(statsRes);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [page, filter, coinFilter]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // 5분 자동 갱신
  useEffect(() => {
    const interval = setInterval(fetchData, 300000);
    return () => clearInterval(interval);
  }, [fetchData]);

  if (loading) return <div className="loading">로딩 중...</div>;

  const fg = stats?.fear_greed;
  const fgColor = fg ? (fg.value <= 25 ? "negative" : fg.value >= 75 ? "positive" : "") : "";
  const fgLabel = fg ? `${fg.value} — ${fg.classification === "Extreme Fear" ? "극도 공포" : fg.classification === "Fear" ? "공포" : fg.classification === "Neutral" ? "중립" : fg.classification === "Greed" ? "탐욕" : "극도 탐욕"}` : "-";

  return (
    <div>
      <div className="page-header">
        <h1>뉴스</h1>
        <p>코인 시장 뉴스 + 공포/탐욕 지수 (30분 자동 수집)</p>
      </div>

      {/* Stats */}
      {stats && (
        <div className="kpi-grid">
          <StatCard label="공포/탐욕 지수" value={fgLabel} valueClass={fgColor} sub={fg ? formatDateTime(fg.timestamp) : ""} />
          <StatCard label="뉴스 (24h)" value={formatNumber(stats.total)} sub={`긍정 ${stats.positive} / 부정 ${stats.negative} / 중립 ${stats.neutral}`} />
          <StatCard
            label="시장 심리"
            value={stats.negative > stats.positive ? "부정적" : stats.positive > stats.negative ? "긍정적" : "중립"}
            valueClass={stats.negative > stats.positive ? "negative" : stats.positive > stats.negative ? "positive" : ""}
            sub={`긍정 ${((stats.positive / (stats.total || 1)) * 100).toFixed(0)}% / 부정 ${((stats.negative / (stats.total || 1)) * 100).toFixed(0)}%`}
          />
          <StatCard label="코인 언급" value={`${stats.coin_tagged}건`} sub={`전체 ${stats.total}건 중`} />
        </div>
      )}

      {/* Filters (#348 Tailwind) */}
      <div className="card mb-4">
        <div className="flex justify-between items-center flex-wrap gap-2">
          <div className="flex gap-1.5">
            {FILTERS.map((f) => (
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
          <div className="flex gap-1.5 items-center">
            <input
              placeholder="코인 검색 (BTC, ETH...)"
              value={coinFilter}
              onChange={(e) => { setCoinFilter(e.target.value.toUpperCase()); setPage(1); }}
              className="px-3 py-1.5 rounded-md border border-border bg-card text-foreground text-sm w-40"
            />
            <span className="text-xs text-muted-foreground">{formatNumber(total)}건</span>
          </div>
        </div>
      </div>

      {/* News List */}
      <div className="flex flex-col gap-3">
        {news.map((n) => (
          <div key={n.id} className="card p-4">
            <div className="flex justify-between items-start mb-2">
              <div className="flex-1">
                <a
                  href={n.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-base font-semibold text-foreground no-underline hover:text-primary"
                >
                  {n.title}
                </a>
              </div>
              <div className="flex gap-1 ml-3 shrink-0">
                <span className={`badge ${SENTIMENT_COLORS[n.sentiment_keyword] || "badge-yellow"}`}>
                  {SENTIMENT_KR[n.sentiment_keyword] || n.sentiment_keyword}
                </span>
                <span className="badge badge-blue">{CATEGORY_KR[n.category] || n.category}</span>
              </div>
            </div>

            {n.summary && (
              <p className="text-sm text-muted-foreground my-0 mb-2 leading-relaxed">
                {n.summary.length > 200 ? n.summary.slice(0, 200) + "..." : n.summary}
              </p>
            )}

            <div className="flex gap-3 items-center text-xs text-muted-foreground">
              <span>{SOURCE_LABELS[n.source] || n.source}</span>
              <span>{n.published_at ? formatDateTime(n.published_at) : "-"}</span>
              {n.coins_mentioned && (
                <div className="flex gap-1">
                  {n.coins_mentioned.split(",").map((c) => (
                    <span
                      key={c}
                      className="badge badge-purple text-[10px] cursor-pointer"
                      onClick={() => { setCoinFilter(c); setPage(1); }}
                    >
                      {c}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}

        {news.length === 0 && <div className="empty-state">뉴스 없음</div>}
      </div>

      {totalPages > 1 && (
        <div style={{ marginTop: 16 }}>
          <Pagination page={page} pages={totalPages} onPageChange={setPage} />
        </div>
      )}
    </div>
  );
}
