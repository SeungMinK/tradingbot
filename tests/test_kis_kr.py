"""KIS 한국주식 어댑터 단위 테스트.

실제 KIS API는 호출하지 않고 mocking으로 검증.

Related: #246
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from cryptobot.exchange.kis.auth import KISTokenManager
from cryptobot.exchange.kis_kr import KISKoreanExchange, korean_stock_tick_size

KST = ZoneInfo("Asia/Seoul")


# ---- tick_size ----


@pytest.mark.parametrize(
    "price,expected",
    [
        (1_500, 1),  # < 2,000
        (3_000, 5),  # 2,000~5,000
        (10_000, 10),  # 5,000~20,000
        (30_000, 50),  # 20,000~50,000
        (100_000, 100),  # 50,000~200,000
        (300_000, 500),  # 200,000~500,000
        (700_000, 1_000),  # >= 500,000
    ],
)
def test_korean_stock_tick_size(price, expected):
    assert korean_stock_tick_size(price) == expected


# ---- KISTokenManager ----


def test_token_manager_caches_to_file(tmp_path: Path):
    cache = tmp_path / "kis_token.json"
    tm = KISTokenManager(
        app_key="test_key",
        app_secret="test_secret",
        cache_path=cache,
    )

    fake_resp = MagicMock()
    fake_resp.json.return_value = {"access_token": "abc123", "expires_in": 86400}
    fake_resp.raise_for_status = MagicMock()

    with patch("requests.post", return_value=fake_resp) as mock_post:
        token = tm.get_token()
        assert token == "abc123"
        mock_post.assert_called_once()

    # 두 번째 호출은 캐시 사용 (HTTP 호출 추가 없음)
    with patch("requests.post") as mock_post2:
        token2 = tm.get_token()
        assert token2 == "abc123"
        mock_post2.assert_not_called()

    # 파일에 저장됨
    assert cache.exists()
    data = json.loads(cache.read_text())
    assert data["app_key"] == "test_key"
    assert data["token"] == "abc123"


def test_token_manager_loads_from_cache(tmp_path: Path):
    """다른 인스턴스가 같은 캐시 파일을 읽으면 토큰 재사용."""
    cache = tmp_path / "kis_token.json"
    # 먼저 캐시 파일 직접 생성
    from datetime import timedelta, timezone

    expires = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
    cache.write_text(
        json.dumps(
            {
                "app_key": "test_key",
                "token": "cached_xyz",
                "expires_at": expires,
            }
        )
    )

    tm = KISTokenManager(app_key="test_key", app_secret="test_secret", cache_path=cache)

    with patch("requests.post") as mock_post:
        token = tm.get_token()
        assert token == "cached_xyz"
        mock_post.assert_not_called()


def test_token_manager_refreshes_when_expired(tmp_path: Path):
    """만료 30분 이내면 신규 발급."""
    cache = tmp_path / "kis_token.json"
    from datetime import timedelta, timezone

    # 20분 후 만료 → 30분 안전마진 안에 들어옴
    expires = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
    cache.write_text(
        json.dumps(
            {
                "app_key": "test_key",
                "token": "stale",
                "expires_at": expires,
            }
        )
    )

    tm = KISTokenManager(app_key="test_key", app_secret="test_secret", cache_path=cache)
    fake_resp = MagicMock()
    fake_resp.json.return_value = {"access_token": "fresh", "expires_in": 86400}
    fake_resp.raise_for_status = MagicMock()

    with patch("requests.post", return_value=fake_resp):
        token = tm.get_token()
        assert token == "fresh"


# ---- KISKoreanExchange 메타 ----


def _build_exchange(tmp_path: Path) -> KISKoreanExchange:
    cache = tmp_path / "kis_token.json"
    tm = KISTokenManager(app_key="k", app_secret="s", cache_path=cache)
    # 토큰 발급은 mocking
    tm._token = "TEST_TOKEN"
    from datetime import timedelta, timezone

    tm._expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    return KISKoreanExchange(token_manager=tm, account_number="12345678")


def test_market_id(tmp_path: Path):
    ex = _build_exchange(tmp_path)
    assert ex.market_id == "kis_kr"


def test_tick_size_method(tmp_path: Path):
    ex = _build_exchange(tmp_path)
    assert ex.tick_size("005930", 70_000) == 100.0
    assert ex.tick_size("005930", 1_500) == 1.0


def test_is_market_open_weekday_business_hours(tmp_path: Path):
    ex = _build_exchange(tmp_path)
    # 평일 10:00 KST
    fake_now = datetime(2026, 5, 11, 10, 0, tzinfo=KST)  # 월요일
    with patch("cryptobot.exchange.kis_kr.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        assert ex.is_market_open() is True


def test_is_market_open_weekend(tmp_path: Path):
    ex = _build_exchange(tmp_path)
    fake_now = datetime(2026, 5, 9, 10, 0, tzinfo=KST)  # 토요일
    with patch("cryptobot.exchange.kis_kr.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        assert ex.is_market_open() is False


def test_is_market_open_after_hours(tmp_path: Path):
    ex = _build_exchange(tmp_path)
    fake_now = datetime(2026, 5, 11, 16, 0, tzinfo=KST)  # 월요일 16:00
    with patch("cryptobot.exchange.kis_kr.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        assert ex.is_market_open() is False


# ---- KISKoreanExchange API 호출 (mocking) ----


def _mock_kis_response(payload: dict, rt_cd: str = "0"):
    resp = MagicMock()
    body = {"rt_cd": rt_cd, **payload}
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    return resp


def test_get_current_price(tmp_path: Path):
    ex = _build_exchange(tmp_path)
    fake = _mock_kis_response({"output": {"stck_prpr": "70500"}})
    with patch("requests.get", return_value=fake):
        price = ex.get_current_price("005930")
    assert price == 70500.0


def test_get_balance_krw(tmp_path: Path):
    ex = _build_exchange(tmp_path)
    fake = _mock_kis_response(
        {
            "output1": [],
            "output2": [{"dnca_tot_amt": "200000"}],
        }
    )
    with patch("requests.get", return_value=fake):
        krw = ex.get_balance("KRW")
    assert krw == 200000.0


def test_get_balance_holding(tmp_path: Path):
    ex = _build_exchange(tmp_path)
    fake = _mock_kis_response(
        {
            "output1": [
                {"pdno": "005930", "hldg_qty": "5"},
                {"pdno": "000660", "hldg_qty": "2"},
            ],
            "output2": [{"dnca_tot_amt": "100000"}],
        }
    )
    with patch("requests.get", return_value=fake):
        assert ex.get_balance("005930") == 5.0
        assert ex.get_balance("000660") == 2.0
        assert ex.get_balance("999999") == 0.0


def test_buy_market(tmp_path: Path):
    ex = _build_exchange(tmp_path)
    order_resp = _mock_kis_response({"output": {"ODNO": "ORDER123"}})
    price_resp = _mock_kis_response({"output": {"stck_prpr": "70000"}})

    with patch("requests.post", return_value=order_resp), patch("requests.get", return_value=price_resp):
        result = ex.buy_market("005930", 1)

    assert result.success is True
    assert result.side == "buy"
    assert result.coin == "005930"
    assert result.amount == 1
    assert result.price == 70000.0
    assert result.order_uuid == "ORDER123"


def test_sell_market_with_tax_in_fee(tmp_path: Path):
    ex = _build_exchange(tmp_path)
    order_resp = _mock_kis_response({"output": {"ODNO": "SELL456"}})
    price_resp = _mock_kis_response({"output": {"stck_prpr": "75000"}})

    with patch("requests.post", return_value=order_resp), patch("requests.get", return_value=price_resp):
        result = ex.sell_market("005930", 1)

    assert result.success is True
    assert result.side == "sell"
    # 매도 수수료 = (위탁 0.015% + 거래세 0.18%) × 75,000 = 약 146.25원
    assert 100 < result.fee_krw < 200


def test_buy_market_zero_qty(tmp_path: Path):
    ex = _build_exchange(tmp_path)
    result = ex.buy_market("005930", 0)
    assert result.success is False
    assert "0 이하" in result.error
