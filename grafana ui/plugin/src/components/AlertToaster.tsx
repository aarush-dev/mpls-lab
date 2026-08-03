import React, { createContext, PropsWithChildren, useCallback, useContext, useMemo, useRef, useState } from 'react';
import { css, keyframes } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2, Icon } from '@grafana/ui';

export interface Toast {
  id: number;
  title: string;
  text?: string;
  severity: 'critical' | 'warning';
}

interface ToasterApi {
  notify: (t: Omit<Toast, 'id'>) => void;
}

const ToasterContext = createContext<ToasterApi>({ notify: () => undefined });

/** Fire a toast from anywhere in the app. */
export function useToaster(): ToasterApi {
  return useContext(ToasterContext);
}

const AUTO_DISMISS_MS = 5000;

/**
 * Global toast host. Renders a fixed top-right stack that shows on every page (it wraps the whole
 * routed app). Each toast auto-dismisses after 5s. Ids come from a counter (no Date/Math.random).
 */
export function AlertToasterProvider({ children }: PropsWithChildren<{}>) {
  const styles = useStyles2(getStyles);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const idRef = useRef(0);

  const notify = useCallback((t: Omit<Toast, 'id'>) => {
    const id = (idRef.current += 1);
    setToasts((prev) => [...prev, { ...t, id }]);
    setTimeout(() => setToasts((prev) => prev.filter((x) => x.id !== id)), AUTO_DISMISS_MS);
  }, []);

  const api = useMemo(() => ({ notify }), [notify]);

  return (
    <ToasterContext.Provider value={api}>
      {children}
      <div className={styles.stack} aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={t.severity === 'critical' ? styles.critical : styles.warning} role="alert">
            <Icon name={t.severity === 'critical' ? 'exclamation-circle' : 'exclamation-triangle'} />
            <div>
              <div className={styles.title}>{t.title}</div>
              {t.text && <div className={styles.text}>{t.text}</div>}
            </div>
          </div>
        ))}
      </div>
    </ToasterContext.Provider>
  );
}

const slideIn = keyframes`
  from { transform: translateX(16px); opacity: 0; }
  to   { transform: translateX(0);    opacity: 1; }
`;
const fadeOut = keyframes`
  to { opacity: 0; transform: translateX(16px); }
`;

const getStyles = (theme: GrafanaTheme2) => {
  const base = css`
    display: flex;
    align-items: flex-start;
    gap: ${theme.spacing(1)};
    min-width: 260px;
    max-width: 360px;
    padding: ${theme.spacing(1.5)};
    border-radius: ${theme.shape.radius.default};
    color: #fff;
    box-shadow: ${theme.shadows.z3};
    // slide in, hold, then fade over the last 0.6s of the 5s lifetime
    animation: ${slideIn} 0.2s ease-out, ${fadeOut} 0.6s ease-in 4.4s forwards;
  `;
  return {
    stack: css`
      position: fixed;
      top: ${theme.spacing(2)};
      right: ${theme.spacing(2)};
      z-index: ${theme.zIndex.portal};
      display: flex;
      flex-direction: column;
      gap: ${theme.spacing(1)};
      pointer-events: none;
    `,
    critical: css`
      ${base};
      background: ${theme.colors.error.main};
    `,
    warning: css`
      ${base};
      background: ${theme.colors.warning.main};
      color: ${theme.colors.warning.contrastText};
    `,
    title: css`
      font-weight: ${theme.typography.fontWeightMedium};
    `,
    text: css`
      font-size: ${theme.typography.bodySmall.fontSize};
      opacity: 0.9;
    `,
  };
};
