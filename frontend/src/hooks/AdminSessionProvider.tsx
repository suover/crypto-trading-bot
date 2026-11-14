import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { AdminSessionContext, type SessionStatus } from './use-admin-session';
import {
  authAvailable,
  AuthError,
  currentAdmin,
  loginAdmin,
  logoutAdmin,
  SESSION_EXPIRED,
} from '@/services/auth-api';

export function AdminSessionProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const [status, setStatus] = useState<SessionStatus>('unchecked');
  const [username, setUsername] = useState<string | null>(null);
  const epoch = useRef(0);
  const checking = useRef(false);
  const available = authAvailable();
  const clear = useCallback(() => {
    client.clear();
    setUsername(null);
  }, [client]);
  const check = useCallback(() => {
    if (!available || checking.current) return;
    checking.current = true;
    const version = ++epoch.current;
    setStatus('checking');
    void currentAdmin()
      .then((name) => {
        if (epoch.current !== version) return;
        setUsername(name);
        setStatus('authenticated');
      })
      .catch((error: unknown) => {
        if (epoch.current !== version) return;
        clear();
        setStatus(error instanceof AuthError && error.status === 401 ? 'guest' : 'error');
      })
      .finally(() => {
        if (epoch.current === version) checking.current = false;
      });
  }, [available, clear]);
  useEffect(() => {
    const expired = () => {
      ++epoch.current;
      checking.current = false;
      clear();
      setStatus('guest');
    };
    window.addEventListener(SESSION_EXPIRED, expired);
    const generation = epoch;
    return () => {
      ++generation.current;
      checking.current = false;
      window.removeEventListener(SESSION_EXPIRED, expired);
    };
  }, [clear]);
  async function login(name: string, password: string) {
    const version = ++epoch.current;
    checking.current = false;
    clear();
    try {
      const admin = await loginAdmin(name, password);
      if (epoch.current !== version) throw new AuthError();
      setUsername(admin);
      setStatus('authenticated');
    } catch (error) {
      if (epoch.current === version) setStatus('guest');
      throw error;
    }
  }
  async function logout() {
    const version = ++epoch.current;
    checking.current = false;
    clear();
    setStatus('checking');
    try {
      await logoutAdmin();
      if (epoch.current === version) setStatus('guest');
    } catch {
      // Do not claim server logout succeeded or display cached data after a network failure.
      if (epoch.current === version) setStatus('error');
    }
  }
  return (
    <AdminSessionContext.Provider value={{ available, status, username, check, login, logout }}>
      {children}
    </AdminSessionContext.Provider>
  );
}
