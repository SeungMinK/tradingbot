"""FastAPI 서버 진입점.

NestJS의 main.ts (bootstrap) + AppModule과 동일.

사용법:
    uvicorn cryptobot.api.main:app --reload --port 8000
"""

import logging as _logging
import os
import time as _time
from collections import defaultdict as _defaultdict

from fastapi import Depends as _Depends
from fastapi import FastAPI, Request
from fastapi import Query as _Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel as _BaseModel

from cryptobot.api.auth import UserResponse, get_current_user
from cryptobot.api.deps import get_db as _get_db
from cryptobot.api.routes import auth, balance, coin_strategy, config, market, market_capital, market_stats, market_universe, news, public, signals, strategies, trades, visits
from cryptobot.logging_config import setup_logging

setup_logging("api", "INFO")

app = FastAPI(
    title="CryptoBot Admin API",
    description="코인 자동매매 봇 관리 API",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# CORS — 개발 + 프로덕션 도메인 허용
_cors_origins = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "https://cryptobot-eight.vercel.app",
    "https://seungmink.dev",
    "https://crypto.seungmink.dev",
]
_extra_origins = os.getenv("CORS_ORIGINS", "")
if _extra_origins:
    _cors_origins.extend([o.strip() for o in _extra_origins.split(",") if o.strip()])

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)


# 보안 헤더 미들웨어
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """보안 헤더 추가."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


# 라우트 등록
app.include_router(public.router)  # 공개 API (인증 불필요)
app.include_router(auth.router)
app.include_router(trades.router)
app.include_router(balance.router)
app.include_router(strategies.router)
app.include_router(market.router)
app.include_router(config.router)
app.include_router(signals.router)
app.include_router(news.router)
app.include_router(coin_strategy.router)
app.include_router(visits.router)  # #240 방문자 통계 (admin)
app.include_router(market_stats.router)  # #254 6단계 시장별 PnL (admin)
app.include_router(market_capital.router)  # #277 시장별 자본 입출금/이동 (admin)
app.include_router(market_universe.router)  # #278 시장별 종목 풀 + 활성 전략 (admin)


@app.get("/api/llm/hard-limits", tags=["llm"])
def get_hard_limits(_: UserResponse = _Depends(get_current_user)):
    """LLM 하드 리밋 조회 (읽기 전용)."""
    from cryptobot.llm.analyzer import HARD_LIMITS

    return {k: {"min": v[0], "max": v[1]} for k, v in HARD_LIMITS.items()}


@app.get("/api/llm/decisions", tags=["llm"])
def get_llm_decisions(limit: int = _Query(4, ge=1, le=50), _: UserResponse = _Depends(get_current_user)):
    """LLM 분석 이력."""
    db = _get_db()
    rows = db.execute("SELECT * FROM llm_decisions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/llm/prompts", tags=["llm"])
def get_llm_prompts(_: UserResponse = _Depends(get_current_user)):
    """프롬프트 버전 목록."""
    db = _get_db()
    rows = db.execute(
        "SELECT id, version, description, is_active, created_at, activated_at FROM prompt_versions ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/llm/prompts/{prompt_id}", tags=["llm"])
def get_llm_prompt_detail(prompt_id: int, _: UserResponse = _Depends(get_current_user)):
    """프롬프트 상세 (전문 포함)."""
    db = _get_db()
    row = db.execute("SELECT * FROM prompt_versions WHERE id = ?", (prompt_id,)).fetchone()
    if row is None:
        return {"detail": "프롬프트 없음"}
    return dict(row)


@app.get("/api/health", tags=["system"])
def health_check():
    """헬스체크. 서버가 살아있는지 확인."""
    return {"status": "ok", "service": "cryptobot-api"}


_web_logger = _logging.getLogger("web")


class _WebErrorReport(_BaseModel):
    message: str
    source: str | None = None
    stack: str | None = None
    url: str | None = None
    user_agent: str | None = None


_error_report_attempts: dict[str, list[float]] = _defaultdict(list)


@app.post("/api/error/report", tags=["system"])
def report_web_error(request: Request, error: _WebErrorReport):
    """Admin 웹에서 발생한 에러를 서버 로그로 기록 (rate limit: 10회/분)."""
    client_ip = request.client.host if request.client else "unknown"
    now = _time.time()
    _error_report_attempts[client_ip] = [t for t in _error_report_attempts[client_ip] if now - t < 60]
    if len(_error_report_attempts[client_ip]) >= 10:
        return JSONResponse(status_code=429, content={"detail": "Too many requests"})
    _error_report_attempts[client_ip].append(now)

    _web_logger.error(
        "[WEB] %s | source=%s | url=%s\n  %s",
        error.message,
        error.source or "unknown",
        error.url or "unknown",
        error.stack or "no stack",
    )
    return {"status": "recorded"}
