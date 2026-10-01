"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Brand, Login } from "@/components/console/Console";
import { WorkspaceProvider } from "@/lib/configurations/client";
import { onSessionExpired } from "@/lib/configurations/session-events";
import { api, ApiError, errorText } from "@/lib/platform/client";
import type { Account } from "@/lib/platform/client";

export interface WorkspaceSessionValue {
  account: Account;
  email: string;
  /** viewer | operator | admin. Workspace documents are writable by any signed-in owner. */
  role: string;
  /** Operator or admin: may dispatch work in the existing application workflow. */
  operator: boolean;
  signOut: () => Promise<void>;
  signingOut: boolean;
  signOutError: string | null;
}
const SessionContext = createContext<WorkspaceSessionValue | null>(null);

/**
 * The existing client-side session gate (Console pattern): `auth/me` once per
 * mount, the shared sign-in form on 401, and back to sign-in when any request
 * reports an ended session.
 */
export function WorkspaceSession({ children }: { children: ReactNode }) {
  const [account, setAccount] = useState<Account | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try { setAccount(await api<Account>("auth/me")); setError(null); }
    catch (cause) { setAccount(null); if (!(cause instanceof ApiError && cause.status === 401)) setError(errorText(cause)); }
  }, []);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 0); return () => window.clearTimeout(timer); }, [load]);
  useEffect(() => onSessionExpired(() => setAccount(null)), []);
  const signOut = useCallback(async () => {
    setSigningOut(true); setSignOutError(null);
    try { await api("auth/logout", {}); setAccount(null); }
    catch (cause) { setSignOutError(errorText(cause)); }
    finally { setSigningOut(false); }
  }, []);
  const value = useMemo<WorkspaceSessionValue | null>(() => account ? {
    account, email: account.user.email, role: account.user.role, operator: account.user.role !== "viewer", signOut, signingOut, signOutError,
  } : null, [account, signOut, signingOut, signOutError]);
  if (account === undefined) return <div className="console-shell"><main className="console-auth"><Brand /><p role="status">Checking your session…</p></main></div>;
  if (!value) return <div className="console-shell"><Login initialError={error} onLogin={load} /></div>;
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

/** The signed-in account and sign-out; available under `WorkspaceSession`. */
export function useSession(): WorkspaceSessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession() needs WorkspaceSession above it.");
  return value;
}

/** Everything a Configurations route needs above its page: the session gate and the workspace document. */
export function ConfigurationsRoot({ children }: { children: ReactNode }) {
  return <WorkspaceSession><WorkspaceProvider>{children}</WorkspaceProvider></WorkspaceSession>;
}
