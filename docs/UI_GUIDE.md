# UI 개선 가이드 (#329~#338)

이슈 #329 후속 — UI 개선 도구 + 도입 결과 정리.

## 채택 Stack (전부 무료)

| 도구 | 역할 | 상태 |
|---|---|---|
| **shadcn/ui** | 컴포넌트 라이브러리 (copy-paste) | ✅ #332 도입 |
| **Tailwind v4** | 디자인 토큰 + 유틸리티 클래스 | ✅ #332 도입 |
| **Claude Code** | LLM 협업 (스크린샷 + 컨텍스트) | ✅ 보유 |
| **Storybook 9** | 컴포넌트 카탈로그 (선택) | ⏳ 후속 작업 |
| **Chromatic** | 시각 회귀 테스트 (free tier) | 📝 #338 셋업 |

## 디자인 토큰

`admin/src/styles/global.css` `@theme` 블록:

```css
@theme {
  --color-primary: hsl(217 91% 60%);
  --color-success: hsl(142 71% 45%);
  --color-destructive: hsl(0 72% 51%);
  --color-muted: hsl(210 40% 96%);
  --color-muted-foreground: hsl(215 16% 47%);
  --color-border: hsl(214 32% 91%);
  --radius-md: 0.5rem;
}
```

**사용법** (Tailwind utility class):
- `bg-primary`, `text-primary-foreground`
- `text-success`, `text-destructive`, `text-muted-foreground`
- `border-border`, `bg-card`
- `rounded-md`, `rounded-lg`

## 컴포넌트 (`admin/src/components/ui/`)

shadcn 표준:
- `Button` (6 variants × 4 sizes)
- `Card` / `CardHeader` / `CardTitle` / `CardContent`
- `Badge` (default/secondary/destructive/outline/success/warning)
- `Input`
- `Table` / `TableHeader` / `TableBody` / `TableRow` / `TableHead` / `TableCell`

## 도메인 컴포넌트 (`admin/src/components/`)

cryptobot 특화:
- `BotStatusBanner` — 봇 상태 + 잔고 + 오늘 매매 + EOD 카운트다운
- `PnLHero` — 누적 손익 큰 강조 (코인 봇 대시보드)
- `TradeRow` — 거래 한 줄 카드 (재사용)
- `StatCard` — 기본 KPI 카드 (shadcn Card 기반)

## Chromatic 시각 회귀 테스트 셋업

### 1. Chromatic 가입 (무료)
1. https://www.chromatic.com/ 가입
2. GitHub 연동 → cryptobot repo 선택
3. Project Token 복사

### 2. GitHub Secrets 추가
1. https://github.com/SeungMinK/tradingbot/settings/secrets/actions
2. `New repository secret`
3. Name: `CHROMATIC_PROJECT_TOKEN`
4. Value: 위에서 복사한 토큰

### 3. Storybook 도입 (필수, 후속 PR)

```bash
cd admin
npx storybook@latest init
# 프롬프트에서 React + Vite 선택
# Playwright는 No 선택 (불필요)

# stories 작성
# admin/src/components/ui/button.stories.tsx 등
```

### 4. workflow 자동 실행

PR 생성 시 `.github/workflows/chromatic.yml`이 자동 실행:
- Storybook 빌드
- Chromatic 업로드
- PR에 시각 변화 diff 표시

### 5. PR 리뷰

Chromatic UI에서:
- 변경된 story들 시각 비교
- 의도적 변경 → Accept
- 회귀 → Reject + 코드 fix

## 마이그레이션 진행 상황

- [x] Phase 1 — shadcn + Tailwind 셋업 (#332)
- [x] Phase 2 — 핵심 컴포넌트 (BotStatusBanner / PnLHero / TradeRow) (#333, #334)
- [x] Phase 3 — DashboardPage 코인 탭 적용 (#335, #336)
- [ ] Phase 3.5 — DashboardPage 나머지 섹션 + Public + Trades / Signals / Strategies
- [x] Phase 4 — Chromatic workflow 셋업 (#338)
- [ ] Phase 4.5 — Storybook 도입 + stories 작성

## LLM 협업 패턴

UI 변경 요청 시:
1. **현재 디자인 캡쳐** → Claude에게 첨부
2. **목표 설명** ("이 카드를 더 강조하고 싶어, OR_low 손절가 같이 보이게")
3. **참조 컴포넌트 제시** ("StatCard 처럼")
4. Claude가 shadcn 컴포넌트 + Tailwind 클래스로 구현
5. `npm run build` + 시각 확인 → 머지

shadcn 컴포넌트가 평문 코드라서 LLM이 직접 수정 가능 — 이게 핵심 가치.
