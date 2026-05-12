-- #386: 백테스트 필터 ON + 화이트리스트에 검증 통과 중소형 알트 추가
--
-- 의도:
-- 1. coin_backtest_filter_enabled = true → 미검증 종목 (백테스트 없는 신생 코인) 자동 차단
-- 2. 화이트리스트에 검증 통과 알트 7종 추가 (BIO/WET/0G/AERO/RENDER/BOUNTY/JST)
--    - 메이저 8종은 알파 거의 없음 (실데이터로 검증), 중소형에서 알파 잡음
--    - 추가 7종 모두 백테스트 평균익절 ≥ 5% 통과
--
-- 결과 매수 풀 (필터 적용 후):
--   메이저 통과: ETH, XRP, SOL, ADA, DOGE, LINK (BTC/AVAX 미통과)
--   알트 추가: BIO, WET, 0G, AERO, RENDER, BOUNTY, JST (모두 통과)
--   = 13종 매수 후보
--
-- 멱등성: UPDATE 사용, 여러 번 실행해도 동일 결과.

BEGIN TRANSACTION;

-- 1. 백테스트 검증 필터 ON (디폴트 false → true)
UPDATE bot_config
SET value = 'true'
WHERE key = 'coin_backtest_filter_enabled';

-- 2. 화이트리스트 확장: 메이저 8종 + 검증 통과 중소형 7종
UPDATE bot_config
SET value = 'KRW-BTC,KRW-ETH,KRW-XRP,KRW-SOL,KRW-ADA,KRW-DOGE,KRW-AVAX,KRW-LINK,KRW-BIO,KRW-WET,KRW-0G,KRW-AERO,KRW-RENDER,KRW-BOUNTY,KRW-JST'
WHERE key = 'coin_whitelist';

COMMIT;

-- 검증 쿼리:
-- SELECT key, value FROM bot_config WHERE key IN ('coin_backtest_filter_enabled', 'coin_whitelist');
