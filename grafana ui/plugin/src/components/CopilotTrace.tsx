import React, { useEffect, useMemo, useRef, useState } from 'react';
import { css, cx } from '@emotion/css';
import { GrafanaTheme2, renderMarkdown, textUtil } from '@grafana/data';
import { useStyles2, Icon } from '@grafana/ui';

import type { ArtifactEvent, ChatEvent, CopilotTurn, ToolResultEvent } from '../data/types';

// T6/#71: only these raster types inline. An svg/html artifact is agent-authored (stored-XSS risk)
// and is NEVER rendered — it drops to a download chip. The UI, never the server's `mime` string,
// decides the type here (a "client-typed blob"), so bytes can't be coerced into an executable type.
const RASTER: Record<string, string> = {
  'image/png': 'image/png',
  'image/jpeg': 'image/jpeg',
  'image/gif': 'image/gif',
  'image/webp': 'image/webp',
  'image/bmp': 'image/bmp',
};

function bytesOf(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) {
    out[i] = bin.charCodeAt(i);
  }
  return out;
}

// T3/#69: Claude-style collapsed trace + citation chips. One card per think / tool_call(+its
// tool_result) / gate event, in stream order, collapsed by default. The answer renders with
// `[source:offset]` citation chips; hovering a chip previews the cited evidence row, clicking it
// expands + scrolls to + highlights that row inside its tool_result card (via the turn's citeMap).
// Honest to the synchronous backend loop: cards burst in after "investigating…", not typed live.

const CITE_RE = /\[([a-z_]+:\d+)\]/g;

/** The single line inside a tool_result's `content` that carries `[id]` — the "cited row". Used for
 * both the chip hover-preview and the in-card highlight. Empty string if none (defensive). */
export function citedRow(content: string, id: string): string {
  return content.split('\n').find((l) => l.includes(`[${id}]`))?.trim() ?? '';
}

export function CopilotTrace({ events, turn }: { events: ChatEvent[]; turn?: CopilotTurn }) {
  const styles = useStyles2(getStyles);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [hot, setHot] = useState<{ key: string; id: string } | null>(null);
  const cards = useRef<Record<string, HTMLDivElement | null>>({});

  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  // Chip click: expand + scroll to + highlight the evidence card that owns this citation.
  const jump = (id: string) => {
    const src = turn?.citeMap[id];
    if (!src) {
      return;
    }
    const key = `tool-${src.id}`;
    setOpen((prev) => new Set(prev).add(key));
    setHot({ key, id });
    cards.current[key]?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  const results: Record<string, ToolResultEvent> = {};
  for (const e of events) {
    if (e.type === 'tool_result') {
      results[e.id] = e;
    }
  }

  const card = (key: string, head: React.ReactNode, body: React.ReactNode) => (
    <div key={key} ref={(n) => (cards.current[key] = n)} className={styles.card}>
      <button className={styles.head} onClick={() => toggle(key)}>
        <Icon name={open.has(key) ? 'angle-down' : 'angle-right'} /> {head}
      </button>
      {open.has(key) && <div className={styles.body}>{body}</div>}
    </div>
  );

  return (
    <div className={styles.trace}>
      {events.map((e, i) => {
        if (e.type === 'think') {
          return card(`think-${i}`, <span>thinking</span>, <div className={styles.think}>{e.content}</div>);
        }
        if (e.type === 'tool_call') {
          const key = `tool-${e.id}`;
          const res = results[e.id];
          return card(
            key,
            <span>
              <code>{e.name}</code>
            </span>,
            <>
              <div className={styles.label}>args</div>
              <pre className={styles.pre}>{JSON.stringify(e.arguments, null, 2)}</pre>
              {res && (
                <>
                  <div className={styles.label}>evidence</div>
                  {res.content.split('\n').map((row, j) => (
                    <div key={j} className={cx(styles.row, hot?.key === key && row.includes(`[${hot.id}]`) && styles.hot)}>
                      {row}
                    </div>
                  ))}
                </>
              )}
            </>
          );
        }
        if (e.type === 'artifact') {
          return <ArtifactView key={`art-${i}`} ev={e} styles={styles} />;
        }
        if (e.type === 'gate') {
          return card(
            `gate-${i}`,
            <span>
              gate {e.ok ? 'passed' : `retry ${e.retry}`}
            </span>,
            <div className={styles.think}>{e.ok ? 'evidence sufficient' : `missing: ${e.missing.join(', ')}`}</div>
          );
        }
        return null;
      })}

      {turn && <Answer turn={turn} onCite={jump} styles={styles} />}
    </div>
  );
}

// Grafana's renderer provides sanitized GFM. Citations become links before parsing so tables and
// other block syntax stay intact; delegated clicks retain the evidence-card jump.
function Answer({ turn, onCite, styles }: { turn: CopilotTurn; onCite: (id: string) => void; styles: Styles }) {
  const markdown = turn.answer.replace(CITE_RE, (_token, id: string) => {
    const src = turn.citeMap[id];
    const title = textUtil.escapeHtml(src ? citedRow(src.content, id) : id);
    return `<a class="${styles.chip}" href="#cite-${id}" title="${title}">${id}</a>`;
  });
  const html = renderMarkdown(markdown);
  const click = (event: React.MouseEvent<HTMLDivElement>) => {
    const link = (event.target as Element).closest<HTMLAnchorElement>('a[href^="#cite-"]');
    const href = link?.getAttribute('href');
    if (href) {
      event.preventDefault();
      onCite(href.slice('#cite-'.length));
    }
  };
  return <div className={styles.answer} onClick={click} dangerouslySetInnerHTML={{ __html: html }} />;
}

// One presented `artifact` event. A raster kind inlines from a client-typed image blob; everything
// else (svg/html/code, or an over-cap reference with no bytes) is a download-only chip — the bytes
// never execute in this origin. Inline events carry `content_b64` (image) or `content` (text/code);
// over-cap events carry only `path` -> a bytes-less chip.
function ArtifactView({ ev, styles }: { ev: ArtifactEvent; styles: Styles }) {
  const b64 = typeof ev.content_b64 === 'string' ? ev.content_b64 : undefined;
  const text = typeof ev.content === 'string' ? ev.content : undefined;
  const raster = typeof ev.mime === 'string' ? RASTER[ev.mime] : undefined;
  const title = (typeof ev.title === 'string' && ev.title) || ev.name;
  // A raster inlines under its own type; anything else downloads as octet-stream (forced non-exec).
  const url = useMemo(() => {
    if (b64) {
      return URL.createObjectURL(new Blob([bytesOf(b64)], { type: raster ?? 'application/octet-stream' }));
    }
    if (text != null) {
      return URL.createObjectURL(new Blob([text], { type: 'application/octet-stream' }));
    }
    return undefined;
  }, [b64, text, raster]);
  useEffect(() => {
    return () => {
      if (url) {
        URL.revokeObjectURL(url);
      }
    };
  }, [url]);

  if (raster && url) {
    return <img className={styles.artifact} src={url} alt={title} />;
  }
  return url ? (
    <a className={styles.dlchip} href={url} download={ev.name}>
      <Icon name="download-alt" /> {title}
    </a>
  ) : (
    <span className={styles.dlchip}>
      <Icon name="download-alt" /> {title} (on backend)
    </span>
  );
}

type Styles = ReturnType<typeof getStyles>;

const getStyles = (theme: GrafanaTheme2) => ({
  trace: css({ display: 'flex', flexDirection: 'column', gap: theme.spacing(0.5) }),
  card: css({ border: `1px solid ${theme.colors.border.weak}`, borderRadius: theme.shape.radius.default }),
  head: css({
    display: 'flex',
    alignItems: 'center',
    gap: theme.spacing(0.5),
    width: '100%',
    padding: theme.spacing(0.5, 1),
    background: 'transparent',
    border: 'none',
    color: theme.colors.text.secondary,
    cursor: 'pointer',
    textAlign: 'left',
  }),
  body: css({ padding: theme.spacing(0.5, 1, 1, 3) }),
  label: css({ fontSize: theme.typography.bodySmall.fontSize, color: theme.colors.text.secondary, marginTop: theme.spacing(0.5) }),
  think: css({ whiteSpace: 'pre-wrap', color: theme.colors.text.secondary }),
  pre: css({ margin: 0, fontSize: theme.typography.bodySmall.fontSize, whiteSpace: 'pre-wrap' }),
  row: css({ fontFamily: theme.typography.fontFamilyMonospace, fontSize: theme.typography.bodySmall.fontSize }),
  hot: css({ background: theme.colors.warning.transparent, borderRadius: theme.shape.radius.default }),
  answer: css({
    marginTop: theme.spacing(1),
    overflowX: 'auto',
    '& > :first-child': { marginTop: 0 },
    '& > :last-child': { marginBottom: 0 },
    '& table': { width: '100%', borderCollapse: 'collapse' },
    '& th, & td': {
      padding: theme.spacing(0.5, 1),
      border: `1px solid ${theme.colors.border.weak}`,
      textAlign: 'left',
    },
  }),
  artifact: css({ maxWidth: '100%', borderRadius: theme.shape.radius.default, border: `1px solid ${theme.colors.border.weak}` }),
  dlchip: css({
    display: 'inline-flex',
    alignItems: 'center',
    gap: theme.spacing(0.5),
    width: 'fit-content',
    padding: theme.spacing(0.25, 0.75),
    background: theme.colors.background.secondary,
    border: `1px solid ${theme.colors.border.weak}`,
    borderRadius: theme.shape.radius.default,
    color: theme.colors.text.link,
    fontSize: theme.typography.bodySmall.fontSize,
  }),
  chip: css({
    display: 'inline',
    padding: theme.spacing(0, 0.5),
    margin: theme.spacing(0, 0.25),
    background: theme.colors.background.secondary,
    border: `1px solid ${theme.colors.border.weak}`,
    borderRadius: theme.shape.radius.default,
    color: theme.colors.text.link,
    font: 'inherit',
    fontSize: theme.typography.bodySmall.fontSize,
    cursor: 'pointer',
  }),
});
