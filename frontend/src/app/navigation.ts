// The sidebar is generated from this list and filtered by the signed-in key's permissions, so a role
// only ever sees pages its API calls are allowed to serve. Labels are English, translated where rendered.
import { msg } from "@/i18n";
export interface NavItem {
  to: string;
  label: string;
  icon: string;
  permission: string;
  end?: boolean;
}

export interface NavSection {
  title: string;
  items: NavItem[];
}

export const NAV: NavSection[] = [
  {
    title: msg("Operations"),
    items: [
      { to: "/cases", label: msg("Case queue"), icon: "▤", permission: "cases:read" },
      { to: "/transactions", label: msg("Transactions"), icon: "⇄", permission: "transactions:read" },
      { to: "/investigations", label: msg("Investigations"), icon: "◎", permission: "cases:read" },
      { to: "/receipts", label: msg("Receipts"), icon: "▧", permission: "receipts:read" },
      { to: "/monitoring", label: msg("Model monitoring"), icon: "∿", permission: "monitoring:read" },
    ],
  },
  {
    title: msg("Merchant"),
    items: [
      { to: "/score", label: msg("Scoring console"), icon: "▶", permission: "payments:score" },
      { to: "/verify", label: msg("Verify receipt"), icon: "✓", permission: "receipts:verify" },
    ],
  },
  {
    title: msg("Admin"),
    items: [
      { to: "/admin", label: msg("Dashboard"), icon: "◧", permission: "admin:read", end: true },
      { to: "/admin/rails", label: msg("Payment rails"), icon: "≡", permission: "admin:read" },
      { to: "/admin/rules", label: msg("Rules"), icon: "⚑", permission: "admin:read" },
      { to: "/models", label: msg("Models"), icon: "◆", permission: "models:read" },
      { to: "/admin/events", label: msg("Event pipeline"), icon: "⇉", permission: "admin:read" },
      { to: "/admin/clients", label: msg("API clients"), icon: "⚿", permission: "clients:manage" },
      { to: "/admin/audit", label: msg("Audit log"), icon: "☰", permission: "audit:read" },
      { to: "/admin/system", label: msg("System"), icon: "⚙", permission: "admin:read" },
    ],
  },
];
