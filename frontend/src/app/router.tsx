import { lazy, Suspense, type ReactNode } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";
import { homeFor, useAuth } from "@/auth/AuthContext";
import { LoginPage } from "@/auth/LoginPage";
import { RequirePermission } from "@/auth/RequirePermission";
import { AppShell } from "@/components/layout/AppShell";
import { Spinner } from "@/components/ui";
import { useT } from "@/i18n";

// Pages are code-split per feature: an analyst never downloads the admin dashboard's charts.
const page = <T extends Record<string, any>>(load: () => Promise<T>, name: keyof T) =>
  lazy(() => load().then((m) => ({ default: m[name] })));

const AdminDashboardPage = page(() => import("@/features/dashboard/AdminDashboardPage"), "AdminDashboardPage");
const CaseQueuePage = page(() => import("@/features/cases/CaseQueuePage"), "CaseQueuePage");
const CaseDetailPage = page(() => import("@/features/cases/CaseDetailPage"), "CaseDetailPage");
const TransactionsPage = page(() => import("@/features/transactions/TransactionsPage"), "TransactionsPage");
const TransactionDetailPage = page(() => import("@/features/transactions/TransactionDetailPage"), "TransactionDetailPage");
const InvestigationsPage = page(() => import("@/features/investigations/InvestigationsPage"), "InvestigationsPage");
const ReceiptsPage = page(() => import("@/features/receipts/ReceiptsPage"), "ReceiptsPage");
const VerifyReceiptPage = page(() => import("@/features/receipts/ReceiptsPage"), "VerifyReceiptPage");
const MonitoringPage = page(() => import("@/features/monitoring/MonitoringPage"), "MonitoringPage");
const ScoringConsolePage = page(() => import("@/features/scoring/ScoringConsolePage"), "ScoringConsolePage");
const ModelsPage = page(() => import("@/features/models/ModelsPage"), "ModelsPage");
const RailsPage = page(() => import("@/features/admin/RailsPage"), "RailsPage");
const RulesPage = page(() => import("@/features/admin/RulesPage"), "RulesPage");
const EventsPage = page(() => import("@/features/admin/EventsPage"), "EventsPage");
const ClientsPage = page(() => import("@/features/admin/ClientsPage"), "ClientsPage");
const AuditPage = page(() => import("@/features/admin/AuditPage"), "AuditPage");
const SystemPage = page(() => import("@/features/admin/SystemPage"), "SystemPage");

function guard(permission: string, el: ReactNode) {
  return (
    <RequirePermission permission={permission}>
      <Suspense fallback={<div className="center"><Spinner /></div>}>{el}</Suspense>
    </RequirePermission>
  );
}

function Home() {
  const { me } = useAuth();
  return me ? <Navigate to={homeFor(me)} replace /> : <Navigate to="/login" replace />;
}

function NotFound() {
  const t = useT();
  return <div className="card"><div className="empty"><h2>{t("Page not found")}</h2><p><a href="/">{t("Go home")}</a></p></div></div>;
}

export const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    element: <RequirePermission><AppShell /></RequirePermission>,
    children: [
      { index: true, element: <Home /> },
      { path: "admin", element: guard("admin:read", <AdminDashboardPage />) },
      { path: "admin/rails", element: guard("admin:read", <RailsPage />) },
      { path: "admin/rules", element: guard("admin:read", <RulesPage />) },
      { path: "admin/events", element: guard("admin:read", <EventsPage />) },
      { path: "admin/clients", element: guard("clients:manage", <ClientsPage />) },
      { path: "admin/audit", element: guard("audit:read", <AuditPage />) },
      { path: "admin/system", element: guard("admin:read", <SystemPage />) },
      { path: "models", element: guard("models:read", <ModelsPage />) },
      { path: "cases", element: guard("cases:read", <CaseQueuePage />) },
      { path: "cases/:caseId", element: guard("cases:read", <CaseDetailPage />) },
      { path: "transactions", element: guard("transactions:read", <TransactionsPage />) },
      { path: "transactions/:transactionId", element: guard("transactions:read", <TransactionDetailPage />) },
      { path: "investigations", element: guard("cases:read", <InvestigationsPage />) },
      { path: "receipts", element: guard("receipts:read", <ReceiptsPage />) },
      { path: "verify", element: guard("receipts:verify", <VerifyReceiptPage />) },
      { path: "monitoring", element: guard("monitoring:read", <MonitoringPage />) },
      { path: "score", element: guard("payments:score", <ScoringConsolePage />) },
      { path: "*", element: <NotFound /> },
    ],
  },
]);
