import { createContext, useContext, useState, type ReactNode } from "react";

interface SettingsValue {
  autoRefresh: boolean;
  setAutoRefresh: (value: boolean) => void;
  intervalMs: number;
}

const SettingsContext = createContext<SettingsValue | undefined>(undefined);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const [autoRefresh, setAutoRefresh] = useState(false);
  return (
    <SettingsContext.Provider value={{ autoRefresh, setAutoRefresh, intervalMs: 30000 }}>
      {children}
    </SettingsContext.Provider>
  );
}

export function useSettings(): SettingsValue {
  const ctx = useContext(SettingsContext);
  if (!ctx) throw new Error("useSettings must be used within SettingsProvider");
  return ctx;
}

export function useRefetchInterval(): number | false {
  const { autoRefresh, intervalMs } = useSettings();
  return autoRefresh ? intervalMs : false;
}
