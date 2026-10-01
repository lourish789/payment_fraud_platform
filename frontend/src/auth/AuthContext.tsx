import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { setApiKey, setUnauthorizedHandler } from "@/api/client";
import { auth } from "@/api/endpoints";
import type { Me } from "@/api/types";

// The console authenticates with the same API keys as every other client. The key is kept in
// sessionStorage (cleared when the tab closes, never shared across tabs) and sent only to this origin;
// the page's CSP forbids third-party scripts and connections. See docs/FRONTEND.md for the trade-off
// against cookie sessions / SSO.
const STORAGE_KEY = "payguard.apiKey";

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (key: string) => Promise<Me>;
  logout: () => void;
  can: (permission: string) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

function readKey(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeKey(key: string | null) {
  try {
    if (key) sessionStorage.setItem(STORAGE_KEY, key);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage blocked: the session lasts until reload */
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const logout = useCallback(() => {
    writeKey(null);
    setApiKey(null);
    setMe(null);
    qc.clear();
  }, [qc]);

  const login = useCallback(async (key: string) => {
    const who = await auth.me(key.trim());
    writeKey(key.trim());
    setApiKey(key.trim());
    setMe(who);
    return who;
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(logout); // a revoked key ends the session everywhere in the app
    const saved = readKey();
    if (!saved) {
      setLoading(false);
      return;
    }
    login(saved)
      .catch(() => logout())
      .finally(() => setLoading(false));
  }, [login, logout]);

  const value = useMemo<AuthState>(
    () => ({ me, loading, login, logout, can: (p) => !!me?.permissions.includes(p) }),
    [me, loading, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}

/** Where each role lands after signing in. */
export function homeFor(me: Me): string {
  if (me.role === "admin") return "/admin";
  if (me.role === "analyst") return "/cases";
  return "/score";
}
