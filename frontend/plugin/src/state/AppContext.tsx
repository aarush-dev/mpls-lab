import React, { createContext, Dispatch, PropsWithChildren, useContext, useReducer } from 'react';
import { AppAction, AppState, appReducer, initialAppState } from './reducer';

interface AppContextValue {
  state: AppState;
  dispatch: Dispatch<AppAction>;
}

const AppContext = createContext<AppContextValue | undefined>(undefined);

export function AppProvider({ children }: PropsWithChildren<{}>) {
  const [state, dispatch] = useReducer(appReducer, initialAppState);

  return <AppContext.Provider value={{ state, dispatch }}>{children}</AppContext.Provider>;
}

export function useAppState(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) {
    throw new Error('useAppState must be used within an AppProvider');
  }
  return ctx;
}
