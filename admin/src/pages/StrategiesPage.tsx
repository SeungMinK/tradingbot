import { useEffect, useState } from "react";
import { getStrategies, getActivationHistory, activateStrategy, deactivateStrategy } from "../api/strategies";
import { getAllCoinStrategies } from "../api/coinStrategy";
import type { CoinStrategyConfig } from "../api/coinStrategy";
import type { Strategy, StrategyActivation } from "../types/strategies";
import ConfirmDialog from "../components/ConfirmDialog";
import { getMarketStateKR } from "../utils/indicatorDescriptions";
import client from "../api/client";
import StrategyCard from "../components/strategies/StrategyCard";
import ParamsEditor from "../components/strategies/ParamsEditor";

const MARKET_SECTIONS = [
  { state: "sideways", label: "횡보장 전략", emoji: "➡️", desc: "변동이 적은 박스권에서 유리" },
  { state: "bullish", label: "상승장 전략", emoji: "📈", desc: "상승 추세에서 유리" },
  { state: "bearish", label: "하락장 전략", emoji: "📉", desc: "하락 추세에서 유리" },
] as const;

export default function StrategiesPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [, setActivations] = useState<StrategyActivation[]>([]);
  const [, setCoinConfigs] = useState<CoinStrategyConfig[]>([]);
  const [coinStrategies, setCoinStrategies] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [confirm, setConfirm] = useState<{ name: string; action: "activate" | "deactivate" } | null>(null);
  const [editingStrategy, setEditingStrategy] = useState<Strategy | null>(null);

  const fetchData = async () => {
    try {
      const [strats, hist, coinCfg, coinStrats] = await Promise.all([
        getStrategies(),
        getActivationHistory(20),
        getAllCoinStrategies().catch(() => []),
        client.get("/market/coin-strategies").then(r => r.data).catch(() => []),
      ]);
      setStrategies(strats);
      setActivations(hist);
      setCoinConfigs(coinCfg);
      setCoinStrategies(coinStrats);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handleToggle = async () => {
    if (!confirm) return;
    try {
      if (confirm.action === "activate") {
        await activateStrategy(confirm.name, "Dashboard에서 수동 활성화");
      } else {
        await deactivateStrategy(confirm.name, "Dashboard에서 수동 비활성화");
      }
      await fetchData();
    } finally {
      setConfirm(null);
    }
  };

  if (loading) return <div className="loading">로딩 중...</div>;

  const hasSwitching = strategies.some((s) => s.status === "shutting_down");

  return (
    <div>
      <div className="page-header">
        <h1>전략 관리</h1>
        <p>매매 전략 및 코인별 전략 설정</p>
      </div>

      <>

      {/* 코인별 전략 현황 */}
      {coinStrategies.length > 0 && (
        <div className="card mb-6">
          <div className="card-title">코인별 적용 전략 (시장 상태 기반 자동 선택)</div>
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>코인</th>
                  <th>시장 상태</th>
                  <th>적용 전략</th>
                  <th>최근 신호</th>
                  <th>보유</th>
                </tr>
              </thead>
              <tbody>
                {coinStrategies.map((cs: any) => (
                  <tr key={cs.coin} className={cs.holding ? "bg-success/5" : ""}>
                    <td className="font-semibold">{cs.coin?.replace("KRW-", "")}</td>
                    <td>
                      <span className={`badge ${cs.market_state === "bullish" ? "badge-green" : cs.market_state === "bearish" ? "badge-red" : "badge-yellow"}`}>
                        {getMarketStateKR(cs.market_state || "")}
                      </span>
                    </td>
                    <td><span className="badge badge-purple">{cs.strategy}</span></td>
                    <td>
                      <span className={`badge ${cs.signal_type === "buy" ? "badge-green" : cs.signal_type === "sell" ? "badge-red" : "badge-yellow"}`}>
                        {cs.signal_type === "buy" ? "매수" : cs.signal_type === "sell" ? "매도" : "HOLD"}
                      </span>
                    </td>
                    <td>{cs.holding ? <span className="badge badge-green">보유중</span> : "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Strategy sections by market state */}
      {MARKET_SECTIONS.map((section) => {
        const sectionStrategies = strategies.filter((s) =>
          s.market_states.includes(section.state)
        );
        if (sectionStrategies.length === 0) return null;

        return (
          <div key={section.state} className="mb-7">
            <div className="flex items-center gap-2.5 mb-3.5">
              <span className="text-xl">{section.emoji}</span>
              <h2 className="m-0 text-lg">{section.label}</h2>
              <span className="text-sm text-muted-foreground">{section.desc}</span>
            </div>
            <div className="strategy-grid">
              {sectionStrategies.map((s) => (
                <StrategyCard
                  key={`${section.state}-${s.name}`}
                  strategy={s}
                  hasSwitching={hasSwitching}
                  activeCoins={coinStrategies.filter((cs: any) => cs.strategy === s.name).map((cs: any) => cs.coin?.replace("KRW-", ""))}
                  onEdit={() => setEditingStrategy(s)}
                  onActivate={() => setConfirm({ name: s.name, action: "activate" })}
                  onDeactivate={() => setConfirm({ name: s.name, action: "deactivate" })}
                />
              ))}
            </div>
          </div>
        );
      })}

      {confirm && (
        <ConfirmDialog
          title={`전략 ${confirm.action === "activate" ? "전환" : "비활성화"}`}
          message={
            confirm.action === "activate"
              ? `'${confirm.name}' 전략으로 전환하시겠습니까? 기존 전략은 자동으로 종료됩니다.`
              : `'${confirm.name}' 전략을 비활성화하시겠습니까?`
          }
          onConfirm={handleToggle}
          onCancel={() => setConfirm(null)}
        />
      )}

      </>

      {editingStrategy && (
        <ParamsEditor
          strategy={editingStrategy}
          onClose={() => setEditingStrategy(null)}
          onSaved={fetchData}
        />
      )}
    </div>
  );
}
