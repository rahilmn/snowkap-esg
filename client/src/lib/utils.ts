import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(date: string | Date): string {
  return new Intl.DateTimeFormat("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(date));
}

export function formatCurrency(amount: number): string {
  if (amount >= 10_000_000) return `₹${(amount / 10_000_000).toFixed(1)}Cr`;
  if (amount >= 100_000) return `₹${(amount / 100_000).toFixed(1)}L`;
  return `₹${amount.toLocaleString("en-IN")}`;
}

export function truncate(str: string, len: number): string {
  return str.length > len ? str.slice(0, len) + "..." : str;
}

export function esgPillarColor(pillar: string): string {
  switch (pillar?.toLowerCase()) {
    case "environmental":
    case "e":
      return "text-esg-environmental";
    case "social":
    case "s":
      return "text-esg-social";
    case "governance":
    case "g":
      return "text-esg-governance";
    default:
      return "text-muted-foreground";
  }
}

export function esgPillarBg(pillar: string): string {
  switch (pillar?.toLowerCase()) {
    case "environmental":
    case "e":
      return "bg-emerald-50 border-emerald-200";
    case "social":
    case "s":
      return "bg-blue-50 border-blue-200";
    case "governance":
    case "g":
      return "bg-violet-50 border-violet-200";
    default:
      return "bg-gray-50 border-gray-200";
  }
}

export function confidenceLabel(score: number): string {
  if (score >= 0.8) return "High";
  if (score >= 0.5) return "Medium";
  return "Low";
}

export function confidenceColor(score: number): string {
  if (score >= 0.8) return "text-red-600";
  if (score >= 0.5) return "text-amber-600";
  return "text-green-600";
}
