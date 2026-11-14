import { createContext, useContext } from 'react';
export type SessionStatus = 'unchecked' | 'checking' | 'authenticated' | 'guest' | 'error';
export const AdminSessionContext = createContext<{
  available: boolean;
  status: SessionStatus;
  username: string | null;
  check: () => void;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
} | null>(null);
export function useAdminSession() {
  const value = useContext(AdminSessionContext);
  if (!value) throw new Error('AdminSessionProvider is required');
  return value;
}
