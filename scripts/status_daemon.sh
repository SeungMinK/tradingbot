#!/bin/bash
# TradingBot 백그라운드 프로세스 상태 확인.

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

PIDS_DIR="$PROJECT_ROOT/pids"
LOGS_DIR="$PROJECT_ROOT/logs/daemon"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${GREEN}=== TradingBot 프로세스 상태 ===${NC}"
echo ""

if [[ ! -d "$PIDS_DIR" ]]; then
    echo -e "${YELLOW}PID 디렉토리 없음 — 실행 기록 없음${NC}"
    exit 0
fi

running=0
dead=0
for name in api bot bot_kis_kr bot_kis_us news tunnel; do
    pid_file="$PIDS_DIR/$name.pid"
    log_file="$LOGS_DIR/$name.log"

    if [[ ! -f "$pid_file" ]]; then
        printf "  ${YELLOW}%-10s${NC} 시작 기록 없음\n" "$name"
        continue
    fi

    pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
        # 프로세스 메모리/CPU 간이 표시
        mem=$(ps -o rss= -p "$pid" 2>/dev/null | awk '{printf "%.1fMB", $1/1024}')
        cpu=$(ps -o %cpu= -p "$pid" 2>/dev/null | awk '{printf "%.1f%%", $1}')
        log_size=""
        if [[ -f "$log_file" ]]; then
            log_size=$(ls -lh "$log_file" 2>/dev/null | awk '{print $5}')
        fi
        printf "  ${GREEN}%-10s${NC} ✅ PID %-6s | CPU %-6s | MEM %-8s | 로그 %s\n" \
            "$name" "$pid" "$cpu" "$mem" "${log_size:-없음}"
        running=$((running + 1))
    else
        printf "  ${RED}%-10s${NC} ❌ PID %-6s (죽었음)\n" "$name" "$pid"
        dead=$((dead + 1))
    fi
done

echo ""
echo -e "${CYAN}실행 중: $running개${NC}  ${RED}죽음: $dead개${NC}"

if [[ $dead -gt 0 ]]; then
    echo ""
    echo -e "${YELLOW}죽은 프로세스 재시작:${NC}"
    echo "  bash scripts/stop_daemon.sh   # 정리"
    echo "  bash scripts/start_daemon.sh  # 재시작"
fi
