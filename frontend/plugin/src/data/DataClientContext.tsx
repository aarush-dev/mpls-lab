import React, { createContext, PropsWithChildren, useContext, useMemo } from 'react';
import { appConfig } from '../config';
import type { DataClient } from './DataClient';
import { MockDataClient } from './MockDataClient';

const DataClientContext = createContext<DataClient | undefined>(undefined);

function createDataClient(): DataClient {
  if (appConfig.mode === 'mock') {
    return new MockDataClient();
  }
  // HttpDataClient lands in a later milestone; until then `api` mode falls back to mock so the
  // app never crashes on an unimplemented client.
  return new MockDataClient();
}

/**
 * Provides a single DataClient instance for the whole app (mounted once in App.tsx, alongside
 * AppProvider). Mock mode gives a `MockDataClient` that's driven by the shared demo clock cursor.
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
