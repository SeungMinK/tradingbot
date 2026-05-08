"""SQLite 데이터베이스 관리 모듈.

NestJS의 TypeOrmModule + Repository 패턴과 비슷한 역할.
다만 ORM 없이 직접 SQL을 작성한다.
"""

import logging
import sqlite3
from pathlib import Path

from cryptobot.exceptions import DatabaseError

logger = logging.getLogger(__name__)

# 테이블 생성 SQL
_SCHEMA = """
CREATE TABLE IF NOT EXISTS ohlcv_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coin TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'upbit',
    date DATE NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    collected_at DATETIME NOT NULL,
    UNIQUE(coin, date)
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    coin TEXT NOT NULL DEFAULT 'KRW-BTC',
    market TEXT NOT NULL DEFAULT 'upbit',
    price REAL NOT NULL,
    open_24h REAL,
    high_24h REAL,
    low_24h REAL,
    change_pct_24h REAL,
    volume_24h REAL,
    trade_count_24h INTEGER,
    rsi_14 REAL,
    ma_5 REAL,
    ma_20 REAL,
    ma_60 REAL,
    bb_upper REAL,
    bb_lower REAL,
    atr_14 REAL,
    total_market_volume_krw REAL,
    top10_avg_change_pct REAL,
    market_state TEXT,
    volatility_level TEXT
);

CREATE TABLE IF NOT EXISTS trade_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    coin TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'upbit',
    signal_type TEXT NOT NULL,
    strategy TEXT NOT NULL,
    confidence REAL,
    trigger_reason TEXT,
    trigger_value REAL,
    current_price REAL,
    target_price REAL,
    executed BOOLEAN DEFAULT FALSE,
    trade_id INTEGER,
    skip_reason TEXT,
    snapshot_id INTEGER,
    strategy_params_json TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    coin TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'upbit',
    side TEXT NOT NULL,
    price REAL NOT NULL,
    amount REAL NOT NULL,
    total_krw REAL NOT NULL,
    fee_krw REAL NOT NULL,
    strategy TEXT NOT NULL,
    trigger_reason TEXT,
    trigger_value REAL,
    param_k_value REAL,
    param_stop_loss REAL,
    param_trailing_stop REAL,
    market_state_at_trade TEXT,
    btc_price_at_trade REAL,
    rsi_at_trade REAL,
    buy_trade_id INTEGER,
    profit_pct REAL,
    profit_krw REAL,
    hold_duration_minutes INTEGER,
    strategy_params_json TEXT,
    strategy_selection_reason TEXT,
    order_uuid TEXT,
    reconciled INTEGER DEFAULT 0,
    reconciled_at DATETIME,
    FOREIGN KEY (buy_trade_id) REFERENCES trades(id)
);

CREATE TABLE IF NOT EXISTS strategy_params (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source TEXT NOT NULL,
    k_value REAL NOT NULL,
    stop_loss_pct REAL NOT NULL,
    trailing_stop_pct REAL NOT NULL,
    max_positions INTEGER NOT NULL,
    position_size_pct REAL,
    allow_trading BOOLEAN NOT NULL DEFAULT TRUE,
    market_state TEXT,
    aggression REAL,
    llm_reasoning TEXT,
    llm_news_summary TEXT,
    llm_model TEXT,
    period_trade_count INTEGER,
    period_win_rate REAL,
    period_total_pnl_pct REAL
);

CREATE TABLE IF NOT EXISTS daily_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL,
    market TEXT NOT NULL DEFAULT 'upbit',
    starting_balance_krw REAL,
    ending_balance_krw REAL,
    total_asset_value_krw REAL,
    realized_pnl_krw REAL,
    unrealized_pnl_krw REAL,
    daily_return_pct REAL,
    cumulative_return_pct REAL,
    total_trades INTEGER,
    buy_trades INTEGER,
    sell_trades INTEGER,
    winning_trades INTEGER,
    losing_trades INTEGER,
    win_rate REAL,
    avg_profit_pct REAL,
    avg_loss_pct REAL,
    max_drawdown_pct REAL,
    total_fees_krw REAL,
    active_param_id INTEGER,
    market_state TEXT,
    UNIQUE(date, market),
    FOREIGN KEY (active_param_id) REFERENCES strategy_params(id)
);

CREATE TABLE IF NOT EXISTS strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    description TEXT,
    category TEXT NOT NULL,
    market_states TEXT NOT NULL,
    timeframe TEXT,
    difficulty TEXT,
    default_params_json TEXT,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    status TEXT NOT NULL DEFAULT 'inactive',
    is_available BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS strategy_activations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    strategy_name TEXT NOT NULL,
    action TEXT NOT NULL,
    source TEXT NOT NULL,
    market_state TEXT,
    reason TEXT,
    previous_strategy TEXT,
    performance_at_switch_json TEXT,
    FOREIGN KEY (strategy_name) REFERENCES strategies(name)
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_login_at DATETIME
);

CREATE TABLE IF NOT EXISTS prompt_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    description TEXT,
    is_active BOOLEAN DEFAULT FALSE,
    created_at DATETIME NOT NULL,
    activated_at DATETIME,
    deactivated_at DATETIME
);

CREATE TABLE IF NOT EXISTS llm_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    model TEXT NOT NULL,
    input_news_count INTEGER,
    input_news_summary TEXT,
    input_market_snapshot_id INTEGER,
    input_recent_trades_count INTEGER,
    input_recent_win_rate REAL,
    output_raw_json TEXT,
    output_market_state TEXT,
    output_aggression REAL,
    output_allow_trading BOOLEAN,
    output_k_value REAL,
    output_stop_loss REAL,
    output_trailing_stop REAL,
    output_reasoning TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    evaluation_period_pnl_pct REAL,
    evaluation_was_good BOOLEAN,
    -- before/after 스냅샷 (#171). input_news_summary 재사용하던 방식 개선
    before_snapshot_json TEXT,
    after_snapshot_json TEXT,
    -- Prompt Caching 토큰 집계 (#183). hit rate 모니터링용
    cache_creation_tokens INTEGER,
    cache_read_tokens INTEGER,
    FOREIGN KEY (input_market_snapshot_id) REFERENCES market_snapshots(id)
);

CREATE TABLE IF NOT EXISTS news_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    url TEXT,
    published_at DATETIME,
    collected_at DATETIME NOT NULL,
    category TEXT,
    coins_mentioned TEXT,
    sentiment_keyword TEXT,
    is_processed BOOLEAN DEFAULT FALSE,
    -- LLM 시장 판단 정밀도 향상 (#154)
    impact_score INTEGER,  -- 0~10: 시장 영향 크기
    scope TEXT             -- "macro" (거시/규제/Fed) / "micro" (개별 프로젝트/기업)
);

CREATE TABLE IF NOT EXISTS fear_greed_index (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME NOT NULL,
    value INTEGER NOT NULL,
    classification TEXT,
    collected_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    value_type TEXT NOT NULL DEFAULT 'string',
    category TEXT NOT NULL DEFAULT 'general',
    display_name TEXT NOT NULL,
    description TEXT,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS coin_strategy_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL UNIQUE,
    strategy_name TEXT NOT NULL,
    stop_loss_pct REAL NOT NULL DEFAULT -5.0,
    trailing_stop_pct REAL NOT NULL DEFAULT -3.0,
    position_size_pct REAL NOT NULL DEFAULT 100.0,
    strategy_params_json TEXT,
    description TEXT,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 코인별 전략 배정 (#152). LLM이 coin_strategies dict로 지정.
-- coin_strategy_config(카테고리 기반 기본값)과 별개.
CREATE TABLE IF NOT EXISTS coin_strategy_assignment (
    coin TEXT PRIMARY KEY,
    strategy_name TEXT NOT NULL,
    params_json TEXT,
    assigned_by TEXT NOT NULL DEFAULT 'llm',  -- 'llm' | 'manual' | 'backtest'
    reason TEXT,
    assigned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date DATE NOT NULL,
    strategy_name TEXT NOT NULL,
    coin TEXT NOT NULL,
    period TEXT NOT NULL,
    num_trades INTEGER NOT NULL,
    win_rate REAL NOT NULL,
    total_return_pct REAL NOT NULL,
    max_drawdown_pct REAL NOT NULL,
    sharpe_ratio REAL NOT NULL,
    avg_profit_pct REAL NOT NULL,
    avg_loss_pct REAL NOT NULL,
    best_trade_pct REAL NOT NULL,
    worst_trade_pct REAL NOT NULL,
    params_json TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_bt_run_date ON backtest_results(run_date, strategy_name, coin);

-- #216: 분봉 OHLCV. 일봉 ATR/ADX는 봇의 실제 동작 주기(분 단위)와 미스매치 →
-- 분봉으로 재측정해 변동성/추세 지표 정밀화. 5분봉 기본 (200캔들 ≈ 17시간치).
CREATE TABLE IF NOT EXISTS ohlcv_minutes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    coin TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'upbit',
    interval_min INTEGER NOT NULL DEFAULT 5,  -- 1, 5, 15, 60
    timestamp DATETIME NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    collected_at DATETIME NOT NULL,
    UNIQUE(coin, interval_min, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_ohlcv_minutes_lookup ON ohlcv_minutes(coin, interval_min, timestamp DESC);

-- #206: 추가 입금 추적. 잔고 검증이 첫 starting_balance만 "총 입금액"으로 보던 한계 해결.
CREATE TABLE IF NOT EXISTS capital_deposits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    currency TEXT NOT NULL DEFAULT 'KRW',
    amount_krw REAL NOT NULL,
    deposited_at DATETIME NOT NULL,
    source TEXT NOT NULL DEFAULT 'api',           -- 'api' | 'manual' | 'initial'
    upbit_uuid TEXT UNIQUE,                       -- 업비트 입금 uuid (중복 방지)
    note TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_capital_deposits_at ON capital_deposits(deposited_at DESC);

-- #276: 시장별 자본 입출금 이력 (KIS 한국/미국 시드 동적 관리).
-- amount_krw 양수=입금, 음수=출금. 사용자가 한투 계좌에 KRW 입금 후 시장별 분배 시 명시.
CREATE TABLE IF NOT EXISTS market_capital_deposits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    amount_krw REAL NOT NULL,
    deposited_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source TEXT NOT NULL DEFAULT 'manual',
    note TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_market_capital_market_at ON market_capital_deposits(market, deposited_at DESC);

-- #293: KIS 미국주식 종목 풀 (DB 기반, admin 관리 가능).
-- enabled=TRUE 종목만 봇이 모니터링/매매. 후보는 enabled=FALSE로 유지.
CREATE TABLE IF NOT EXISTS kis_us_symbols (
    ticker TEXT PRIMARY KEY,
    display_name TEXT,
    exchange TEXT NOT NULL DEFAULT 'NASD',          -- NASD/NYSE/AMEX
    is_integer_only BOOLEAN NOT NULL DEFAULT FALSE, -- 정수 매매만 (레버리지 ETF 등)
    category TEXT,                                   -- bigtech/semi/leveraged/crypto/ev/ai/etf
    enabled BOOLEAN NOT NULL DEFAULT FALSE,          -- 활성 (모니터링 풀 포함 여부)
    note TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_kis_us_symbols_enabled ON kis_us_symbols(enabled);

-- #297-2: 매 틱 매수 판단 결과 기록 (사용자 가시성).
-- 봇이 30초마다 평가하는 내용을 DB에 저장하면 admin에서 "왜 아직 안 샀는지" 확인 가능.
CREATE TABLE IF NOT EXISTS kis_us_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ticker TEXT NOT NULL,
    price REAL,
    rsi REAL,
    ma20 REAL,
    ma60 REAL,
    should_buy BOOLEAN NOT NULL DEFAULT 0,
    reason TEXT,                    -- "RSI 42.0>35" 같은 미충족 사유 또는 매수 신호 사유
    confidence REAL DEFAULT 0,
    holds_already BOOLEAN DEFAULT 0  -- 보유 중이라 매도 평가만 한 경우
);
CREATE INDEX IF NOT EXISTS idx_kis_us_eval_ts ON kis_us_evaluations(evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_kis_us_eval_ticker_ts ON kis_us_evaluations(ticker, evaluated_at DESC);

-- #240: 페이지 방문자 추적 (admin 우선)
CREATE TABLE IF NOT EXISTS page_visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visited_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    session_id TEXT,
    ip_hash TEXT,
    user_agent TEXT,
    page TEXT,
    is_unique BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_visits_at ON page_visits(visited_at DESC);
CREATE INDEX IF NOT EXISTS idx_visits_session ON page_visits(session_id);

-- #245 멀티 마켓 인덱스: market 컬럼 마이그레이션 *후* 생성 (initialize 마이그레이션 블록에서)
"""

# 기본 전략 파라미터 (최초 1회 삽입)
_DEFAULT_PARAMS = """
INSERT INTO strategy_params (
    source, k_value, stop_loss_pct, trailing_stop_pct,
    max_positions, position_size_pct, allow_trading, market_state, aggression
) VALUES (
    'default', 0.5, -5.0, -3.0,
    1, 100.0, TRUE, 'sideways', 0.5
);
"""

# 전략 마스터 데이터
_DEFAULT_STRATEGIES = [
    {
        "name": "volatility_breakout",
        "display_name": "변동성 돌파",
        "description": "시가 + 전일 변동폭 × K 돌파 시 매수. 래리 윌리엄스의 단기 전략.",
        "category": "volatility",
        "market_states": "bullish",
        "timeframe": "1d",
        "difficulty": "easy",
        "default_params_json": '{"k_value": 0.5}',
        "is_active": False,
    },
    {
        "name": "ma_crossover",
        "display_name": "이동평균 교차",
        "description": "단기 MA가 장기 MA를 돌파하면 매수/매도. 가장 고전적인 추세 추종 전략.",
        "category": "trend",
        "market_states": "bullish,bearish",
        "timeframe": "1d",
        "difficulty": "easy",
        "default_params_json": '{"short_period": 5, "long_period": 20}',
        "is_active": False,
    },
    {
        "name": "macd",
        "display_name": "MACD",
        "description": "MACD-시그널 라인 교차로 추세 강도와 방향 판단.",
        "category": "trend",
        "market_states": "bullish,bearish",
        "timeframe": "1d",
        "difficulty": "easy",
        "default_params_json": '{"fast": 12, "slow": 26, "signal_period": 9}',
        "is_active": False,
    },
    {
        "name": "supertrend",
        "display_name": "슈퍼트렌드",
        "description": "ATR 기반 동적 지지/저항선으로 추세 추종. 변동성 적응형.",
        "category": "trend",
        "market_states": "bullish,bearish",
        "timeframe": "1d",
        "difficulty": "medium",
        "default_params_json": '{"st_period": 10, "st_multiplier": 3.0}',
        "is_active": False,
    },
    {
        "name": "rsi_mean_reversion",
        "display_name": "RSI 평균 회귀",
        "description": "RSI 과매도 반등 매수, 과매수 하락 매도. 횡보장 전용.",
        "category": "mean_reversion",
        "market_states": "sideways",
        "timeframe": "1h",
        "difficulty": "easy",
        "default_params_json": '{"rsi_period": 14, "oversold": 30, "overbought": 70}',
        "is_active": False,
    },
    {
        "name": "bollinger_bands",
        "display_name": "볼린저 밴드",
        "description": "밴드 이탈 후 복귀 시 반전 진입. 횡보장에서 높은 승률.",
        "category": "mean_reversion",
        "market_states": "sideways",
        "timeframe": "1h",
        "difficulty": "easy",
        "default_params_json": '{"bb_period": 20, "bb_std": 2.0}',
        "is_active": False,
    },
    {
        "name": "grid_trading",
        "display_name": "그리드 트레이딩",
        "description": "가격 범위를 격자로 나누어 분할 매수/매도. 추세 예측 불필요.",
        "category": "grid",
        "market_states": "sideways",
        "timeframe": "1h",
        "difficulty": "medium",
        "default_params_json": '{"grid_count": 10, "range_pct": 10.0}',
        "is_active": False,
    },
    {
        "name": "breakout_momentum",
        "display_name": "브레이크아웃 모멘텀",
        "description": "N일 최고가 돌파 매수. 터틀 트레이딩 핵심 전략.",
        "category": "momentum",
        "market_states": "bullish,sideways",
        "timeframe": "1d",
        "difficulty": "easy",
        "default_params_json": '{"entry_period": 20, "exit_period": 10}',
        "is_active": False,
    },
    {
        "name": "bollinger_squeeze",
        "display_name": "볼린저 스퀴즈",
        "description": "밴드 수축 후 폭발적 움직임 포착. 횡보→추세 전환 구간.",
        "category": "volatility",
        "market_states": "sideways,bullish",
        "timeframe": "1d",
        "difficulty": "medium",
        "default_params_json": '{"bb_period": 20, "bb_std": 2.0, "squeeze_lookback": 120}',
        "is_active": False,
    },
    {
        "name": "bb_rsi_combined",
        "display_name": "볼린저+RSI 복합",
        "description": "RSI 과매도 + 볼린저 하단 이탈 동시 충족 시 매수. 거짓 신호 감소로 60%+ 승률.",
        "category": "mean_reversion",
        "market_states": "sideways,bearish",
        "timeframe": "1d",
        "difficulty": "medium",
        "default_params_json": (
            '{"bb_period": 20, "bb_std": 2.0, "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70}'
        ),
        "is_active": True,  # #197: 신규 DB 기본 전략 (운영 의도 반영)
    },
    {
        # #226: 진득한 스윙 트레이딩. 잦은 매매로 인한 수수료 적자(EV +0.058% < 수수료 0.1%) 해소 목적.
        # 평균 보유 10~16일, 월 1~3건. BTC/XRP 백테스트에서 Buy&Hold 대비 +10~25%.
        "name": "long_term_swing",
        "display_name": "장기 스윙",
        "description": "공포탐욕지수+50일MA+RSI 결합. 저점 매수, 고점 매도. 평균 보유 10~16일.",
        "category": "swing",
        "market_states": "bearish,sideways",
        "timeframe": "1d",
        "difficulty": "medium",
        "default_params_json": (
            '{"ma_long": 50, "ma_short": 20, "rsi_period": 14, "rsi_entry_max": 45,'
            ' "fear_threshold": 30, "greed_threshold": 70, "take_profit_pct": 20.0, "min_hold_days": 7}'
        ),
        "is_active": False,  # 기본 비활성, 사용자 평가 후 활성화
    },
]

# 봇 설정 기본값
_DEFAULT_COIN_STRATEGY = [
    {
        "category": "core",
        "strategy_name": "rsi_mean_reversion",
        "stop_loss_pct": -5.0,
        "trailing_stop_pct": -3.0,
        "position_size_pct": 50.0,
        "strategy_params_json": '{"rsi_period": 14, "oversold": 35, "overbought": 70}',
        "description": "대형코인 (BTC/ETH/XRP) — 변동 적으므로 RSI 과매도 반등 전략. 보수적 운용.",
    },
    {
        "category": "alt",
        "strategy_name": "volatility_breakout",
        "stop_loss_pct": -3.0,
        "trailing_stop_pct": -2.0,
        "position_size_pct": 100.0,
        "strategy_params_json": '{"k_value": 0.3}',
        "description": "알트코인 — 변동 크므로 변동성 돌파 전략. 공격적 진입 + 빡빡한 스탑.",
    },
]

_DEFAULT_BOT_CONFIG = [
    {
        "key": "slack_tick_report",
        "value": "false",
        "value_type": "bool",
        "category": "notification",
        "display_name": "틱별 판단 리포트",
        "description": "매 스케줄러 실행 시 매수/매도/HOLD 판단 근거를 Slack으로 발송",
    },
    {
        "key": "slack_trade_notification",
        "value": "true",
        "value_type": "bool",
        "category": "notification",
        "display_name": "매매 체결 알림",
        "description": "매수/매도 체결 시 Slack 알림 발송",
    },
    {
        "key": "slack_daily_report",
        "value": "true",
        "value_type": "bool",
        "category": "notification",
        "display_name": "일일 정산 리포트",
        "description": "자정에 일일 매매 성과를 Slack으로 발송",
    },
    {
        "key": "tick_interval_seconds",
        "value": "30",
        "value_type": "int",
        "category": "bot",
        "display_name": "판단 주기 (초)",
        "description": "매매 신호 판단 간격. 너무 짧으면 API 호출 제한에 걸릴 수 있음",
    },
    {
        "key": "position_size_pct",
        "value": "100",
        "value_type": "float",
        "category": "risk",
        "display_name": "포지션 크기 (%)",
        "description": "가용 잔고 대비 최대 매수 비율. 50이면 잔고의 50%까지만 매수",
    },
    {
        "key": "stop_loss_pct",
        "value": "-5.0",
        "value_type": "float",
        "category": "risk",
        "display_name": "손절률 (%)",
        "description": "매수가 대비 이 비율만큼 하락하면 자동 매도",
    },
    {
        "key": "trailing_stop_pct",
        "value": "-3.0",
        "value_type": "float",
        "category": "risk",
        "display_name": "트레일링 스탑 (%)",
        "description": "최고가 대비 이 비율만큼 하락하면 자동 매도",
    },
    {
        "key": "max_daily_trades",
        "value": "10",
        "value_type": "int",
        "category": "risk",
        "display_name": "일일 최대 거래 횟수",
        "description": "하루에 이 횟수 이상 거래하면 매매 중단",
    },
    {
        "key": "max_daily_loss_pct",
        "value": "-10.0",
        "value_type": "float",
        "category": "risk",
        "display_name": "일일 최대 손실률 (%)",
        "description": "일일 누적 손실이 이 비율을 초과하면 매매 중단",
    },
    {
        "key": "max_consecutive_losses",
        "value": "3",
        "value_type": "int",
        "category": "risk",
        "display_name": "연속 손실 허용 횟수",
        "description": "연속으로 이 횟수만큼 손실 시 매매 중단",
    },
    {
        "key": "strategy_switch_delay_seconds",
        "value": "30",
        "value_type": "int",
        "category": "bot",
        "display_name": "전략 전환 대기 시간 (초)",
        "description": "새 전략 활성화 시 기존 전략이 종료되기까지 대기하는 시간",
    },
    {
        "key": "multi_coin_enabled",
        "value": "true",
        "value_type": "bool",
        "category": "coin",
        "display_name": "멀티코인 모드",
        "description": "여러 코인을 동시에 모니터링하고 매매. false면 BTC만 매매.",
    },
    {
        "key": "max_coins",
        "value": "5",
        "value_type": "int",
        "category": "coin",
        "display_name": "동시 모니터링 코인 수",
        "description": "자동 선별할 최대 코인 수. 거래량 상위 N개 선정.",
    },
    {
        "key": "max_position_per_coin_pct",
        "value": "50",
        "value_type": "float",
        "category": "coin",
        "display_name": "1종목당 최대 포지션 (%)",
        "description": "전체 잔고 대비 1종목에 투자할 수 있는 최대 비율.",
    },
    {
        "key": "coin_refresh_interval_minutes",
        "value": "30",
        "value_type": "int",
        "category": "coin",
        "display_name": "코인 목록 갱신 주기 (분)",
        "description": "자동 선별 코인 목록을 갱신하는 주기.",
    },
    # #228: 메이저 화이트리스트 — 알트(NEWT 등) 마구잡이 매매로 인한 큰 손실 차단.
    # 실측 데이터: NEWT 한 종목만 -31,056원 (한 달 손해의 1.5배).
    # 기본 ON. 끄면 기존 scanner + LLM add 동작.
    {
        "key": "coin_whitelist_enabled",
        "value": "true",
        "value_type": "bool",
        "category": "coin",
        "display_name": "코인 화이트리스트 모드",
        "description": "ON: 화이트리스트 코인만 매매. OFF: scanner + LLM 자동 선별 (기존).",
    },
    {
        "key": "coin_whitelist",
        "value": "KRW-BTC,KRW-ETH,KRW-XRP,KRW-SOL,KRW-ADA,KRW-DOGE,KRW-AVAX,KRW-LINK",
        "value_type": "string",
        "category": "coin",
        "display_name": "화이트리스트 코인 (CSV)",
        "description": "화이트리스트 모드에서 매매 허용 코인. T1: BTC/ETH/XRP/SOL, T2: ADA/DOGE/AVAX/LINK. 백테스트 +10.94%.",
    },
    {
        "key": "min_volume_krw",
        "value": "1000000000",
        "value_type": "float",
        "category": "coin",
        "display_name": "최소 거래대금 (원)",
        "description": "24시간 거래대금이 이 값 이상인 코인만 선별. 기본 10억원.",
    },
    {
        "key": "min_price_krw",
        "value": "1000",
        "value_type": "float",
        "category": "coin",
        "display_name": "최소 가격 (원)",
        "description": "현재가가 이 값 이상인 코인만 선별.",
    },
    {
        "key": "llm_add_coins",
        "value": "[]",
        "value_type": "string",
        "category": "coin",
        "display_name": "LLM 추천 추가 코인",
        "description": "LLM이 추천한 추가 모니터링 코인 목록 (JSON 배열).",
    },
    {
        "key": "llm_remove_coins",
        "value": "[]",
        "value_type": "string",
        "category": "coin",
        "display_name": "LLM 추천 제거 코인",
        "description": "LLM이 추천한 모니터링 제외 코인 목록 (JSON 배열).",
    },
    {
        "key": "k_value",
        "value": "0.5",
        "value_type": "float",
        "category": "strategy",
        "display_name": "K 값 (변동성 돌파)",
        "description": "변동성 돌파 전략의 K 계수. 높을수록 보수적 (0.0~1.0)",
    },
    {
        "key": "allow_trading",
        "value": "true",
        "value_type": "bool",
        "category": "bot",
        "display_name": "매매 허용",
        "description": "false로 설정하면 봇이 신호만 기록하고 실제 매매는 하지 않음",
    },
    # #230: LLM 비활성 토글. 한 달 데이터(활성 -31,676 vs 비활성 +8,640)로 LLM이 수익률에
    # 도움 안 됨 확인. 기본 false. 다시 켜려면 true + Anthropic API 결제.
    {
        "key": "llm_enabled",
        "value": "false",
        "value_type": "bool",
        "category": "llm",
        "display_name": "LLM 분석 활성",
        "description": "false면 _should_run에서 즉시 차단. 호출 0건. Anthropic 비용 0.",
    },
]


class Database:
    """SQLite 데이터베이스 연결 관리.

    NestJS에서 TypeOrmModule.forRoot()로 DB 연결하는 것과 동일한 역할.
    with 구문으로 사용하면 자동으로 커넥션을 닫는다.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def connection(self) -> sqlite3.Connection:
        """현재 DB 커넥션을 반환한다. 없으면 생성."""
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False, timeout=30)
            self._conn.row_factory = sqlite3.Row  # dict처럼 접근 가능
            self._conn.execute("PRAGMA journal_mode=WAL")  # 동시 읽기 성능 향상
            self._conn.execute("PRAGMA busy_timeout=30000")  # 30초 대기 (동시 시작 대응)
            self._conn.execute("PRAGMA foreign_keys=OFF")
        return self._conn

    def initialize(self) -> None:
        """테이블 생성 및 기본 데이터 삽입."""
        try:
            conn = self.connection
            conn.executescript(_SCHEMA)

            # 마이그레이션: strategies에 status 컬럼 추가 (기존 DB 호환)
            try:
                conn.execute("SELECT status FROM strategies LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE strategies ADD COLUMN status TEXT NOT NULL DEFAULT 'inactive'")
                conn.execute("UPDATE strategies SET status = 'active' WHERE is_active = TRUE")
                conn.execute("UPDATE strategies SET status = 'inactive' WHERE is_active = FALSE")
                logger.info("strategies 테이블에 status 컬럼 추가 완료")

            # 마이그레이션: market_snapshots AUTOINCREMENT 확인
            try:
                idx = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='market_snapshots'"
                ).fetchone()
                if idx and "AUTOINCREMENT" not in (idx[0] or ""):
                    # 컬럼을 명시적으로 매핑하여 재생성 (SELECT * 사용 금지 — 컬럼 밀림 방지)
                    conn.executescript("""
                        CREATE TABLE IF NOT EXISTS market_snapshots_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            coin TEXT NOT NULL DEFAULT 'KRW-BTC',
                            price REAL NOT NULL,
                            open_24h REAL, high_24h REAL, low_24h REAL,
                            change_pct_24h REAL, volume_24h REAL,
                            trade_count_24h INTEGER,
                            rsi_14 REAL, ma_5 REAL, ma_20 REAL, ma_60 REAL,
                            bb_upper REAL, bb_lower REAL, atr_14 REAL,
                            total_market_volume_krw REAL, top10_avg_change_pct REAL,
                            market_state TEXT, volatility_level TEXT
                        );
                        INSERT INTO market_snapshots_new (
                            id, timestamp, coin, price, open_24h, high_24h, low_24h,
                            change_pct_24h, volume_24h, trade_count_24h,
                            rsi_14, ma_5, ma_20, ma_60,
                            bb_upper, bb_lower, atr_14,
                            total_market_volume_krw, top10_avg_change_pct, market_state, volatility_level
                        ) SELECT
                            id, timestamp, coin, price, open_24h, high_24h, low_24h,
                            change_pct_24h, volume_24h, trade_count_24h,
                            rsi_14, ma_5, ma_20, ma_60,
                            bb_upper, bb_lower, atr_14,
                            total_market_volume_krw, top10_avg_change_pct, market_state, volatility_level
                        FROM market_snapshots;
                        DROP TABLE market_snapshots;
                        ALTER TABLE market_snapshots_new RENAME TO market_snapshots;
                    """)
                    logger.info("market_snapshots AUTOINCREMENT 복원 완료")
            except Exception as e:
                logger.warning("market_snapshots 마이그레이션 스킵: %s", e)

            # 마이그레이션: market_snapshots에 coin 컬럼 추가
            try:
                conn.execute("SELECT coin FROM market_snapshots LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE market_snapshots ADD COLUMN coin TEXT NOT NULL DEFAULT 'KRW-BTC'")
                logger.info("market_snapshots 테이블에 coin 컬럼 추가 완료")

            # 마이그레이션: trade_signals에 strategy_params_json 컬럼 추가
            try:
                conn.execute("SELECT strategy_params_json FROM trade_signals LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE trade_signals ADD COLUMN strategy_params_json TEXT")
                logger.info("trade_signals 테이블에 strategy_params_json 컬럼 추가 완료")

            # 마이그레이션: trades 테이블에 정합성 검증 컬럼 추가
            try:
                conn.execute("SELECT order_uuid FROM trades LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE trades ADD COLUMN order_uuid TEXT")
                conn.execute("ALTER TABLE trades ADD COLUMN reconciled INTEGER DEFAULT 0")
                conn.execute("ALTER TABLE trades ADD COLUMN reconciled_at DATETIME")
                logger.info("trades 테이블에 정합성 검증 컬럼 추가 완료 (order_uuid, reconciled, reconciled_at)")

            # 마이그레이션: llm_decisions에 before/after 스냅샷 컬럼 추가 (#171)
            try:
                conn.execute("SELECT before_snapshot_json FROM llm_decisions LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE llm_decisions ADD COLUMN before_snapshot_json TEXT")
                conn.execute("ALTER TABLE llm_decisions ADD COLUMN after_snapshot_json TEXT")
                logger.info("llm_decisions 테이블에 before/after 스냅샷 컬럼 추가 완료")

            # 마이그레이션: news_articles에 impact_score/scope 컬럼 추가 (#154)
            try:
                conn.execute("SELECT impact_score FROM news_articles LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE news_articles ADD COLUMN impact_score INTEGER")
                conn.execute("ALTER TABLE news_articles ADD COLUMN scope TEXT")
                logger.info("news_articles 테이블에 impact_score/scope 컬럼 추가 완료")

            # 마이그레이션: llm_decisions에 캐시 토큰 컬럼 추가 (#183)
            try:
                conn.execute("SELECT cache_creation_tokens FROM llm_decisions LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE llm_decisions ADD COLUMN cache_creation_tokens INTEGER")
                conn.execute("ALTER TABLE llm_decisions ADD COLUMN cache_read_tokens INTEGER")
                logger.info("llm_decisions 테이블에 cache_creation_tokens/cache_read_tokens 컬럼 추가 완료")

            # 마이그레이션: coin_strategy_assignment 테이블 생성 (#152)
            try:
                conn.execute("SELECT 1 FROM coin_strategy_assignment LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute(
                    """
                    CREATE TABLE coin_strategy_assignment (
                        coin TEXT PRIMARY KEY,
                        strategy_name TEXT NOT NULL,
                        params_json TEXT,
                        assigned_by TEXT NOT NULL DEFAULT 'llm',
                        reason TEXT,
                        assigned_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                logger.info("coin_strategy_assignment 테이블 생성 완료")

            # 마이그레이션: bot_config에 새 설정 추가 (기존 DB 호환)
            existing = conn.execute("SELECT key FROM bot_config WHERE key = 'strategy_switch_delay_seconds'").fetchone()
            if existing is None:
                conn.execute(
                    "INSERT OR IGNORE INTO bot_config "
                    "(key, value, value_type, category, display_name, description) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "strategy_switch_delay_seconds",
                        "30",
                        "int",
                        "bot",
                        "전략 전환 대기 시간 (초)",
                        "새 전략 활성화 시 기존 전략이 종료되기까지 대기하는 시간",
                    ),
                )
                logger.info("bot_config에 strategy_switch_delay_seconds 추가")

            # 마이그레이션: 멀티코인 설정 추가
            multi_coin = conn.execute("SELECT key FROM bot_config WHERE key = 'multi_coin_enabled'").fetchone()
            if multi_coin is None:
                for cfg in _DEFAULT_BOT_CONFIG:
                    if cfg["category"] == "coin":
                        conn.execute(
                            "INSERT OR IGNORE INTO bot_config "
                            "(key, value, value_type, category, display_name, description) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                cfg["key"],
                                cfg["value"],
                                cfg["value_type"],
                                cfg["category"],
                                cfg["display_name"],
                                cfg["description"],
                            ),
                        )
                logger.info("bot_config에 멀티코인 설정 추가")

            # #230: llm_enabled 토글 마이그레이션 (기존 DB)
            llm_e = conn.execute("SELECT key FROM bot_config WHERE key = 'llm_enabled'").fetchone()
            if llm_e is None:
                conn.execute(
                    "INSERT OR IGNORE INTO bot_config "
                    "(key, value, value_type, category, display_name, description) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("llm_enabled", "false", "bool", "llm", "LLM 분석 활성",
                     "#230: false면 _should_run에서 즉시 차단. 호출 0건."),
                )
                logger.info("#230: llm_enabled=false 토글 추가")

            # #228: 화이트리스트 설정 마이그레이션 (기존 DB)
            wl_exists = conn.execute(
                "SELECT key FROM bot_config WHERE key = 'coin_whitelist_enabled'"
            ).fetchone()
            if wl_exists is None:
                for cfg in _DEFAULT_BOT_CONFIG:
                    if cfg["key"] in ("coin_whitelist_enabled", "coin_whitelist"):
                        conn.execute(
                            "INSERT OR IGNORE INTO bot_config "
                            "(key, value, value_type, category, display_name, description) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                cfg["key"], cfg["value"], cfg["value_type"],
                                cfg["category"], cfg["display_name"], cfg["description"],
                            ),
                        )
                logger.info("#228: 메이저 코인 화이트리스트 설정 추가")

            # #285: KIS 시장별 거래 토글 (DB 기반 ON/OFF)
            for k, default_val, dn, desc in (
                ("kis_kr_trading_enabled", "false", "한국주식 거래 활성",
                 "false면 봇 도는데 거래만 OFF — 시드 작을 때 단가 1주 미만 종목 회피"),
                ("kis_us_trading_enabled", "true", "미국주식 거래 활성",
                 "false면 봇 도는데 거래만 OFF"),
            ):
                exists = conn.execute(
                    "SELECT 1 FROM bot_config WHERE key = ?", (k,)
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT OR IGNORE INTO bot_config "
                        "(key, value, value_type, category, display_name, description) "
                        "VALUES (?, ?, 'bool', 'kis', ?, ?)",
                        (k, default_val, dn, desc),
                    )
                    logger.info("#285: bot_config %s=%s 추가", k, default_val)

            # #293: KIS 미국 종목 풀 시드 (오늘 세션에서 선정된 후보들)
            # SOXL만 enabled=TRUE (사용자 첫날 단타 종목), 나머지는 후보로 비활성
            count = conn.execute("SELECT COUNT(*) FROM kis_us_symbols").fetchone()[0]
            if count == 0:
                _seed = [
                    # ticker, display_name, exchange, integer_only, category, enabled, note
                    # 빅테크
                    ("AAPL",  "Apple",          "NASD", 0, "bigtech", 0, ""),
                    ("MSFT",  "Microsoft",      "NASD", 0, "bigtech", 0, ""),
                    ("GOOGL", "Alphabet",       "NASD", 0, "bigtech", 0, ""),
                    ("AMZN",  "Amazon",         "NASD", 0, "bigtech", 0, ""),
                    ("META",  "Meta",           "NASD", 0, "bigtech", 0, ""),
                    # 반도체
                    ("NVDA",  "Nvidia",         "NASD", 0, "semi",    0, ""),
                    ("AMD",   "AMD",            "NASD", 0, "semi",    0, ""),
                    ("TSM",   "TSMC",           "NYSE", 0, "semi",    0, ""),
                    ("AVGO",  "Broadcom",       "NASD", 0, "semi",    0, ""),
                    ("ASML",  "ASML",           "NASD", 0, "semi",    0, ""),
                    ("SNDK",  "SanDisk",        "NASD", 0, "semi",    0, "2025년 WDC 분사"),
                    # 레버리지 ETF (정수 매매)
                    ("SOXL",  "Direxion Semi Bull 3X",        "AMEX", 1, "leveraged", 1, "기본 활성 — 첫날 단타"),
                    ("SOXS",  "Direxion Semi Bear 3X",        "AMEX", 1, "leveraged", 0, "반도체 하락 베팅"),
                    ("TQQQ",  "ProShares UltraPro QQQ 3X",    "AMEX", 1, "leveraged", 0, "나스닥100 3X"),
                    ("SQQQ",  "ProShares UltraPro Short 3X",  "AMEX", 1, "leveraged", 0, "나스닥100 -3X"),
                    ("USD",   "ProShares Ultra Semi 2X",      "AMEX", 1, "leveraged", 0, "반도체 2X"),
                    ("TECL",  "Direxion Tech Bull 3X",        "AMEX", 1, "leveraged", 0, "기술주 3X"),
                    ("NVDL",  "GraniteShares NVDA 2X",        "AMEX", 1, "leveraged", 0, "엔비디아 2X"),
                    ("SNXX",  "Tradr 2X Long SNDK",           "AMEX", 1, "leveraged", 0, "SanDisk 2X"),
                    # 크립토 노출
                    ("COIN",  "Coinbase",       "NASD", 0, "crypto",  0, ""),
                    ("MSTR",  "MicroStrategy",  "NASD", 0, "crypto",  0, "BTC 보유"),
                    ("HOOD",  "Robinhood",      "NASD", 0, "crypto",  0, ""),
                    # EV / 모빌리티
                    ("TSLA",  "Tesla",          "NASD", 0, "ev",      0, ""),
                    ("RIVN",  "Rivian",         "NASD", 0, "ev",      0, ""),
                    # AI / 소프트웨어
                    ("PLTR",  "Palantir",       "NASD", 0, "ai",      0, ""),
                    ("ARM",   "ARM Holdings",   "NASD", 0, "ai",      0, ""),
                    ("NFLX",  "Netflix",        "NASD", 0, "ai",      0, ""),
                    # ETF (1X — 시장 노출용, 변동성 낮아 RSI 신호 적음)
                    ("QQQ",   "Nasdaq 100",     "NASD", 0, "etf",     0, ""),
                    ("SPY",   "S&P 500",        "AMEX", 0, "etf",     0, ""),
                    ("VOO",   "Vanguard S&P 500","AMEX", 0, "etf",     0, ""),
                    ("SOXX",  "iShares Semi",   "NASD", 0, "etf",     0, ""),
                    ("SMH",   "VanEck Semi",    "NASD", 0, "etf",     0, ""),
                ]
                for row in _seed:
                    conn.execute(
                        "INSERT OR IGNORE INTO kis_us_symbols "
                        "(ticker, display_name, exchange, is_integer_only, category, enabled, note) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        row,
                    )
                logger.info("#293: kis_us_symbols 시드 %d종목 (SOXL 활성)", len(_seed))

            # 코인 카테고리별 전략 기본값
            row = conn.execute("SELECT COUNT(*) FROM coin_strategy_config").fetchone()
            if row[0] == 0:
                for cat_cfg in _DEFAULT_COIN_STRATEGY:
                    conn.execute(
                        """INSERT INTO coin_strategy_config (
                            category, strategy_name, stop_loss_pct, trailing_stop_pct,
                            position_size_pct, strategy_params_json, description
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            cat_cfg["category"],
                            cat_cfg["strategy_name"],
                            cat_cfg["stop_loss_pct"],
                            cat_cfg["trailing_stop_pct"],
                            cat_cfg["position_size_pct"],
                            cat_cfg["strategy_params_json"],
                            cat_cfg["description"],
                        ),
                    )
                logger.info("코인 카테고리별 전략 기본값 삽입 완료")

            # 기본 파라미터가 없으면 삽입
            row = conn.execute("SELECT COUNT(*) FROM strategy_params").fetchone()
            if row[0] == 0:
                conn.executescript(_DEFAULT_PARAMS)
                logger.info("기본 전략 파라미터 삽입 완료")

            # 전략 마스터 데이터 삽입
            row = conn.execute("SELECT COUNT(*) FROM strategies").fetchone()
            if row[0] == 0:
                for s in _DEFAULT_STRATEGIES:
                    conn.execute(
                        """
                        INSERT INTO strategies (
                            name, display_name, description, category,
                            market_states, timeframe, difficulty,
                            default_params_json, is_active, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            s["name"],
                            s["display_name"],
                            s["description"],
                            s["category"],
                            s["market_states"],
                            s["timeframe"],
                            s["difficulty"],
                            s["default_params_json"],
                            s["is_active"],
                            "active" if s["is_active"] else "inactive",
                        ),
                    )
                logger.info("전략 마스터 데이터 삽입 완료 (%d개)", len(_DEFAULT_STRATEGIES))

            # 마이그레이션: bb_rsi_combined 전략 추가 (기존 DB)
            bb_rsi = conn.execute("SELECT 1 FROM strategies WHERE name = 'bb_rsi_combined'").fetchone()
            if bb_rsi is None:
                conn.execute(
                    """INSERT INTO strategies (
                        name, display_name, description, category, market_states,
                        timeframe, difficulty, default_params_json, is_active, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "bb_rsi_combined",
                        "볼린저+RSI 복합",
                        "RSI 과매도 + 볼린저 하단 이탈 동시 충족 시 매수. 거짓 신호 감소로 60%+ 승률.",
                        "mean_reversion",
                        "sideways,bearish",
                        "1d",
                        "medium",
                        '{"bb_period": 20, "bb_std": 2.0, "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70}',
                        False,
                        "inactive",
                    ),
                )
                logger.info("bb_rsi_combined 전략 추가")

            # #226: long_term_swing 전략 추가 (기존 DB)
            lts = conn.execute("SELECT 1 FROM strategies WHERE name = 'long_term_swing'").fetchone()
            if lts is None:
                conn.execute(
                    """INSERT INTO strategies (
                        name, display_name, description, category, market_states,
                        timeframe, difficulty, default_params_json, is_active, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "long_term_swing",
                        "장기 스윙",
                        "공포탐욕지수+50일MA+RSI 결합. 저점 매수, 고점 매도. 평균 보유 10~16일.",
                        "swing",
                        "bearish,sideways",
                        "1d",
                        "medium",
                        (
                            '{"ma_long": 50, "ma_short": 20, "rsi_period": 14, "rsi_entry_max": 45,'
                            ' "fear_threshold": 30, "greed_threshold": 70, "take_profit_pct": 20.0,'
                            ' "min_hold_days": 7}'
                        ),
                        False,  # 기본 비활성
                        "inactive",
                    ),
                )
                logger.info("long_term_swing 전략 추가 (#226, 기본 비활성)")

            # 봇 설정 기본값 삽입
            row = conn.execute("SELECT COUNT(*) FROM bot_config").fetchone()
            if row[0] == 0:
                for cfg in _DEFAULT_BOT_CONFIG:
                    conn.execute(
                        """
                        INSERT INTO bot_config (key, value, value_type, category, display_name, description)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cfg["key"],
                            cfg["value"],
                            cfg["value_type"],
                            cfg["category"],
                            cfg["display_name"],
                            cfg["description"],
                        ),
                    )
                logger.info("봇 설정 기본값 삽입 완료 (%d개)", len(_DEFAULT_BOT_CONFIG))

            # 마이그레이션: 멀티 마켓 market 컬럼 추가 (#245)
            for tbl in ("trades", "trade_signals", "market_snapshots", "ohlcv_daily", "ohlcv_minutes"):
                try:
                    conn.execute(f"SELECT market FROM {tbl} LIMIT 1")
                except sqlite3.OperationalError:
                    conn.execute(f"ALTER TABLE {tbl} ADD COLUMN market TEXT NOT NULL DEFAULT 'upbit'")
                    logger.info("%s 테이블에 market 컬럼 추가 (#245)", tbl)

            # 마이그레이션: daily_reports에 market 컬럼 + UNIQUE(date, market) 적용 (#245)
            # ALTER TABLE로 UNIQUE 제약 변경 불가 → 새 테이블 만들어 데이터 이전
            try:
                cols = conn.execute("PRAGMA table_info(daily_reports)").fetchall()
                has_market = any(c[1] == "market" for c in cols)
                if not has_market:
                    conn.executescript(
                        """
                        CREATE TABLE daily_reports_new (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            date DATE NOT NULL,
                            market TEXT NOT NULL DEFAULT 'upbit',
                            starting_balance_krw REAL,
                            ending_balance_krw REAL,
                            total_asset_value_krw REAL,
                            realized_pnl_krw REAL,
                            unrealized_pnl_krw REAL,
                            daily_return_pct REAL,
                            cumulative_return_pct REAL,
                            total_trades INTEGER,
                            buy_trades INTEGER,
                            sell_trades INTEGER,
                            winning_trades INTEGER,
                            losing_trades INTEGER,
                            win_rate REAL,
                            avg_profit_pct REAL,
                            avg_loss_pct REAL,
                            max_drawdown_pct REAL,
                            total_fees_krw REAL,
                            active_param_id INTEGER,
                            market_state TEXT,
                            UNIQUE(date, market),
                            FOREIGN KEY (active_param_id) REFERENCES strategy_params(id)
                        );
                        INSERT INTO daily_reports_new (
                            id, date, market, starting_balance_krw, ending_balance_krw,
                            total_asset_value_krw, realized_pnl_krw, unrealized_pnl_krw,
                            daily_return_pct, cumulative_return_pct,
                            total_trades, buy_trades, sell_trades, winning_trades, losing_trades,
                            win_rate, avg_profit_pct, avg_loss_pct, max_drawdown_pct,
                            total_fees_krw, active_param_id, market_state
                        ) SELECT
                            id, date, 'upbit', starting_balance_krw, ending_balance_krw,
                            total_asset_value_krw, realized_pnl_krw, unrealized_pnl_krw,
                            daily_return_pct, cumulative_return_pct,
                            total_trades, buy_trades, sell_trades, winning_trades, losing_trades,
                            win_rate, avg_profit_pct, avg_loss_pct, max_drawdown_pct,
                            total_fees_krw, active_param_id, market_state
                        FROM daily_reports;
                        DROP TABLE daily_reports;
                        ALTER TABLE daily_reports_new RENAME TO daily_reports;
                        """
                    )
                    logger.info("daily_reports에 market 컬럼 + UNIQUE(date, market) 적용 (#245)")
            except sqlite3.OperationalError as e:
                logger.warning("daily_reports market 마이그레이션 스킵: %s", e)

            # 인덱스 생성
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_coin_id ON market_snapshots(coin, id DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON market_snapshots(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_coin_timestamp ON trade_signals(coin, timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON trade_signals(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_coin_side ON trades(coin, side)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_coin_date ON ohlcv_daily(coin, date)")
            # #245: 멀티 마켓 인덱스 — market 컬럼 마이그레이션 *후* 생성 (시장별 조회 최적화)
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_market_ts ON trades(market, timestamp DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_market_ts ON trade_signals(market, timestamp DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_market_ts ON market_snapshots(market, timestamp DESC)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_reports_market ON daily_reports(market, date DESC)")
            except sqlite3.OperationalError as e:
                logger.warning("멀티 마켓 인덱스 생성 스킵 (market 컬럼 미존재): %s", e)

            # 잔여 마이그레이션 테이블 정리
            conn.execute("DROP TABLE IF EXISTS market_snapshots_old")

            conn.commit()
            logger.debug("데이터베이스 연결 준비 완료: %s", self._db_path)
        except sqlite3.Error as e:
            raise DatabaseError(f"데이터베이스 초기화 실패: {e}") from e

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """SQL 실행 후 커서 반환."""
        return self.connection.execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple]) -> sqlite3.Cursor:
        """여러 행 삽입."""
        return self.connection.executemany(sql, params_list)

    def commit(self) -> None:
        """트랜잭션 커밋."""
        self.connection.commit()

    def close(self) -> None:
        """커넥션 종료."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.debug("데이터베이스 커넥션 종료")

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
