"""KIS 미국주식 어댑터 단위 테스트.

mocking으로 KIS API 호출 없이 검증.

Related: #247
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from cryptobot.exchange.kis.auth import KISTokenManager
from cryptobot.exchange.kis_us import KISUSExchange

NY = ZoneInfo("America/New_York")


def _build(tmp_path: Path) -> KISUSExchange:
    cache = tmp_path / "kis_token.json"
    tm = KISTokenManager(app_key="k", app_secret="s", cache_path=cache)
    tm._token = "TEST"
    tm._expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    return KISUSExchange(token_manager=tm, account_number="12345678")


def _mock_resp(payload: dict, rt_cd: str = "0"):
    resp = MagicMock()
    resp.json.return_value = {"rt_cd": rt_cd, **payload}
    resp.raise_for_status = MagicMock()
    return resp


# ---- 메타 ----


def test_market_id(tmp_path: Path):
    assert _build(tmp_path).market_id == "kis_us"


def test_tick_size_us(tmp_path: Path):
    ex = _build(tmp_path)
    # 미국주식 호가단위는 가격 무관 0.01 USD
    assert ex.tick_size("NVDA", 100) == 0.01
    assert ex.tick_size("NVDA", 1500) == 0.01


def test_is_market_open_during_ny_hours(tmp_path: Path):
    ex = _build(tmp_path)
    # NY 평일 10:00 (정규장)
    fake_now = datetime(2026, 5, 11, 10, 0, tzinfo=NY)
    with patch("cryptobot.exchange.kis_us.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        assert ex.is_market_open() is True


def test_is_market_open_weekend(tmp_path: Path):
    ex = _build(tmp_path)
    # NY 토요일
    fake_now = datetime(2026, 5, 9, 12, 0, tzinfo=NY)
    with patch("cryptobot.exchange.kis_us.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        assert ex.is_market_open() is False


def test_is_market_open_premarket(tmp_path: Path):
    ex = _build(tmp_path)
    # NY 평일 08:00 (프리마켓)
    fake_now = datetime(2026, 5, 11, 8, 0, tzinfo=NY)
    with patch("cryptobot.exchange.kis_us.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        assert ex.is_market_open() is False


# ---- API 호출 ----


def test_get_current_price(tmp_path: Path):
    ex = _build(tmp_path)
    fake = _mock_resp({"output": {"last": "150.25"}})
    with patch("requests.get", return_value=fake):
        assert ex.get_current_price("NVDA") == 150.25


def test_get_balance_usd(tmp_path: Path):
    """#287 변경: USD/KRW는 inquire-present-balance(CTRP6504R) 응답 사용."""
    ex = _build(tmp_path)
    # inquire-present-balance 응답 — output2는 통화별 list, output3는 요약 dict
    fake = _mock_resp(
        {
            "output1": [],
            "output2": [
                {"crcy_cd": "USD", "frcr_dncl_amt_2": "150.50", "frst_bltn_exrt": "1450.80"}
            ],
            "output3": {"tot_dncl_amt": "200000", "frcr_evlu_tota": "218400"},
        }
    )
    with patch("requests.get", return_value=fake):
        assert ex.get_balance("USD") == 150.50
        assert ex.get_balance("KRW") == 200000.0


def test_get_balance_holding(tmp_path: Path):
    ex = _build(tmp_path)
    fake = _mock_resp(
        {
            "output1": [
                {"ovrs_pdno": "NVDA", "ovrs_cblc_qty": "0.5"},
                {"ovrs_pdno": "AAPL", "ovrs_cblc_qty": "1.2"},
            ],
            "output2": {"frcr_dncl_amt1": "100", "krw_dncl_amt": "0"},
        }
    )
    with patch("requests.get", return_value=fake):
        assert ex.get_balance("NVDA") == 0.5
        assert ex.get_balance("AAPL") == 1.2
        assert ex.get_balance("MSFT") == 0.0


def test_buy_market_fractional(tmp_path: Path):
    """소수점 매수가 정상 동작."""
    ex = _build(tmp_path)
    order_resp = _mock_resp({"output": {"ODNO": "US123"}})
    price_resp = _mock_resp({"output": {"last": "150.00"}})

    with patch("requests.post", return_value=order_resp), patch("requests.get", return_value=price_resp):
        result = ex.buy_market("NVDA", 0.5)

    assert result.success is True
    assert result.amount == 0.5
    assert result.price == 150.00
    assert result.coin == "NVDA"
    # 환전 + 수수료가 fee에 반영됨 (150 USD × 1380 KRW/USD × 0.5주 × (0.07% + 0.25%) ≈ 331원)
    assert result.fee_krw > 200


def test_buy_zero_qty(tmp_path: Path):
    ex = _build(tmp_path)
    result = ex.buy_market("NVDA", 0)
    assert result.success is False
    assert "0 이하" in result.error


def test_sell_market(tmp_path: Path):
    ex = _build(tmp_path)
    order_resp = _mock_resp({"output": {"ODNO": "SELL_US"}})
    price_resp = _mock_resp({"output": {"last": "200.00"}})

    with patch("requests.post", return_value=order_resp), patch("requests.get", return_value=price_resp):
        result = ex.sell_market("AAPL", 1.0)

    assert result.success is True
    assert result.side == "sell"
    assert result.price == 200.00
    assert result.coin == "AAPL"


def test_exchange_code_default_nasdaq(tmp_path: Path):
    """풀에 없는 ticker는 NASDAQ 기본. #289: SPY/VOO는 AMEX (NYSE Arca ETF)."""
    ex = _build(tmp_path)
    assert ex._exchange_code("UNKNOWN") == "NASD"
    assert ex._exchange_code("NVDA") == "NASD"
    assert ex._exchange_code("SOXL") == "AMEX"  # 레버리지 ETF
    assert ex._exchange_code("TSM") == "NYSE"


def test_integer_only_tickers_set():
    """레버리지 ETF는 매매단위 1주 (KIS 미국주식 정수 매매)."""
    from cryptobot.exchange.kis_us import INTEGER_ONLY_TICKERS

    assert "SOXL" in INTEGER_ONLY_TICKERS
    assert "TQQQ" in INTEGER_ONLY_TICKERS
    assert "SQQQ" in INTEGER_ONLY_TICKERS
    # 일반 주식은 fractional 가능
    assert "NVDA" not in INTEGER_ONLY_TICKERS
    assert "AAPL" not in INTEGER_ONLY_TICKERS
