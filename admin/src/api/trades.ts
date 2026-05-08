import client from "./client";
import type { TradesResponse, TradeStats, DailyReturn, Trade } from "../types/trades";

export interface TradeQueryParams {
  page?: number;
  limit?: number;
  coin?: string;
  strategy?: string;
  side?: string;
  market?: string;  // #266
  date_from?: string;
  date_to?: string;
}

export async function getTrades(params: TradeQueryParams = {}): Promise<TradesResponse> {
  const filtered = Object.fromEntries(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== "")
  );
  const { data } = await client.get<TradesResponse>("/trades", { params: filtered });
  return data;
}

export async function getTrade(id: number): Promise<Trade> {
  const { data } = await client.get<Trade>(`/trades/${id}`);
  return data;
}

export async function getTradeStats(days: number = 7): Promise<TradeStats> {
  const { data } = await client.get<TradeStats>("/trades/stats", { params: { days } });
  return data;
}

export async function getDailyReturns(days: number = 30): Promise<DailyReturn[]> {
  const { data } = await client.get<DailyReturn[]>("/trades/daily", { params: { days } });
  return data;
}
