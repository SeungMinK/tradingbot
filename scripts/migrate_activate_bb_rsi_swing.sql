-- #382: bb_rsi_combined swing 모드 활성화 + 백테스트 필터 시드.
--
-- 변경:
-- 1. 활성 전략 vwap_orb_breakout → bb_rsi_combined
-- 2. bb_rsi_combined default_params 업데이트 (bb_std=1.5, rsi_oversold=25, min_profit_for_trailing=5.0)
-- 3. coin_backtest_filter_enabled / min_avg_profit / min_trades 시드 (기존 DB 호환)
--
-- 멱등성: 여러 번 실행해도 동일 결과.

BEGIN TRANSACTION;

-- 1. 기존 활성 전략 모두 비활성화
UPDATE strategies SET is_active = 0;

-- 2. bb_rsi_combined 활성화 + 파라미터 업데이트
UPDATE strategies
SET is_active = 1,
    default_params_json = '{"bb_std": 1.5, "bb_period": 20, "rsi_period": 14, "rsi_oversold": 25, "rsi_overbought": 50, "min_profit_for_trailing": 5.0}'
WHERE name = 'bb_rsi_combined';

-- 3. bot_config 시드 (이미 있으면 무시 — INSERT OR IGNORE)
INSERT OR IGNORE INTO bot_config (key, value, value_type, category, display_name, description)
VALUES
    ('coin_backtest_filter_enabled', 'false', 'bool', 'coin',
     '백테스트 검증 필터',
     'ON: 화이트리스트 ∩ 백테스트 통과 코인만 매수. 미검증 종목 손실(NEWT 등) 차단.'),
    ('coin_backtest_min_avg_profit', '5.0', 'float', 'coin',
     '백테스트 최소 평균 익절 (%)',
     '백테스트 결과 avg_profit_pct가 이 값 이상인 코인만 통과. 기본 5%.'),
    ('coin_backtest_min_trades', '3', 'int', 'coin',
     '백테스트 최소 거래 건수',
     '백테스트 결과 num_trades가 이 값 이상인 코인만 통과. 표본 부족 코인 제외.');

COMMIT;

-- 검증 쿼리 (실행 후 수동 확인용):
-- SELECT name, is_active, default_params_json FROM strategies WHERE is_active = 1;
-- SELECT key, value FROM bot_config WHERE key LIKE 'coin_backtest%';
