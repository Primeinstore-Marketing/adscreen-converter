"""
Design system for AdScreen Converter.

Single source of truth for colour, type, spacing, radius and the small set of
reusable UI helpers the app renders with. Nothing in here knows about ffmpeg or
templates — it is presentation only.

Why tokens: the previous styling hard-coded 22 distinct hex values across three
inline CSS blocks and leaned on ~200 `!important` declarations. Tokens mean a
colour is defined once, and component rules can be scoped rather than global.

Why `st.container(key=...)`: Streamlit renders `st.markdown('<div class="x">')`
as a *sibling* of the widget that follows it, not a parent, so the wrapper never
wraps and the style has to be applied globally (which then leaks onto every
other button on the page). A keyed container emits a real `.st-key-<key>`
wrapper class, so component styles can be properly scoped.
"""

import streamlit as st

# ──────────────────────────────────────────────────────────────────────────────
# TOKENS
# ──────────────────────────────────────────────────────────────────────────────

COLOR = {
    # Surfaces — deepest to most elevated
    "bg":            "#0B1320",
    "surface":       "#111D2F",
    "surface_2":     "#16243A",
    "surface_3":     "#1C2E48",
    # Lines
    "border":        "#24374F",   # decorative separators only
    "border_strong": "#33507A",
    "border_control":"#4E72A6",   # control outlines — 3.45:1 vs surface
    # Text
    "text":          "#E9EEF7",
    "text_secondary":"#AEBFD4",
    "text_muted":    "#8497AE",
    # Brand / interactive
    "primary":       "#2563EB",   # white on this = 5.17:1 (AA)
    "primary_hover": "#1D4ED8",   # 6.70:1
    "accent":        "#3B82F6",   # focus rings / accents on dark only
    "primary_soft":  "#1B3358",
    "primary_fg":    "#FFFFFF",
    # Semantic
    "success":       "#3DDC84",
    "warning":       "#F5B544",
    "error":         "#FF6B6B",
    "info":          "#56C7F5",
}

SPACE  = {"1": "4px", "2": "8px", "3": "12px", "4": "16px",
          "5": "24px", "6": "32px", "7": "48px", "8": "64px"}

RADIUS = {"control": "6px", "input": "8px", "card": "12px", "container": "16px"}

SHADOW = {
    "none":   "none",
    "subtle": "0 1px 2px rgba(0,0,0,.28)",
    "raised": "0 4px 12px rgba(0,0,0,.32)",
}

FONT_STACK = ('"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", '
              'Roboto, Helvetica, Arial, sans-serif')


def _css_vars() -> str:
    parts = [f"--c-{k.replace('_','-')}: {v};" for k, v in COLOR.items()]
    parts += [f"--s-{k}: {v};" for k, v in SPACE.items()]
    parts += [f"--r-{k}: {v};" for k, v in RADIUS.items()]
    parts += [f"--sh-{k}: {v};" for k, v in SHADOW.items()]
    return "\n    ".join(parts)


# ──────────────────────────────────────────────────────────────────────────────
# THEME
# ──────────────────────────────────────────────────────────────────────────────

def inject_theme() -> None:
    """Emit the single stylesheet for the app. Call once, early."""
    # The <style> tag MUST be the first thing in this string. A markdown HTML
    # block opened by anything else (e.g. <link>) ends at the first blank line,
    # after which the rest of the stylesheet is parsed as markdown and rendered
    # as visible text. A block opened by <style> runs until </style>, so blank
    # lines between sections are safe. The font is therefore pulled in with
    # @import rather than <link>.
    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
:root {{
    {_css_vars()}
}}

/* ── Base ──────────────────────────────────────────────────────────────── */
html, body, [data-testid="stAppViewContainer"], [class*="st-emotion"] {{
    font-family: {FONT_STACK};
}}
/* Streamlit draws icons as Material Symbols ligatures — the span's text content
   is literally "keyboard_arrow_right". The broad font-family rule above reaches
   those spans, and once the icon font is gone the ligature cannot resolve, so
   the raw name prints on top of the label. Restore the icon font explicitly. */
[data-testid="stIconMaterial"],
[data-testid="stExpanderIcon"],
span[class*="material-symbols"],
span[translate="no"] {{
    font-family: "Material Symbols Rounded" !important;
    font-feature-settings: "liga" !important;
    font-weight: normal !important;
    letter-spacing: normal !important;
    text-transform: none !important;
    white-space: nowrap !important;
    word-wrap: normal !important;
    direction: ltr !important;
}}
[data-testid="stAppViewContainer"] {{ background: var(--c-bg); }}
[data-testid="stHeader"]  {{ background: transparent; border: none; }}
[data-testid="stSidebar"] {{
    background: var(--c-surface);
    border-right: 1px solid var(--c-border);
}}
[data-testid="stMainBlockContainer"] {{
    padding-top: var(--s-5);
    max-width: 1280px;
}}

/* ── Typography scale ──────────────────────────────────────────────────── */
h1, h2, h3, h4 {{ color: var(--c-text); font-family: {FONT_STACK}; letter-spacing: -0.015em; }}
h1 {{ font-size: 1.75rem; font-weight: 700; }}
h2 {{ font-size: 1.30rem; font-weight: 600; }}
h3 {{ font-size: 1.05rem; font-weight: 600; }}
p, li, label, span, div {{ color: var(--c-text-secondary); }}
small, .ds-caption {{ color: var(--c-text-muted); font-size: .82rem; }}

/* ── Buttons ───────────────────────────────────────────────────────────── */
[data-testid="stButton"] > button {{
    border-radius: var(--r-control);
    font-weight: 600;
    font-size: .9rem;
    min-height: 40px;
    transition: background .12s ease, border-color .12s ease, color .12s ease;
    box-shadow: var(--sh-none);
}}
[data-testid="stButton"] > button[kind="primary"] {{
    background: var(--c-primary);
    border: 1px solid var(--c-primary);
    color: var(--c-primary-fg);
}}
[data-testid="stButton"] > button[kind="primary"]:hover {{
    background: var(--c-primary-hover);
    border-color: var(--c-primary-hover);
}}
[data-testid="stButton"] > button[kind="secondary"] {{
    background: var(--c-surface-2);
    border: 1px solid var(--c-border);
    color: var(--c-text-secondary);
}}
[data-testid="stButton"] > button[kind="secondary"]:hover {{
    border-color: var(--c-border-strong);
    color: var(--c-text);
}}
/* Visible keyboard focus — Streamlit's default is easy to lose */
[data-testid="stButton"] > button:focus-visible,
input:focus-visible, select:focus-visible, textarea:focus-visible {{
    outline: 2px solid var(--c-accent);
    outline-offset: 2px;
}}

/* ── Inputs ────────────────────────────────────────────────────────────── */
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
[data-testid="stTextArea"] textarea {{
    background: var(--c-surface-2);
    border: 1px solid var(--c-border-control);
    border-radius: var(--r-input);
    color: var(--c-text);
}}
[data-testid="stTextInput"] input::placeholder {{ color: var(--c-text-muted); }}

/* ── Template picker cards (scoped — no global button leak) ───────────── */
.st-key-tmpl-grid [data-testid="stButton"] > button {{
    min-height: 74px;
    text-align: left;
    padding: 10px 14px;
    white-space: pre-line;
    line-height: 1.45;
    font-size: .84rem;
    font-weight: 500;
    border-radius: var(--r-card);
    align-items: flex-start;
}}
.st-key-tmpl-grid [data-testid="stButton"] > button p {{
    text-align: left;
    white-space: pre-line;
}}

/* ── Surfaces ──────────────────────────────────────────────────────────── */
.ds-panel {{
    background: var(--c-surface);
    border: 1px solid var(--c-border);
    border-radius: var(--r-card);
    padding: var(--s-5);
}}
.ds-empty {{
    background: var(--c-surface);
    border: 1px dashed var(--c-border-strong);
    border-radius: var(--r-card);
    padding: var(--s-6) var(--s-5);
    text-align: center;
}}
.ds-empty-title {{ color: var(--c-text); font-weight: 600; font-size: 1rem; margin-bottom: var(--s-2); }}
.ds-empty-body  {{ color: var(--c-text-muted); font-size: .88rem; margin: 0 auto; max-width: 46ch; }}

/* ── Page + step headers ───────────────────────────────────────────────── */
.ds-page-title {{ font-size: 1.75rem; font-weight: 700; color: var(--c-text); margin: 0; letter-spacing: -.02em; }}
.ds-page-sub   {{ color: var(--c-text-muted); font-size: .9rem; margin-top: var(--s-1); }}

.ds-step {{ display: flex; align-items: center; gap: var(--s-3); margin: var(--s-5) 0 var(--s-3); }}
.ds-step-num {{
    width: 26px; height: 26px; border-radius: 50%;
    display: inline-flex; align-items: center; justify-content: center;
    font-size: .8rem; font-weight: 700; flex: 0 0 26px;
    background: var(--c-surface-3); color: var(--c-text-muted);
    border: 1px solid var(--c-border);
}}
.ds-step-num[data-done="1"] {{ background: var(--c-primary); color: var(--c-primary-fg); border-color: var(--c-primary); }}
.ds-step-title {{ font-size: 1.05rem; font-weight: 600; color: var(--c-text); }}

/* ── Badges ────────────────────────────────────────────────────────────── */
.ds-badge {{
    display: inline-block; padding: 2px 8px; border-radius: 999px;
    font-size: .74rem; font-weight: 600; letter-spacing: .01em;
}}
.ds-badge[data-kind="neutral"] {{ background: var(--c-surface-3); color: var(--c-text-secondary); }}
.ds-badge[data-kind="info"]    {{ background: var(--c-primary-soft); color: var(--c-info); }}
.ds-badge[data-kind="success"] {{ background: #12351F; color: var(--c-success); }}
.ds-badge[data-kind="warning"] {{ background: #3A2D12; color: var(--c-warning); }}
.ds-badge[data-kind="error"]   {{ background: #3B1B1B; color: var(--c-error); }}


/* ── Alerts ────────────────────────────────────────────────────────────── */
[data-testid="stAlert"] {{
    border-radius: var(--r-input);
    border: 1px solid var(--c-border);
    background: var(--c-surface-2);
}}
[data-testid="stAlert"] p {{ color: var(--c-text-secondary); }}

/* ── Expander — secondary actions, collapsed by default ───────────────── */
[data-testid="stExpander"] {{
    border: 1px solid var(--c-border);
    border-radius: var(--r-card);
    background: var(--c-surface);
}}
[data-testid="stExpander"] summary {{ color: var(--c-text-secondary); font-weight: 500; }}
[data-testid="stExpander"] summary:hover {{ color: var(--c-text); }}

/* ── File uploader ─────────────────────────────────────────────────────── */
[data-testid="stFileUploader"] section {{
    background: var(--c-surface-2);
    border: 1px dashed var(--c-border-control);
    border-radius: var(--r-card);
}}
[data-testid="stFileUploader"] section:hover {{ border-color: var(--c-accent); }}

/* ── Multiselect ───────────────────────────────────────────────────────── */
[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {{
    background: var(--c-surface-2);
    border: 1px solid var(--c-border-control);
    border-radius: var(--r-input);
}}
[data-testid="stMultiSelect"] span[data-baseweb="tag"] {{
    background: var(--c-primary-soft);
    color: var(--c-text);
    border-radius: var(--r-control);
}}

/* ── Progress / slider / toggle ────────────────────────────────────────── */
[data-testid="stProgress"] > div > div > div > div {{ background: var(--c-primary); }}
[data-testid="stProgress"] > div > div > div {{ background: var(--c-surface-3); }}
[data-testid="stSlider"] [data-baseweb="slider"] div[role="slider"] {{ background: var(--c-primary); }}
[data-testid="stTickBarMin"], [data-testid="stTickBarMax"] {{ color: var(--c-text-muted); }}
[data-testid="stToggle"] label {{ color: var(--c-text-secondary); }}

/* ── Misc ──────────────────────────────────────────────────────────────── */
[data-testid="stCaption"], [data-testid="stCaption"] p {{ color: var(--c-text-muted); }}
hr {{ border-color: var(--c-border); margin: var(--s-5) 0; }}
[data-testid="stTabs"] button {{ color: var(--c-text-muted); }}
[data-testid="stTabs"] button[aria-selected="true"] {{ color: var(--c-text); }}

/* ── Responsive — intentional, not just shrinking ──────────────────────── */
@media (max-width: 1280px) {{
    [data-testid="stMainBlockContainer"] {{ padding-left: var(--s-5); padding-right: var(--s-5); }}
}}
@media (max-width: 768px) {{
    h1, .ds-page-title {{ font-size: 1.4rem; }}
    [data-testid="stMainBlockContainer"] {{ padding-left: var(--s-4); padding-right: var(--s-4); }}
    /* Stack Streamlit's horizontal blocks instead of crushing them */
    [data-testid="stHorizontalBlock"] {{ flex-wrap: wrap; gap: var(--s-2); }}
    [data-testid="stHorizontalBlock"] > div {{ min-width: 100% !important; }}
    /* Touch targets */
    [data-testid="stButton"] > button {{ min-height: 44px; }}
}}
@media (max-width: 480px) {{
    [data-testid="stMainBlockContainer"] {{ padding-left: var(--s-3); padding-right: var(--s-3); }}
    .ds-empty {{ padding: var(--s-5) var(--s-4); }}
}}

/* Respect reduced-motion preferences */
@media (prefers-reduced-motion: reduce) {{
    * {{ transition: none !important; animation: none !important; }}
}}
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# COMPONENTS
# ──────────────────────────────────────────────────────────────────────────────

def page_header(title: str, subtitle: str = "") -> None:
    sub = f'<div class="ds-page-sub">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f'<div><div class="ds-page-title">{title}</div>{sub}</div>',
        unsafe_allow_html=True,
    )


def step_header(num: int, title: str, done: bool = False) -> None:
    st.markdown(
        f'<div class="ds-step">'
        f'<span class="ds-step-num" data-done="{1 if done else 0}">'
        f'{"✓" if done else num}</span>'
        f'<span class="ds-step-title">{title}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def empty_state(title: str, body: str) -> None:
    """What this is, why it's empty, what to do next."""
    st.markdown(
        f'<div class="ds-empty">'
        f'<div class="ds-empty-title">{title}</div>'
        f'<div class="ds-empty-body">{body}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def badge(text: str, kind: str = "neutral") -> str:
    """Returns markup — embed inside another markdown call."""
    return f'<span class="ds-badge" data-kind="{kind}">{text}</span>'
