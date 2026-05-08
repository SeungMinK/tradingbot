#!/bin/bash
# TradingBot 전체 실행 스크립트 (봇 + API 서버 + Admin 웹)
# 사용법: bash scripts/start_all.sh
# 종료: Ctrl+C (전체 프로세스 종료)

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

# 색상
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${GREEN}=== TradingBot 전체 시작 ===${NC}"
echo ""

# 종료 시 모든 자식 프로세스 정리
cleanup() {
    echo ""
    echo -e "${YELLOW}모든 프로세스 종료 중...${NC}"
    kill 0 2>/dev/null
    wait 2>/dev/null
    echo -e "${GREEN}종료 완료${NC}"
    exit 0
}
trap cleanup SIGINT SIGTERM

# 1. API 서버 (port 8000)
echo -e "${CYAN}[1/3] API 서버 시작 (port 8000)${NC}"
.venv/bin/uvicorn cryptobot.api.main:app --host 0.0.0.0 --port 8000 --app-dir src 2>&1 | sed 's/^/  [API] /' &

sleep 1

# 2. 봇
echo -e "${CYAN}[2/3] 트레이딩 봇 시작${NC}"
.venv/bin/python -m cryptobot.bot.main 2>&1 | sed 's/^/  [BOT] /' &

sleep 1

# 3. 뉴스 수집기
echo -e "${CYAN}[3/4] 뉴스 수집기 시작${NC}"
.venv/bin/python news-collector/collector.py 2>&1 | sed 's/^/  [NEWS] /' &

sleep 1

# 4. Admin 웹 (port 5173)
echo -e "${CYAN}[4/5] Admin 웹서버 시작 (port 5173)${NC}"
cd admin && npm run dev 2>&1 | sed 's/^/  [WEB] /' &

cd "$PROJECT_ROOT"

sleep 1

# 5. Cloudflare Tunnel (api.seungmink.dev)
if command -v cloudflared &> /dev/null; then
    echo -e "${CYAN}[5/5] Cloudflare Tunnel 시작 (api.seungmink.dev)${NC}"
    cloudflared tunnel run cryptobot-api 2>&1 | sed 's/^/  [TUNNEL] /' &
else
    echo -e "${YELLOW}[5/5] cloudflared 미설치 — 터널 스킵${NC}"
fi

echo ""
echo -e "${GREEN}=== 전체 실행 중 ===${NC}"
echo -e "  API 서버:  http://localhost:8000"
echo -e "  Admin 웹:  http://localhost:5173"
echo -e "  공개 API:  https://api.seungmink.dev"
echo -e "  공개 웹:   https://cryptobot-eight.vercel.app"
echo -e "  종료: Ctrl+C"
echo ""

# 모든 백그라운드 프로세스 대기
wait
