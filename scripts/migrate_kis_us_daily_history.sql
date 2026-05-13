-- #396: KIS US 일일 매매 history 테이블 (자산 축적용)
--
-- 매일 종목별 1행 기록:
-- - bar1 패턴 (양봉/음봉/도지)
-- - 매수/매도 여부, 손익
-- - skip 이유 (도지, 갭 가드, 자금 부족 등)

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS kis_us_daily_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,            -- NY 거래일 "YYYY-MM-DD"
    ticker TEXT NOT NULL,
    bar1_pattern TEXT,                   -- "bullish" / "bearish" / "doji" / null (데이터 없음)
    bar1_body_pct REAL,                  -- bar1 |close-open|/open × 100
    signal_price REAL,                   -- bar1 close (시그널 가격)
    bought INTEGER NOT NULL DEFAULT 0,
    sold INTEGER NOT NULL DEFAULT 0,
    buy_price REAL,
    sell_price REAL,
    qty REAL,
    pnl_usd REAL,
    pnl_pct REAL,
    sell_type TEXT,                      -- "stop_loss" / "eod_profit" / "eod_loss" / null
    skip_reason TEXT,                    -- "도지" / "음봉" / "갭 가드 (-2.1%)" / "자금 부족" 등
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(trade_date, ticker)
);

CREATE INDEX IF NOT EXISTS idx_kis_us_daily_history_date
    ON kis_us_daily_history(trade_date DESC);

COMMIT;
