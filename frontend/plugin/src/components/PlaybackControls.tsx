import React, { useCallback } from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { Button, Icon, Slider, useStyles2 } from '@grafana/ui';
import { useAppDispatch, useAppState } from '../state/AppContext';
import { BucketMeta, bucketToTsMs, formatUtc } from '../utils/time';
import { brand } from '../brand';

const SPEEDS = [0.5, 1, 2, 4];

interface Props {
  /** Optional fixture bucket metadata; when absent the live label falls back to a bare index. */
  meta?: BucketMeta;
}

const getStyles = (theme: GrafanaTheme2) =>
  css({
    display: 'flex',
    alignItems: 'center',
    gap: theme.spacing(1),
    padding: theme.spacing(1),
    background: brand.bg2,
    borderRadius: theme.shape.radius.default,
  });

const sliderWrapStyle = css({
  flex: 1,
  minWidth: 160,
  paddingTop: 6,
});

const labelStyle = (theme: GrafanaTheme2) =>
  css({
    whiteSpace: 'nowrap',
    fontFamily: theme.typography.fontFamilyMonospace,
    fontSize: theme.typography.bodySmall.fontSize,
    color: brand.textDim,
    minWidth: 190,
  });

const speedGroupStyle = css({
  display: 'flex',
  gap: 4,
});

export function PlaybackControls({ meta }: Props) {
  const styles = useStyles2(getStyles);
  const label = useStyles2(labelStyle);
  const { cursor, playing, speed, bucketCount } = useAppState();
  const dispatch = useAppDispatch();

  const togglePlaying = useCallback(() => {
    dispatch({ type: playing ? 'PAUSE' : 'PLAY' });
  }, [dispatch, playing]);

  const onSeek = useCallback(
    (value: number) => {
      dispatch({ type: 'SEEK', payload: { cursor: Math.round(value) } });
    },
    [dispatch]
  );

  const maxIndex = Math.max(0, bucketCount - 1);
  const timeLabel = meta ? formatUtc(bucketToTsMs(meta, cursor)) : `bucket ${cursor}`;

  return (
    <div className={styles}>
      <Button
        variant="secondary"
        fill="outline"
        icon={playing ? 'pause' : 'play'}
        onClick={togglePlaying}
        aria-label={playing ? 'Pause playback' : 'Play playback'}
        disabled={bucketCount <= 0}
      >
        {playing ? 'Pause' : 'Play'}
      </Button>

      <div className={sliderWrapStyle}>
        <Slider
          min={0}
          max={Math.max(maxIndex, 1)}
          step={1}
          value={cursor}
          onChange={onSeek}
          included={false}
        />
      </div>

      <div className={speedGroupStyle}>
        {SPEEDS.map((s) => (
          <Button
            key={s}
            size="sm"
            variant={speed === s ? 'primary' : 'secondary'}
            fill="outline"
            onClick={() => dispatch({ type: 'SET_SPEED', payload: { speed: s } })}
          >
            {s}x
          </Button>
        ))}
      </div>

      <span className={label} title="Current bucket time (UTC)">
        <Icon name="clock-nine" size="sm" /> {timeLabel}
      </span>
    </div>
  );
}
