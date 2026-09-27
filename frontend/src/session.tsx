import { createContext, useContext, type ReactNode } from "react";

export interface Me {
  role: "admin" | "auditor" | "inference";
  email: string;
  displayName: string;
  userId: string;
  workspaceId: string;
  workspaceName: string;
  homeId: string;
  homeName: string;
  monthlyUsd: number;
  spentUsd: number;
  period: string;
}

const SessionContext = createContext<Me | null>(null);

export function SessionProvider({ value, children }: { value: Me; children: ReactNode }) {
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Me {
  const value = useContext(SessionContext);
  if (!value) {
    throw new Error("Session is missing.");
  }
  return value;
}
