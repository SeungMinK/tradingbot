// #332 Phase 1: shadcn/ui 표준 cn() 헬퍼 (clsx + tailwind-merge)
import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
