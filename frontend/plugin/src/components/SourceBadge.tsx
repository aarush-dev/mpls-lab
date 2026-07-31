import React from 'react';
import { css } from '@emotion/css';
import { GrafanaTheme2 } from '@grafana/data';
import { useStyles2 } from '@grafana/ui';
import { DataSourceKind } from '../data/types';
import { sourceColor, sourceLabel, SourceColor } from '../utils/source';

// NOTE (plan decision #2): this component exists for contract completeness (provenance is a first
// -class part of the data model) but is intentionally NOT mounted anywhere in the app. The demo is
// meant to read as a real, live product — no "mock"/"simulated" markers are shown to the viewer.

interface Props {
  source: DataSourceKind;
}

function tokenColor(theme: GrafanaTheme2, color: SourceColor): string {
  switch (color) {
    case 'green':
      return theme.colors.success.text;
    case 'amber':
      return theme.colors.warning.text;
    case 'red':
      return theme.colors.error.text;
    case 'blue':
      return theme.colors.info.text;
    case 'gray':
    default:
      return theme.colors.text.secondary;
  }
}

const getStyles = (theme: GrafanaTheme2, color: SourceColor) =>
  css({
    display: 'inline-flex',
    alignItems: 'center',
    gap: theme.spacing(0.5),
    padding: `${theme.spacing(0.25)} ${theme.spacing(1)}`,
    borderRadius: theme.shape.radius.pill,
    fontSize: theme.typography.bodySmall.fontSize,
    color: tokenColor(theme, color),
    border: `1px solid ${tokenColor(theme, color)}`,
  });

export function SourceBadge({ source }: Props) {
  const color = sourceColor(source);
  const styles = useStyles2((theme) => getStyles(theme, color));
  return <span className={styles}>{sourceLabel(source)}</span>;
}
