import React, { useCallback } from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2, SelectableValue } from '@grafana/data';
import { Button, RadioButtonGroup, Select, useStyles2 } from '@grafana/ui';
import { useAppDispatch, useAppState } from '../state/AppContext';
import { ClockMode } from '../state/reducer';

// Global time control (header). Live = auto-refresh every 5s, window follows now. History = frozen
// window ending now, picked from the range select (back to VictoriaMetrics' 30d retention).
// ponytail: relative ranges cover "view any recent window"; a full absolute date picker
//   (@grafana/ui TimeRangePicker) is the upgrade path if arbitrary start/end is ever needed.

const RANGES: Array<SelectableValue<number>> = [
  { label: 'Last 5m', value: 300 },
  { label: 'Last 15m', value: 900 },
  { label: 'Last 1h', value: 3600 },
  { label: 'Last 6h', value: 21600 },
  { label: 'Last 24h', value: 86400 },
  { label: 'Last 7d', value: 604800 },
  { label: 'Last 30d', value: 2592000 },
];

const MODES: Array<{ label: string; value: ClockMode }> = [
  { label: 'Live', value: 'live' },
  { label: 'History', value: 'history' },
];

const getStyles = (theme: GrafanaTheme2) =>
  css({
    display: 'flex',
    alignItems: 'center',
    gap: theme.spacing(1),
  });

const selectWrap = css({ minWidth: 130 });

export function TimeControl() {
  const styles = useStyles2(getStyles);
  const { mode, liveWindowSec, range } = useAppState();
  const dispatch = useAppDispatch();

  // Selected range length: live window in live mode, else the frozen span.
  const selectedSec = mode === 'live' ? liveWindowSec : Math.max(1, Math.round((range.toMs - range.fromMs) / 1000));

  const onMode = useCallback(
    (m: ClockMode) => dispatch({ type: 'SET_MODE', payload: { mode: m, nowMs: Date.now() } }),
    [dispatch]
  );

  const onRange = useCallback(
    (opt: SelectableValue<number>) => {
      const sec = opt.value ?? 900;
      const now = Date.now();
      if (mode === 'live') {
        dispatch({ type: 'SET_LIVE_WINDOW', payload: { sec, nowMs: now } });
      } else {
        // History: freeze a window of this length ending now.
        dispatch({ type: 'SET_RANGE', payload: { fromMs: now - sec * 1000, toMs: now } });
      }
    },
    [dispatch, mode]
  );

  const onRefresh = useCallback(() => dispatch({ type: 'REFRESH', payload: { nowMs: Date.now() } }), [dispatch]);

  return (
    <div className={styles}>
      <RadioButtonGroup options={MODES} value={mode} onChange={onMode} size="sm" />
      <div className={selectWrap}>
        <Select<number>
          options={RANGES}
          value={RANGES.find((r) => r.value === selectedSec) ?? { label: `${selectedSec}s`, value: selectedSec }}
          onChange={onRange}
          isSearchable={false}
        />
      </div>
      <Button
        variant="secondary"
        fill="outline"
        size="sm"
        icon="sync"
        onClick={onRefresh}
        aria-label="Refresh now"
        tooltip={mode === 'live' ? 'Refresh now' : 'Re-run this window'}
      />
    </div>
  );
}
