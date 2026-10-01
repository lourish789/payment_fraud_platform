// The sidebar is generated from this list and filtered by the signed-in key's permissions, so a role
// only ever sees pages its API calls are allowed to serve.
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
    title: "Operations",
    items: [
      { to: "/cases", label: "Case queue", icon: "▤", permission: "cases:read" },
      { to: "/transactions", label: "Transactions", icon: "⇄", permission: "transactions:read" },
      { to: "/investigations", label: "Investigations", icon: "◎", permission: "cases:read" },
      { to: "/receipts", label: "Receipts", icon: "▧", permission: "receipts:read" },
      { to: "/monitoring", label: "Model monitoring", icon: "∿", permission: "monitoring:read" },
    ],
  },
  {
    title: "Merchant",
    items: [
      { to: "/score", label: "Scoring console", icon: "▶", permission: "payments:score" },
      { to: "/verify", label: "Verify receipt", icon: "✓", permission: "receipts:verify" },
    ],
  },
  {
    title: "Admin",
    items: [
      { to: "/admin", label: "Dashboard", icon: "◧", permission: "admin:read", end: true },
      { to: "/admin/rails", label: "Payment rails", icon: "≡", permission: "admin:read" },
      { to: "/admin/rules", label: "Rules", icon: "⚑", permission: "admin:read" },
      { to: "/models", label: "Models", icon: "◆", permission: "models:read" },
      { to: "/admin/events", label: "Event pipeline", icon: "⇉", permission: "admin:read" },
      { to: "/admin/clients", label: "API clients", icon: "⚿", permission: "clients:manage" },
      { to: "/admin/audit", label: "Audit log", icon: "☰", permission: "audit:read" },
      { to: "/admin/system", label: "System", icon: "⚙", permission: "admin:read" },
    ],
  },
];
