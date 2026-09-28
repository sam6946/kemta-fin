import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { authApi, type User } from "../api/auth";
import { ApiError, tokens } from "../api/client";

type AuthState = {
  user: User | null;
  loading: boolean;
  login: (phone: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshProfile: () => Promise<void>;
  setSession: (access: string, refresh: string) => Promise<void>;
  hasCapability: (capability: string) => boolean;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshProfile = useCallback(async () => {
    if (!tokens.access) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      setUser(await authApi.me());
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) tokens.clear();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshProfile();
  }, [refreshProfile]);

  const login = useCallback(async (phone: string, password: string) => {
    const session = await authApi.login({ phone, password });
    tokens.set(session.access, session.refresh);
    setUser(session.user);
  }, []);

  const setSession = useCallback(async (access: string, refresh: string) => {
    tokens.set(access, refresh);
    setUser(await authApi.me());
  }, []);

  const logout = useCallback(async () => {
    const refresh = tokens.refresh;
    try {
      if (refresh) await authApi.logout(refresh);
    } catch {
      // La déconnexion locale reste garantie même si le serveur est injoignable.
    } finally {
      tokens.clear();
      setUser(null);
    }
  }, []);

  const hasCapability = useCallback(
    (capability: string) => Boolean(user?.capabilities?.includes(capability)),
    [user],
  );

  const value = useMemo<AuthState>(
    () => ({ user, loading, login, logout, refreshProfile, setSession, hasCapability }),
    [user, loading, login, logout, refreshProfile, setSession, hasCapability],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth doit être utilisé dans un AuthProvider.");
  return context;
}
