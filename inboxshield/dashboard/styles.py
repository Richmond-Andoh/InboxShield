"""Dashboard visual system (CSS only; no external assets or fonts)."""

from __future__ import annotations

import streamlit as st

_CSS = """
<style>
:root {
  --is-navy: #0b1f33;
  --is-navy-soft: #12304a;
  --is-surface: #ffffff;
  --is-canvas: #f4f7fb;
  --is-border: #dfe7f1;
  --is-teal: #0f766e;
  --is-teal-soft: #e6f4f1;
  --is-amber: #b45309;
  --is-amber-soft: #fef3c7;
  --is-red: #b91c1c;
  --is-red-soft: #fee2e2;
  --is-green: #15803d;
  --is-green-soft: #dcfce7;
  --is-slate: #475569;
  --is-muted: #64748b;
}

html, body, [class*="css"] {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    "Helvetica Neue", Arial, sans-serif;
}

.stApp { background: var(--is-canvas); }
.block-container { padding-top: 2.1rem; padding-bottom: 3rem; max-width: 1320px; }

section[data-testid="stSidebar"] {
  background: var(--is-navy);
  border-right: 1px solid #102a43;
}
section[data-testid="stSidebar"] * { color: #e2e8f0; }
section[data-testid="stSidebar"] .stRadio label p { font-size: 0.98rem; }
section[data-testid="stSidebar"] .stRadio [data-checked="true"] {
  background: var(--is-navy-soft);
}

.is-brand { padding: 0.2rem 0 1.1rem 0; border-bottom: 1px solid #1e3a55; }
.is-brand-mark {
  display: inline-flex; align-items: center; justify-content: center;
  width: 34px; height: 34px; border-radius: 9px;
  background: var(--is-teal); color: white; font-weight: 800;
  font-size: 16px; margin-right: 10px; vertical-align: middle;
}
.is-brand-name {
  font-size: 1.22rem; font-weight: 750; letter-spacing: -0.01em;
  color: #ffffff; vertical-align: middle;
}
.is-brand-sub {
  margin-top: 8px; font-size: 0.76rem; color: #93a8bd;
  text-transform: uppercase; letter-spacing: 0.09em;
}
.is-side-block {
  margin-top: 1rem; padding: 0.8rem 0.9rem; border-radius: 10px;
  background: var(--is-navy-soft); border: 1px solid #1e3a55;
  font-size: 0.8rem; color: #cbd5e1; line-height: 1.55;
}
.is-side-block strong { color: #ffffff; }

.is-page-title {
  font-size: 1.85rem; font-weight: 760; letter-spacing: -0.025em;
  color: #0f172a; margin-bottom: 0.15rem;
}
.is-page-sub { color: var(--is-muted); font-size: 0.94rem; margin-bottom: 1.4rem; }

.is-card {
  background: var(--is-surface); border: 1px solid var(--is-border);
  border-radius: 14px; padding: 1.05rem 1.15rem;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
}
.is-metric-label {
  font-size: 0.75rem; font-weight: 650; letter-spacing: 0.06em;
  text-transform: uppercase; color: var(--is-muted);
}
.is-metric-value {
  font-size: 1.7rem; font-weight: 760; color: #0f172a;
  margin-top: 0.25rem; line-height: 1.15;
}
.is-metric-hint { font-size: 0.78rem; color: var(--is-muted); margin-top: 0.2rem; }
.is-metric-tone { border-left: 4px solid var(--is-teal); }
.is-metric-tone.amber { border-left-color: var(--is-amber); }
.is-metric-tone.red { border-left-color: var(--is-red); }
.is-metric-tone.green { border-left-color: var(--is-green); }
.is-metric-tone.slate { border-left-color: var(--is-slate); }

.is-section { font-size: 1.06rem; font-weight: 700; color: #0f172a;
  margin: 1.4rem 0 0.55rem 0; }
.is-muted { color: var(--is-muted); font-size: 0.86rem; }
.is-mono { font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 0.8rem; }

.is-badge {
  display: inline-block; padding: 0.16rem 0.55rem; border-radius: 999px;
  font-size: 0.71rem; font-weight: 700; letter-spacing: 0.03em;
  border: 1px solid transparent;
}
.is-badge.verified { background: var(--is-green-soft); color: var(--is-green);
  border-color: #bbf7d0; }
.is-badge.review { background: var(--is-amber-soft); color: var(--is-amber);
  border-color: #fde68a; }
.is-badge.duplicate { background: var(--is-red-soft); color: var(--is-red);
  border-color: #fecaca; }
.is-badge.failed { background: #f1f5f9; color: var(--is-slate);
  border-color: #e2e8f0; }
.is-badge.processing { background: #e0f2fe; color: #0369a1; border-color: #bae6fd; }
.is-badge.neutral { background: #f1f5f9; color: var(--is-slate);
  border-color: #e2e8f0; }

.is-finding {
  border: 1px solid var(--is-border); border-radius: 10px;
  padding: 0.75rem 0.9rem; margin-bottom: 0.55rem; background: #fbfdff;
}
.is-finding.high { border-left: 4px solid var(--is-red); }
.is-finding.medium { border-left: 4px solid var(--is-amber); }
.is-finding.low { border-left: 4px solid var(--is-teal); }
.is-finding-title { font-weight: 700; font-size: 0.9rem; color: #0f172a; }
.is-finding-body { font-size: 0.86rem; color: #334155; margin-top: 0.25rem; }
.is-finding-meta { font-size: 0.75rem; color: var(--is-muted); margin-top: 0.35rem; }

.is-empty {
  background: var(--is-surface); border: 1px dashed #cbd5e1; border-radius: 14px;
  padding: 1.6rem; text-align: center; color: var(--is-muted);
}
.is-kv { display: flex; justify-content: space-between; gap: 1rem;
  padding: 0.42rem 0; border-bottom: 1px solid #eef2f7; font-size: 0.87rem; }
.is-kv:last-child { border-bottom: none; }
.is-kv span:first-child { color: var(--is-muted); }
.is-kv span:last-child { color: #0f172a; font-weight: 600; text-align: right; }

.is-health {
  display: flex; align-items: center; gap: 0.55rem; font-size: 0.88rem;
  padding: 0.5rem 0.7rem; border-radius: 9px; background: #f8fafc;
  border: 1px solid var(--is-border); margin-bottom: 0.4rem;
}
.is-dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.is-dot.ok { background: var(--is-green); }
.is-dot.warn { background: var(--is-amber); }
.is-dot.bad { background: var(--is-red); }

.stButton > button, .stDownloadButton > button {
  border-radius: 9px; border: 1px solid var(--is-border);
  font-weight: 600; font-size: 0.86rem;
}
.stButton > button[kind="primary"] {
  background: var(--is-teal); border-color: var(--is-teal);
}
div[data-testid="stDataFrame"] { border: 1px solid var(--is-border); border-radius: 12px; }
.stTabs [data-baseweb="tab-list"] { gap: 0.4rem; }
.stTabs [data-baseweb="tab"] { font-weight: 600; font-size: 0.88rem; }
footer { visibility: hidden; }
#MainMenu { visibility: hidden; }
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)
