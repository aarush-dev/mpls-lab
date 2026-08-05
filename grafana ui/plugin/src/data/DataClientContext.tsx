import React, { createContext, PropsWithChildren, useContext, useMemo } from 'react';
import { appConfig } from '../config';
import type { DataClient } from './DataClient';
import { HttpDataClient } from './HttpDataClient';

const DataClientContext = createContext<DataClient | undefined>(undefined);

function createDataClient(): DataClient {
  // Always the real backends — dataapi reads on apiBaseUrl, copilot /chat on copilotBaseUrl. No
  // mock path exists: a wrong answer can never be silently served (#66).
  return new HttpDataClient(
    appConfig.apiBaseUrl,
    appConfig.requestTimeoutMs,
    appConfig.copilotBaseUrl,
    appConfig.copilotTimeoutMs
  );
}

/**
 * Provides a single DataClient instance for the whole app (mounted once in App.tsx, alongside
 * AppProvider).
 */
export function DataClientProvider({ children }: PropsWithChildren<{}>) {
  const client = useMemo(() => createDataClient(), []);
  return <DataClientContext.Provider value={client}>{children}</DataClientContext.Provider>;
}

export function useDataClient(): DataClient {
  const ctx = useContext(DataClientContext);
  if (!ctx) {
    throw new Error('useDataClient must be used within a DataClientProvider');
  }
  return ctx;
}
