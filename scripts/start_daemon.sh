#!/bin/bash
# CryptoBot 백그라운드 실행 — 터미널 닫아도 계속 돌아감.
#
# 사용법:
#   bash scripts/start_daemon.sh        # 시작
#   bash scripts/stop_daemon.sh         # 종료
#   bash scripts/status_daemon.sh       # 상태 확인
#   tail -f logs/daemon/bot.log         # 실시간 로그
#
# 원리:
#   - nohup + setsid로 SIGHUP 차단
#   - PID를 pids/ 디렉토리에 저장 (종료 스크립트가 참조)
#   - stdout/stderr는 logs/daemon/ 로 리다이렉트

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

PIDS_DIR="$PROJECT_ROOT/pids"
LOGS_DIR="$PROJECT_ROOT/logs/daemon"
mkdir -p "$PIDS_DIR" "$LOGS_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

# 이미 실행 중인지 확인
check_running() {
    local name=$1
    local pid_file="$PIDS_DIR/$name.pid"
    if [[ -f "$pid_file" ]]; then
        local pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "${YELLOW}이미 실행 중: $name (PID $pid)${NC}"
            return 0
        fi
    fi
    return 1
}

# 백그라운드 실행 + PID 저장
start_bg() {
    local name=$1
    local cmd=$2

    if check_running "$name"; then
        return 1
    fi

    local pid_file="$PIDS_DIR/$name.pid"
    local log_file="$LOGS_DIR/$name.log"

    # nohup + setsid로 터미널과 완전 분리 (SIGHUP 차단)
    nohup bash -c "$cmd" >> "$log_file" 2>&1 &
    local pid=$!

    # PID가 실제로 살아있는지 확인 (즉시 죽는 경우 대비)
    sleep 0.5
    if ! kill -0 "$pid" 2>/dev/null; then
        echo -e "${RED}[$name] 시작 실패 — 로그 확인: $log_file${NC}"
        return 1
    fi

    echo "$pid" > "$pid_file"
    echo -e "${GREEN}[$name] 시작됨 (PID $pid) — 로그: $log_file${NC}"
    return 0
}

echo -e "${GREEN}=== CryptoBot 백그라운드 시작 ===${NC}"
echo ""

# 1. API 서버
echo -e "${CYAN}[1/6] API 서버${NC}"
start_bg "api" ".venv/bin/uvicorn cryptobot.api.main:app --host 0.0.0.0 --port 8000 --app-dir src"

sleep 1

# 2. 봇
# caffeinate -i: Mac 유휴 절전 차단. 절전 진입 시 APScheduler 스레드가 멈춰
# 4h 헬스체크·hourly 등 주기 job이 misfire로 스킵되는 문제를 예방한다.
# 봇 프로세스 종료 시 caffeinate도 같이 종료됨.
echo -e "${CYAN}[2/6] 코인 봇 (Upbit)${NC}"
if command -v caffeinate &> /dev/null; then
    start_bg "bot" "caffeinate -i .venv/bin/python -m cryptobot.bot.main"
else
    start_bg "bot" ".venv/bin/python -m cryptobot.bot.main"
fi

sleep 1

# #262: KIS API key 로드 (.env)
KIS_KEY_SET=false
if [[ -f "$PROJECT_ROOT/.env" ]]; then
    KIS_APP_KEY=$(grep -E "^KIS_APP_KEY=" "$PROJECT_ROOT/.env" 2>/dev/null | cut -d= -f2- | tr -d ' "')
    if [[ -n "$KIS_APP_KEY" ]]; then
        KIS_KEY_SET=true
    fi
fi

# 3. 한국주식 봇
echo -e "${CYAN}[3/6] 한국주식 봇 (KIS)${NC}"
if [[ "$KIS_KEY_SET" == "true" ]]; then
    start_bg "bot_kis_kr" ".venv/bin/python -m cryptobot.entrypoints.run_kis_kr"
else
    echo -e "${YELLOW}  KIS_APP_KEY 미설정 — 한국주식 봇 스킵${NC}"
fi

sleep 1

# 4. 미국주식 봇
echo -e "${CYAN}[4/6] 미국주식 봇 (KIS)${NC}"
if [[ "$KIS_KEY_SET" == "true" ]]; then
    start_bg "bot_kis_us" ".venv/bin/python -m cryptobot.entrypoints.run_kis_us"
else
    echo -e "${YELLOW}  KIS_APP_KEY 미설정 — 미국주식 봇 스킵${NC}"
fi

sleep 1

# 5. 뉴스 수집기
echo -e "${CYAN}[5/6] 뉴스 수집기${NC}"
start_bg "news" ".venv/bin/python news-collector/collector.py"

sleep 1

# 6. Cloudflare Tunnel (선택)
if command -v cloudflared &> /dev/null; then
    echo -e "${CYAN}[6/6] Cloudflare Tunnel${NC}"
    start_bg "tunnel" "cloudflared tunnel run cryptobot-api"
else
    echo -e "${YELLOW}[6/6] cloudflared 미설치 — 터널 스킵${NC}"
fi

echo ""
echo -e "${GREEN}=== 백그라운드 실행 중 — 터미널 닫아도 계속 돕니다 ===${NC}"
echo -e "  상태 확인:  ${CYAN}bash scripts/status_daemon.sh${NC}"
echo -e "  종료:       ${CYAN}bash scripts/stop_daemon.sh${NC}"
echo -e "  실시간 로그: ${CYAN}tail -f logs/daemon/bot.log${NC}"
echo ""
echo -e "  ${YELLOW}참고: Admin 웹(vite)은 개발용이라 daemon으로 안 띄움.${NC}"
echo -e "        ${YELLOW}필요하면 별도 터미널에서 'cd admin && npm run dev'${NC}"
