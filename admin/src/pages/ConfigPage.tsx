import { useEffect, useState, useCallback } from "react";
import { getAllConfig, updateConfig } from "../api/config";
import type { ConfigItem } from "../api/config";
import client from "../api/client";
import { cn } from "@/lib/utils";

const CATEGORY_LABELS: Record<string, string> = {
  coin: "코인 선별",
  notification: "알림 설정",
  bot: "봇 설정",
  risk: "리스크 관리",
  strategy: "전략 파라미터",
};

const CATEGORY_ORDER = ["coin", "bot", "risk", "notification"];

const LIMIT_LABELS: Record<string, string> = {
  stop_loss_pct: "손절률 (%)",
  trailing_stop_pct: "트레일링 스탑 (%)",
  max_position_per_coin_pct: "종목당 최대 포지션 (%)",
  max_coins: "동시 모니터링 코인 수",
  min_balance_pct: "최소 유지 잔고 (원금 대비 %)",
  k_value: "K 값 (변동성 돌파)",
  bb_std: "볼린저 밴드 폭",
  rsi_oversold: "RSI 과매도 기준",
  aggression: "공격성",
};

export default function ConfigPage() {
  const [configs, setConfigs] = useState<ConfigItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<string | null>(null);
  const [editValues, setEditValues] = useState<Record<string, string>>({});
  const [hardLimits, setHardLimits] = useState<Record<string, { min: number; max: number }>>({});
  const loadConfigs = useCallback(async () => {
    try {
      const [data, limits] = await Promise.all([
        getAllConfig(),
        client.get("/llm/hard-limits").then((r) => r.data).catch(() => ({})),
      ]);
      setConfigs(data);
      setHardLimits(limits);
      const values: Record<string, string> = {};
      data.forEach((c) => (values[c.key] = c.value));
      setEditValues(values);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadConfigs();
  }, [loadConfigs]);

  const handleToggle = async (key: string, currentValue: string) => {
    const newValue = currentValue === "true" ? "false" : "true";
    setSaving(key);
    try {
      const updated = await updateConfig(key, newValue);
      setConfigs((prev) => prev.map((c) => (c.key === key ? updated : c)));
      setEditValues((prev) => ({ ...prev, [key]: newValue }));
    } catch {
      // ignore
    } finally {
      setSaving(null);
    }
  };

  const handleSave = async (key: string) => {
    const newValue = editValues[key];
    if (newValue === undefined) return;
    setSaving(key);
    try {
      const updated = await updateConfig(key, newValue);
      setConfigs((prev) => prev.map((c) => (c.key === key ? updated : c)));
    } catch {
      // ignore
    } finally {
      setSaving(null);
    }
  };

  if (loading) return <div className="loading">로딩 중...</div>;

  // 카테고리별 그룹핑
  const grouped: Record<string, ConfigItem[]> = {};
  configs.forEach((c) => {
    if (!grouped[c.category]) grouped[c.category] = [];
    grouped[c.category].push(c);
  });

  return (
    <div>
      <div className="page-header">
        <h1>Config</h1>
        <p>봇 설정 관리 - 변경 시 즉시 반영됩니다</p>
      </div>

      {CATEGORY_ORDER.filter((cat) => grouped[cat]).map((category) => (
        <div key={category}>
        <div className={cn("card", category === "coin" ? "mb-0" : "mb-5")}>
          <div className="card-title">{CATEGORY_LABELS[category] || category}</div>
          <div className="flex flex-col gap-4">
            {grouped[category].map((cfg) => (
              <div
                key={cfg.key}
                className="flex justify-between items-center py-3 border-b border-border last:border-b-0"
              >
                <div className="flex-1">
                  <div className="font-semibold mb-1">{cfg.display_name}</div>
                  <div className="text-xs text-muted-foreground">{cfg.description}</div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    key: <code>{cfg.key}</code>
                  </div>
                </div>
                <div className="ml-6 min-w-[140px] flex justify-end items-center gap-2">
                  {cfg.value_type === "bool" ? (
                    <button
                      onClick={() => handleToggle(cfg.key, cfg.value)}
                      disabled={saving === cfg.key}
                      className={cn(
                        "px-4 py-1.5 rounded-full border-none cursor-pointer text-sm font-semibold min-w-[60px] text-white transition-all",
                        cfg.value === "true" ? "bg-success" : "bg-muted-foreground",
                        saving === cfg.key && "opacity-60",
                      )}
                    >
                      {cfg.value === "true" ? "ON" : "OFF"}
                    </button>
                  ) : (
                    <>
                      <input
                        type={cfg.value_type === "int" || cfg.value_type === "float" ? "number" : "text"}
                        step={cfg.value_type === "float" ? "0.1" : "1"}
                        value={editValues[cfg.key] ?? ""}
                        onChange={(e) => setEditValues((prev) => ({ ...prev, [cfg.key]: e.target.value }))}
                        className="w-20 px-2 py-1.5 rounded-md border border-border bg-card text-foreground text-sm text-right"
                      />
                      <button
                        onClick={() => handleSave(cfg.key)}
                        disabled={saving === cfg.key || editValues[cfg.key] === cfg.value}
                        className={cn(
                          "px-3 py-1.5 rounded-md border-none text-xs",
                          editValues[cfg.key] !== cfg.value
                            ? "bg-primary text-primary-foreground cursor-pointer"
                            : "bg-muted text-muted-foreground cursor-default",
                          saving === cfg.key && "opacity-60",
                        )}
                      >
                        저장
                      </button>
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* risk 카테고리 아래 하드 리밋 */}
        {category === "risk" && Object.keys(hardLimits).length > 0 && (
          <div className="card" style={{ marginTop: 12, marginBottom: 0 }}>
            <div className="card-title">LLM 하드 리밋 (읽기 전용)</div>
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 12 }}>
              LLM이 파라미터를 조절할 수 있는 최소/최대 범위. 코드에서 고정됨.
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              {Object.entries(hardLimits).map(([key, { min, max }]) => (
                <div key={key} style={{ padding: "8px 12px", borderRadius: 6, background: "var(--bg-secondary)" }}>
                  <div style={{ fontSize: 12, fontWeight: 600 }}>{LIMIT_LABELS[key] || key}</div>
                  <div style={{ fontSize: 14, color: "#4a9eff" }}>{min} ~ {max}</div>
                </div>
              ))}
            </div>
          </div>
        )}
        </div>
      ))}
    </div>
  );
}
