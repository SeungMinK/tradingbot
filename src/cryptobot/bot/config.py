"""봇 설정 관리 모듈.

NestJS의 ConfigModule과 동일한 역할.
.env 파일에서 환경변수를 로딩하고, 앱 전체에서 사용할 설정을 관리한다.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# 프로젝트 루트 기준으로 .env 로딩
PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class UpbitConfig:
    """업비트 API 설정."""

    access_key: str = field(default_factory=lambda: os.getenv("UPBIT_ACCESS_KEY", ""))
    secret_key: str = field(default_factory=lambda: os.getenv("UPBIT_SECRET_KEY", ""))

    @property
    def is_configured(self) -> bool:
        """API Key가 설정되어 있는지 확인."""
        return bool(self.access_key and self.secret_key)


@dataclass(frozen=True)
class KISConfig:
    """KIS Developers API 설정 (한국·미국 주식 공통)."""

    app_key: str = field(default_factory=lambda: os.getenv("KIS_APP_KEY", ""))
    app_secret: str = field(default_factory=lambda: os.getenv("KIS_APP_SECRET", ""))
    account_number: str = field(default_factory=lambda: os.getenv("KIS_ACCOUNT_NUMBER", ""))
    account_product_code: str = field(default_factory=lambda: os.getenv("KIS_ACCOUNT_PRODUCT_CODE", "01"))
    is_paper: bool = field(default_factory=lambda: os.getenv("KIS_IS_PAPER", "false").lower() == "true")
    # #274: 시장별 예산 (단일 KIS 계좌에서 한국/미국 충돌 방지)
    kr_budget_krw: float = field(default_factory=lambda: float(os.getenv("KIS_KR_BUDGET_KRW", "200000")))
    us_budget_krw: float = field(default_factory=lambda: float(os.getenv("KIS_US_BUDGET_KRW", "200000")))

    @property
    def is_configured(self) -> bool:
        return bool(self.app_key and self.app_secret and self.account_number)


@dataclass(frozen=True)
class SlackConfig:
    """Slack 알림 설정."""

    bot_token: str = field(default_factory=lambda: os.getenv("SLACK_BOT_TOKEN", ""))
    channel: str = field(default_factory=lambda: os.getenv("SLACK_CHANNEL", ""))
    # [DEPRECATED] Webhook 방식 — Bot Token 방식 사용 권장
    webhook_url: str = field(default_factory=lambda: os.getenv("SLACK_WEBHOOK_URL", ""))

    @property
    def is_configured(self) -> bool:
        """Bot Token 방식 또는 Webhook 방식 중 하나라도 설정되어 있으면 True."""
        return bool(self.bot_token and self.channel) or bool(self.webhook_url)


@dataclass(frozen=True)
class BotConfig:
    """봇 매매 설정."""

    coin: str = field(default_factory=lambda: os.getenv("BOT_COIN", "KRW-BTC"))
    log_level: str = field(default_factory=lambda: os.getenv("BOT_LOG_LEVEL", "INFO"))
    db_path: Path = field(default_factory=lambda: PROJECT_ROOT / os.getenv("DB_PATH", "data/cryptobot.db"))


@dataclass(frozen=True)
class Config:
    """앱 전체 설정. NestJS의 ConfigService와 동일한 역할."""

    upbit: UpbitConfig = field(default_factory=UpbitConfig)
    kis: KISConfig = field(default_factory=KISConfig)
    slack: SlackConfig = field(default_factory=SlackConfig)
    bot: BotConfig = field(default_factory=BotConfig)


# 싱글턴 — NestJS의 @Global() + @Module()처럼 앱 전체에서 import해서 사용
config = Config()
