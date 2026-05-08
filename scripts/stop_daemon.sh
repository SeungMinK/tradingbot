#!/bin/bash
# TradingBot 백그라운드 종료.

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

PIDS_DIR="$PROJECT_ROOT/pids"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

if [[ ! -d "$PIDS_DIR" ]]; then
    echo -e "${YELLOW}실행 중인 프로세스 없음 ($PIDS_DIR 없음)${NC}"
    exit 0
fi

echo -e "${GREEN}=== TradingBot 백그라운드 종료 ===${NC}"

stopped=0
not_running=0
for pid_file in "$PIDS_DIR"/*.pid; do
    [[ -f "$pid_file" ]] || continue
    name=$(basename "$pid_file" .pid)
    pid=$(cat "$pid_file")

    if kill -0 "$pid" 2>/dev/null; then
        echo -e "  [$name] 종료 중 (PID $pid)..."
        kill -TERM "$pid" 2>/dev/null || true
        # TERM 5초 기다리고 그래도 살아있으면 KILL
        for _ in 1 2 3 4 5; do
            sleep 1
            kill -0 "$pid" 2>/dev/null || break
        done
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "  ${YELLOW}[$name] TERM 무반응 — KILL${NC}"
            kill -KILL "$pid" 2>/dev/null || true
        fi
        stopped=$((stopped + 1))
    else
        not_running=$((not_running + 1))
    fi
    rm -f "$pid_file"
done

echo ""
if [[ $stopped -gt 0 ]]; then
    echo -e "${GREEN}종료 완료: $stopped개${NC}"
fi
if [[ $not_running -gt 0 ]]; then
    echo -e "${YELLOW}이미 종료됨: $not_running개 (PID 파일 정리)${NC}"
fi
