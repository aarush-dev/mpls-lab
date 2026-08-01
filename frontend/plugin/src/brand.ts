// MPLS Copilot brand palette + instance-wide reskin.
// Monotone plum with a single red accent (user-specified: #261a2d / #e1d7de / #dd423e).
// applyBrand() injects one global stylesheet into <head>; with plugin.json "preload": true this
// runs on every Grafana page, so the whole instance (login, nav, dashboards, our app) is reskinned.
export const brand = {
  bg0: '#1b1220', // deepest — page background, track
  bg1: '#261a2d', // base surface (the user's plum)
  bg2: '#2f2038', // elevated surface — cards, panels
  bg3: '#3a2740', // hover / raised
  border: '#4a3550',
  text: '#e1d7de',
  textDim: 'rgba(225,215,222,0.66)',
  textFaint: 'rgba(225,215,222,0.42)',
  accent: '#dd423e', // red
  accentHover: '#e85d59',
  accentDim: 'rgba(221,66,62,0.16)',
};

const STYLE_ID = 'mplsl-brand';

// Best-effort chrome reskin: backgrounds/text/links/scrollbars/inputs cascade reliably; deep
// Grafana widgets keep their own emotion styles but sit on the plum base. Our own app components
// are themed explicitly via the `brand` constants above, so the app surfaces are exact.
const brandCss = `
:root {
  --mplsl-bg0:${brand.bg0}; --mplsl-bg1:${brand.bg1}; --mplsl-bg2:${brand.bg2};
  --mplsl-border:${brand.border}; --mplsl-text:${brand.text}; --mplsl-accent:${brand.accent};
}
html, body, .main-view, [class*="main-view"], [class*="-canvas"] {
  background-color: ${brand.bg1} !important;
  color: ${brand.text};
}
/* top command bar + side mega-menu + any semantic banner/nav */
header, nav, [class*="-mega-menu"], [class*="-navToolbar"], [class*="-pageToolbar"], [role="banner"] {
  background-color: ${brand.bg0} !important;
  border-color: ${brand.border} !important;
}
/* generic elevated surfaces */
[class*="-panel-container"], [class*="-card"], [class*="-Card"] {
  background-color: ${brand.bg2} !important;
  border-color: ${brand.border} !important;
}
a, a:visited { color: ${brand.accent}; }
a:hover { color: ${brand.accentHover}; }
input, textarea, select, [class*="-input-wrapper"] {
  background-color: ${brand.bg0} !important;
  color: ${brand.text} !important;
  border-color: ${brand.border} !important;
}
::selection { background: ${brand.accent}; color: #fff; }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: ${brand.border}; border-radius: 5px; }
::-webkit-scrollbar-thumb:hover { background: ${brand.accent}; }
::-webkit-scrollbar-track { background: ${brand.bg0}; }
* { scrollbar-color: ${brand.border} ${brand.bg0}; }
`;

export function applyBrand(): void {
  if (typeof document === 'undefined' || document.getElementById(STYLE_ID)) {
    return;
  }
  const el = document.createElement('style');
  el.id = STYLE_ID;
  el.textContent = brandCss;
  document.head.appendChild(el);
}
