
import streamlit as st
import json
import subprocess
import datetime
import logging
import shutil
import zipfile
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# Resolve ffmpeg binary path (no subprocess at startup — just find the binary)
def _resolve_ffmpeg() -> str:
    # 1. System install (packages.txt installs this on Streamlit Cloud)
    sys_ff = shutil.which("ffmpeg")
    if sys_ff:
        return sys_ff
    # 2. imageio-ffmpeg bundled binary
    try:
        import imageio_ffmpeg as _iio
        return _iio.get_ffmpeg_exe()
    except Exception:
        pass
    return "ffmpeg"

_FFMPEG_BIN = _resolve_ffmpeg()

# ──────────────────────────────────────────────
# CONFIG & LOGGING
# ──────────────────────────────────────────────

import sys as _sys

_frozen = getattr(_sys, "frozen", False)

if _frozen:
    import platform
    if platform.system() == "Darwin":
        # macOS: use ~/Library/Application Support/ — always writable, no sandbox issues
        _user_dir = Path.home() / "Library" / "Application Support" / "AdScreen Converter"
    else:
        # Windows: use %APPDATA%
        _user_dir = Path.home() / "AppData" / "Roaming" / "AdScreen Converter"
else:
    _user_dir = Path(__file__).parent

_user_dir.mkdir(parents=True, exist_ok=True)
_log_path = _user_dir / "app.log"

# Build log handlers — fall back to stream-only if the file can't be created
_handlers: list[logging.Handler] = [logging.StreamHandler()]
try:
    _handlers.insert(0, logging.FileHandler(_log_path))
except OSError:
    pass  # Can't write log file — stream only

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=_handlers,
)
log = logging.getLogger(__name__)

BASE_DIR     = Path(_sys._MEIPASS) if _frozen else Path(__file__).parent
TEMPLATE_DIR = BASE_DIR / "templates"
_BASE_IO_DIR = _user_dir  # per-session subdirs created after st.set_page_config

st.set_page_config(
    page_title="AdScreen Converter",
    page_icon="🎬",
    layout="wide",
)

# ── Per-session isolated directories (prevents multi-user file conflicts) ──────
import uuid as _uuid
if "session_id" not in st.session_state:
    st.session_state.session_id = _uuid.uuid4().hex[:12]
if "upload_key" not in st.session_state:
    st.session_state.upload_key = 0
if "compress_key" not in st.session_state:
    st.session_state.compress_key = 0
_sess = st.session_state.session_id
INPUT_DIR   = _BASE_IO_DIR / "sessions" / _sess / "input"
OUTPUT_DIR  = _BASE_IO_DIR / "sessions" / _sess / "output"
PREVIEW_DIR = _BASE_IO_DIR / "sessions" / _sess / "previews"
for _d in (INPUT_DIR, OUTPUT_DIR, PREVIEW_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── Global colour theme ────────────────────────────────────────────────────────
st.markdown("""
<style>
/* ── Palette ──────────────────────────────────────────────────────────────────
   Page bg:        #0d1b2e   deep navy
   Sidebar bg:     #102540   slightly lighter
   Panel / card:   #1a3350   medium navy
   Input bg:       #1e3a5f   lighter navy
   Border:         #2d5f9e   blue border
   Accent:         #3b82f6   bright blue
   Text primary:   #f1f5f9   near-white  (high contrast on dark)
   Text secondary: #cbd5e1   light grey  (readable on dark)
   Text muted:     #94a3b8   medium grey
   Dropdown:       #ffffff   white bg / dark text
   ──────────────────────────────────────────────────────────────────────────── */

/* ── Page background ─────────────────────────────────────────────────────── */
[data-testid="stAppViewContainer"],
[data-testid="stAppViewContainer"] > .main {
    background-color: #0d1b2e !important;
}

/* ── Main content text ───────────────────────────────────────────────────── */
h1, h2, h3, h4, h5, h6 { color: #f1f5f9 !important; }
/* Scope general text colour to the app container only — NOT dropdowns */
[data-testid="stAppViewContainer"] p,
[data-testid="stAppViewContainer"] label,
[data-testid="stAppViewContainer"] .stMarkdown li { color: #cbd5e1; }
[data-testid="stCaption"] { color: #94a3b8 !important; }
[data-testid="stText"]    { color: #cbd5e1 !important; }

/* ── Header bar ──────────────────────────────────────────────────────────── */
[data-testid="stHeader"] {
    background-color: #0d1b2e !important;
    border-bottom: 1px solid #1e3a5f !important;
}

/* ── Sidebar ─────────────────────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background-color: #102540 !important;
    border-right: 1px solid #2d5f9e !important;
}
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 { color: #f1f5f9 !important; }
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] div { color: #cbd5e1 !important; }
[data-testid="stSidebar"] .stMarkdown hr { border-color: #2d5f9e !important; }

/* ── Tabs ────────────────────────────────────────────────────────────────── */
[data-testid="stTabs"] [data-baseweb="tab-list"] {
    background-color: #1a3350 !important;
    border-radius: 8px !important;
    padding: 4px !important;
    gap: 4px !important;
}
[data-testid="stTabs"] [data-baseweb="tab"] {
    color: #94a3b8 !important;
    border-radius: 6px !important;
    font-weight: 600 !important;
}
[data-testid="stTabs"] [aria-selected="true"] {
    background-color: #2d5f9e !important;
    color: #f1f5f9 !important;
}

/* ── Buttons ─────────────────────────────────────────────────────────────── */
[data-testid="stButton"] > button[kind="primary"] {
    background: linear-gradient(135deg, #2563eb, #3b82f6) !important;
    border: none !important;
    color: #ffffff !important;
    font-weight: 700 !important;
    border-radius: 10px !important;
    box-shadow: 0 4px 16px rgba(59,130,246,0.35) !important;
}
[data-testid="stButton"] > button[kind="primary"] p,
[data-testid="stButton"] > button[kind="primary"] span {
    color: #ffffff !important;
    font-weight: 700 !important;
}
[data-testid="stButton"] > button[kind="primary"]:hover {
    box-shadow: 0 6px 22px rgba(59,130,246,0.55) !important;
    filter: brightness(1.1) !important;
}
[data-testid="stButton"] > button[kind="secondary"] {
    background: #1a3350 !important;
    border: 1px solid #3b82f6 !important;
    color: #93c5fd !important;
    font-weight: 600 !important;
    border-radius: 10px !important;
}
[data-testid="stButton"] > button[kind="secondary"] p,
[data-testid="stButton"] > button[kind="secondary"] span {
    color: #93c5fd !important;
}

/* ── Nav buttons — fixed height so they never resize on tab switch ────────── */
[data-testid="stButton"][data-key="nav_convert"] > button,
[data-testid="stButton"][data-key="nav_compress"] > button {
    height: 48px !important;
    min-height: 48px !important;
    max-height: 48px !important;
    white-space: nowrap !important;
    text-align: center !important;
    font-size: 0.95rem !important;
    padding: 0 28px !important;
}
[data-testid="stButton"][data-key="nav_convert"] > button p,
[data-testid="stButton"][data-key="nav_convert"] > button span,
[data-testid="stButton"][data-key="nav_compress"] > button p,
[data-testid="stButton"][data-key="nav_compress"] > button span {
    text-align: center !important;
    white-space: nowrap !important;
    color: inherit !important;
}
[data-testid="stButton"] > button[kind="secondary"]:hover {
    background: #1e3a5f !important;
}

/* ── Text / Number inputs ────────────────────────────────────────────────── */
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input {
    background-color: #1e3a5f !important;
    border: 1px solid #2d5f9e !important;
    color: #f1f5f9 !important;
    border-radius: 6px !important;
}
[data-testid="stTextInput"] input::placeholder,
[data-testid="stNumberInput"] input::placeholder { color: #94a3b8 !important; }
[data-testid="stTextInput"] label,
[data-testid="stNumberInput"] label { color: #cbd5e1 !important; }
[data-testid="stNumberInput"] > div {
    background-color: #1e3a5f !important;
    border: 1px solid #2d5f9e !important;
    border-radius: 6px !important;
}
[data-testid="stNumberInput"] button {
    background-color: #2d5f9e !important;
    border-color: #2d5f9e !important;
    color: #ffffff !important;
}
[data-testid="stNumberInput"] button:hover { background-color: #3b82f6 !important; }

/* ── Selectbox ───────────────────────────────────────────────────────────── */
[data-testid="stSelectbox"] label { color: #cbd5e1 !important; }
[data-testid="stSelectbox"] div[data-baseweb="select"] {
    background-color: #1e3a5f !important;
    border: 1px solid #2d5f9e !important;
    border-radius: 6px !important;
}
[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
    background-color: #1e3a5f !important;
    color: #f1f5f9 !important;
}

/* ── Multiselect ─────────────────────────────────────────────────────────── */
[data-testid="stMultiSelect"] label { color: #cbd5e1 !important; }

/* Outer wrapper — one unified dark box */
[data-testid="stMultiSelect"] div[data-baseweb="select"],
[data-testid="stMultiSelect"] div[data-baseweb="select"] > div,
[data-testid="stMultiSelect"] div[data-baseweb="select"] > div > div {
    background-color: #1e3a5f !important;
    border: 1px solid #2d5f9e !important;
    border-radius: 6px !important;
    color: #f1f5f9 !important;
}
/* Remove any inner borders that create a double-box look */
[data-testid="stMultiSelect"] div[data-baseweb="select"] > div {
    border: none !important;
}
/* Search input inside the box */
[data-testid="stMultiSelect"] input {
    background-color: #1e3a5f !important;
    color: #f1f5f9 !important;
    border: none !important;
    outline: none !important;
}
[data-testid="stMultiSelect"] input::placeholder { color: #94a3b8 !important; }
/* Selected tags */
[data-testid="stMultiSelect"] [data-baseweb="tag"] {
    background-color: #2d5f9e !important;
    color: #ffffff !important;
    border-radius: 4px !important;
}
[data-testid="stMultiSelect"] [data-baseweb="tag"] span,
[data-testid="stMultiSelect"] [data-baseweb="tag"] svg { color: #ffffff !important; }

/* ── Dropdown popover (white bg, dark readable text) ─────────────────────── */
[data-baseweb="popover"],
[data-baseweb="popover"] ul,
[data-baseweb="menu"] {
    background-color: #ffffff !important;
    border: 1px solid #cbd5e1 !important;
    box-shadow: 0 8px 24px rgba(0,0,0,0.18) !important;
    border-radius: 8px !important;
}
/* Every element inside the popover gets dark text */
[data-baseweb="popover"] *,
[data-baseweb="menu"] *,
[role="listbox"] *,
[role="option"] * {
    color: #1e293b !important;
    background-color: transparent !important;
}
/* Row backgrounds */
[data-baseweb="menu"] li,
[data-baseweb="option"],
[role="option"] {
    background-color: #ffffff !important;
    font-weight: 500 !important;
}
[data-baseweb="menu"] li:hover,
[data-baseweb="option"]:hover,
[role="option"]:hover {
    background-color: #dbeafe !important;
}

/* ── Slider ──────────────────────────────────────────────────────────────── */
[data-testid="stSlider"] label { color: #cbd5e1 !important; }
[data-testid="stSlider"] [data-testid="stTickBarMin"],
[data-testid="stSlider"] [data-testid="stTickBarMax"] { color: #94a3b8 !important; }

/* ── Toggle ──────────────────────────────────────────────────────────────── */
[data-testid="stToggle"] label,
[data-testid="stToggle"] p { color: #cbd5e1 !important; }

/* ── File uploader ───────────────────────────────────────────────────────── */
[data-testid="stFileUploader"] {
    background-color: #1a3350 !important;
    border: 2px dashed #2d5f9e !important;
    border-radius: 10px !important;
}
[data-testid="stFileUploader"] label,
[data-testid="stFileUploader"] span,
[data-testid="stFileUploader"] p { color: #cbd5e1 !important; }
[data-testid="stFileUploader"] button { color: #93c5fd !important; }

/* ── Expanders ───────────────────────────────────────────────────────────── */
[data-testid="stExpander"] {
    background-color: #1a3350 !important;
    border: 1px solid #2d5f9e !important;
    border-radius: 8px !important;
}
[data-testid="stExpander"] summary { color: #f1f5f9 !important; font-weight: 600 !important; }
[data-testid="stExpander"] summary p { color: #f1f5f9 !important; }

/* ── Alert / info boxes ──────────────────────────────────────────────────── */
[data-testid="stAlert"] { border-radius: 8px !important; }
[data-testid="stAlert"] p { color: inherit !important; }

/* ── Progress / spinner ──────────────────────────────────────────────────── */
[data-testid="stProgress"] > div > div { background-color: #3b82f6 !important; }

/* ── Dividers ────────────────────────────────────────────────────────────── */
hr { border-color: #1e3a5f !important; }
</style>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────
# DATA CLASSES
# ──────────────────────────────────────────────

@dataclass
class Template:
    name: str
    formats: list[tuple[int, int]]
    description: str = ""
    tags: list[str] = field(default_factory=list)
    safe_zones: list[dict] = field(default_factory=list)

    @property
    def primary_resolution(self) -> str:
        if self.formats:
            w, h = self.formats[0][0], self.formats[0][1]
            return f"{w}×{h}"
        return "—"

    @classmethod
    def from_file(cls, path: Path) -> "Template":
        with open(path) as f:
            data = json.load(f)
        return cls(
            name=data["name"],
            formats=[tuple(fmt) for fmt in data["formats"]],
            description=data.get("description", ""),
            tags=data.get("tags", []),
            safe_zones=data.get("safe_zones", []),
        )


# ──────────────────────────────────────────────
# FFMPEG UTILITIES
# ──────────────────────────────────────────────

@st.cache_data(ttl=300)
def ffmpeg_available() -> bool:
    try:
        r = subprocess.run([_FFMPEG_BIN, "-version"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def probe_video(path: Path) -> tuple[int, int, float]:
    """Return (width, height, duration_seconds) using OpenCV (no ffprobe needed)."""
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {path.name}")
    w   = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h   = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
    dur = frames / fps if frames > 0 else 0.0
    cap.release()
    if w == 0 or h == 0:
        raise RuntimeError(f"Could not read video metadata for {path.name}")
    return w, h, dur


def image_to_video(src: Path, dst: Path, duration: int = 10) -> None:
    """Convert a still image to a looping MP4."""
    cmd = [
        _FFMPEG_BIN, "-y",
        "-loop", "1",
        "-i", str(src),
        "-t", str(duration),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=25,format=yuv420p",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        str(dst),
    ]
    _run(cmd)


def generate_thumbnail(src: Path, dst: Path, timestamp: float = 0.5) -> None:
    """Extract a JPEG thumbnail from a video."""
    cmd = [
        _FFMPEG_BIN, "-y",
        "-ss", str(timestamp),
        "-i", str(src),
        "-vframes", "1",
        "-q:v", "3",
        str(dst),
    ]
    _run(cmd, check=False)


def export_format(
    src: Path,
    dst: Path,
    width: int,
    height: int,
    crop_x: int,
    crop_y: int,
    crf: int = 18,
    preset: str = "slow",
    resize_mode: str = "crop",
) -> subprocess.CompletedProcess:
    """Export a video to the target resolution.

    resize_mode:
      "crop"    — scale up then centre-crop (no distortion, may cut edges)
      "stretch" — scale directly to exact dimensions (may squash/stretch)
    """
    if resize_mode == "stretch":
        vf = f"scale={width}:{height}"
    else:
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height}:{crop_x}:{crop_y}"
        )
    cmd = [
        _FFMPEG_BIN, "-y",
        "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", preset,
        "-an",
        "-movflags", "+faststart",
        str(dst),
    ]
    return _run(cmd)


def _run(cmd: list, check: bool = True) -> subprocess.CompletedProcess:
    log.info("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if check and result.returncode != 0:
        log.error("stderr: %s", result.stderr)
        raise RuntimeError(result.stderr[-500:])
    return result


# ──────────────────────────────────────────────
# TEMPLATE LOADER
# ──────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_templates() -> dict[str, Template]:
    templates = {}
    for p in sorted(TEMPLATE_DIR.glob("*.json")):
        try:
            t = Template.from_file(p)
            display = f"{t.name}  —  {t.primary_resolution}  ({len(t.formats)} formats)"
            templates[display] = t
        except Exception as exc:
            log.warning("Skipping %s: %s", p.name, exc)
    return templates


# ──────────────────────────────────────────────
# CROP / EXPORT PLANNING
# ──────────────────────────────────────────────

@dataclass
class FormatPlan:
    """Pre-computed export plan for one target resolution."""
    width: int
    height: int
    crop_x: int = 0
    crop_y: int = 0
    label: str = ""


def plan_exports(
    template: Template,
    video_w: int,
    video_h: int,
    importance_boxes: list = None,
) -> list[FormatPlan]:
    """Centre-crop plan, or smart-crop when importance_boxes are provided."""
    plans = []
    for fmt in template.formats:
        target_w, target_h = fmt[0], fmt[1]
        label = fmt[2] if len(fmt) > 2 else ""
        if importance_boxes:
            from smart_crop import smart_crop_origin
            crop_x, crop_y = smart_crop_origin(
                video_w, video_h, target_w, target_h, importance_boxes
            )
        else:
            crop_x = max(0, (video_w - target_w) // 2)
            crop_y = max(0, (video_h - target_h) // 2)
        plans.append(FormatPlan(width=target_w, height=target_h, crop_x=crop_x, crop_y=crop_y, label=label))
    return plans


# ──────────────────────────────────────────────
# ZIP HELPER
# ──────────────────────────────────────────────

def zip_outputs(files: list[Path], output_dir: Path) -> Path:
    zip_path = output_dir / f"export_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, f.name)
    return zip_path


# ──────────────────────────────────────────────
# UI  ─  WELCOME SCREEN  (must come first so st.stop() blocks everything below)
# ──────────────────────────────────────────────

if "welcomed" not in st.session_state:
    st.session_state.welcomed = False

if not st.session_state.welcomed:
    _logo_path = BASE_DIR / "img" / "logo_white.png"

    import base64 as _b64
    _logo_b64 = ""
    if _logo_path.exists():
        _logo_b64 = _b64.b64encode(_logo_path.read_bytes()).decode()

    _logo_tag = f"<img src='data:image/png;base64,{_logo_b64}' style='max-width:220px;width:100%;display:block;margin:0 auto 20px' />" if _logo_b64 else ""

    st.markdown(f"""
<style>
[data-testid="stAppViewContainer"] {{ background: #0d1b2e !important; }}
[data-testid="stHeader"]  {{ background: transparent !important; border: none !important; }}
[data-testid="stSidebar"] {{ display: none !important; }}

/* Vertically centre the content area */
[data-testid="stMainBlockContainer"] {{
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    min-height: 95vh !important;
    padding: 20px !important;
}}

/* Style the block container itself as the card */
[data-testid="stMainBlockContainer"] > .block-container {{
    background: #1e3a5f !important;
    border: 1px solid #2d5f9e !important;
    border-radius: 20px !important;
    padding: 44px 52px 40px !important;
    max-width: 560px !important;
    width: 100% !important;
    box-shadow: 0 24px 64px rgba(0,0,0,0.55) !important;
    text-align: center !important;
}}

.wdivider {{
    width: 48px; height: 3px;
    background: linear-gradient(90deg, #3b82f6, #60a5fa);
    border-radius: 2px; margin: 12px auto 16px;
}}
.weyebrow {{
    font-size: 0.72rem; font-weight: 600; color: #93c5fd;
    letter-spacing: 0.22em; text-transform: uppercase;
}}
.wtitle {{
    font-size: 2rem; font-weight: 800; color: #f1f5f9;
    letter-spacing: -0.5px; margin: 0 0 28px; line-height: 1.1;
}}
.wfeats {{
    display: flex; gap: 10px; flex-wrap: wrap;
    justify-content: center; margin-top: 20px;
}}
.wfeat {{
    background: #142034; border: 1px solid #2d5f9e;
    border-radius: 10px; padding: 12px 10px;
    flex: 1; min-width: 100px; max-width: 130px;
}}
.wfeat-icon {{ font-size: 1.3rem; margin-bottom: 5px; }}
.wfeat-lbl  {{ font-size: 0.73rem; color: #f1f5f9; font-weight: 600; line-height: 1.3; }}
.wfeat-desc {{ font-size: 0.66rem; color: #aac4e8; margin-top: 3px; line-height: 1.3; }}

/* Welcome CTA button — white, centred, bold */
.welcome-btn [data-testid="stButton"] {{
    display: flex !important;
    justify-content: center !important;
}}
.welcome-btn [data-testid="stButton"] > button[kind="primary"] {{
    background: #ffffff !important;
    color: #0d1b2e !important;
    border: none !important;
    font-weight: 800 !important;
    font-size: 1.05rem !important;
    letter-spacing: 0.02em !important;
    padding: 16px 48px !important;
    border-radius: 50px !important;
    width: auto !important;
    min-width: 240px !important;
    box-shadow: 0 4px 20px rgba(255,255,255,0.15) !important;
}}
.welcome-btn [data-testid="stButton"] > button[kind="primary"] p,
.welcome-btn [data-testid="stButton"] > button[kind="primary"] span {{
    color: #0d1b2e !important;
    font-weight: 800 !important;
}}
.welcome-btn [data-testid="stButton"] > button[kind="primary"]:hover {{
    background: #e2e8f0 !important;
    box-shadow: 0 6px 28px rgba(255,255,255,0.25) !important;
}}
</style>

<div style="text-align:center">
  {_logo_tag}
  <div class="weyebrow">Powered by Primedia Out of Home</div>
  <div class="wdivider"></div>
  <div class="wtitle">AdScreen Converter</div>
</div>
""", unsafe_allow_html=True)

    st.markdown('<div class="welcome-btn">', unsafe_allow_html=True)
    col_a, col_b, col_c = st.columns([1, 1, 1])
    with col_b:
        if st.button("✦  Let's Get Started", type="primary", use_container_width=True):
            st.session_state.welcomed = True
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("""
<div class="wfeats">
  <div class="wfeat"><div class="wfeat-icon">🖥️</div><div class="wfeat-lbl">Multi-Format Export</div><div class="wfeat-desc">All screen sizes in one click</div></div>
  <div class="wfeat"><div class="wfeat-icon">🧠</div><div class="wfeat-lbl">Smart Crop AI</div><div class="wfeat-desc">Keeps faces &amp; logos in frame</div></div>
  <div class="wfeat"><div class="wfeat-icon">📦</div><div class="wfeat-lbl">Compression</div><div class="wfeat-desc">Smaller files, same quality</div></div>
  <div class="wfeat"><div class="wfeat-icon">📐</div><div class="wfeat-lbl">Custom Sizes</div><div class="wfeat-desc">Any ratio or resolution</div></div>
</div>
""", unsafe_allow_html=True)

    st.stop()


# ──────────────────────────────────────────────
# UI  ─  SIDEBAR
# ──────────────────────────────────────────────

with st.sidebar:
    st.image("https://cdn.prod.website-files.com/637c4a14cf822a674336049c/699f40327d75665a5a6c8852_9aa2876621654cf23c5bf38ab087b8f0_primedia-malls.png", use_container_width=True)
    st.markdown("---")

    st.subheader("⚙️ System")
    if ffmpeg_available():
        st.success(f"FFmpeg detected (`{_FFMPEG_BIN}`)")
    else:
        st.error(f"FFmpeg not found (tried: `{_FFMPEG_BIN}`)")

    st.markdown("---")
    st.subheader("🎛️ Export Settings")

    resize_mode = st.radio(
        "Resize mode",
        options=["Crop to Fit", "Stretch to Fit"],
        index=0,
        help=(
            "**Crop to Fit** — scales up then crops the edges. No distortion, but some content may be cut.\n\n"
            "**Stretch to Fit** — scales directly to the exact target size. Nothing is cut, "
            "but the video may appear squashed or stretched if the aspect ratio differs."
        ),
    )
    resize_mode_key = "crop" if resize_mode == "Crop to Fit" else "stretch"

    use_smart_crop = st.toggle(
        "🧠 Smart Crop (AI)",
        value=True,
        help="Uses YOLOv8 + EasyOCR to detect faces, text, logos and packshots, "
             "then adjusts the crop origin so they stay in frame.",
    )
    crf    = st.slider("CRF (quality — lower = better)", 12, 28, 18, help="18 is visually lossless for H.264")
    preset = st.selectbox("Encoding preset", ["ultrafast","fast","medium","slow","veryslow"], index=3)
    img_duration = st.number_input("Image → Video duration (s)", 3, 60, 10)

    st.markdown("---")
    st.subheader("📋 Log")
    if st.button("View app.log"):
        try:
            st.text_area("", _log_path.read_text()[-3000:], height=200)
        except FileNotFoundError:
            st.info("No log file yet.")


# ──────────────────────────────────────────────
# UI  ─  WELCOME SCREEN
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# UI  ─  MAIN
# ──────────────────────────────────────────────

st.title("🎬 AdScreen Converter")
st.caption("Automated multi-format video export for digital-out-of-home advertising")

# ── Page navigation buttons ────────────────────────────────────────────────────
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "convert"

st.markdown("""
<style>
.nav-bar [data-testid="stButton"] > button {
    height: 48px !important;
    min-height: 48px !important;
    border-radius: 10px !important;
    font-size: 0.95rem !important;
    font-weight: 600 !important;
    white-space: nowrap !important;
    padding: 0 28px !important;
    width: 100% !important;
    transition: all 0.15s ease !important;
}
.nav-bar [data-testid="stButton"] > button[kind="primary"] {
    background: linear-gradient(135deg, #2563eb, #3b82f6) !important;
    border: none !important;
    color: #ffffff !important;
    box-shadow: 0 4px 14px rgba(59,130,246,0.4) !important;
    text-align: center !important;
}
.nav-bar [data-testid="stButton"] > button[kind="secondary"] {
    background: #1a3350 !important;
    border: 1px solid #2d5f9e !important;
    color: #93c5fd !important;
    text-align: center !important;
}
.nav-bar [data-testid="stButton"] > button[kind="secondary"]:hover {
    background: #1e3a5f !important;
    border-color: #3b82f6 !important;
    color: #f1f5f9 !important;
    min-height: 48px !important;
    max-height: 48px !important;
}
.nav-bar [data-testid="stButton"] > button p,
.nav-bar [data-testid="stButton"] > button span {
    text-align: center !important;
    color: inherit !important;
    white-space: nowrap !important;
    font-size: 0.95rem !important;
}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="nav-bar">', unsafe_allow_html=True)
nav_col1, nav_col2, nav_spacer = st.columns([2, 2, 5])
with nav_col1:
    if st.button(
        "🖥️  Convert to Templates",
        type="primary" if st.session_state.active_tab == "convert" else "secondary",
        use_container_width=True,
        key="nav_convert",
    ):
        st.session_state.active_tab = "convert"
        st.rerun()
with nav_col2:
    if st.button(
        "📦  Compress File",
        type="primary" if st.session_state.active_tab == "compress" else "secondary",
        use_container_width=True,
        key="nav_compress",
    ):
        st.session_state.active_tab = "compress"
        st.rerun()
st.markdown('</div>', unsafe_allow_html=True)

st.markdown("---")

# ══════════════════════════════════════════════
# PAGE 2 — COMPRESS
# ══════════════════════════════════════════════

if st.session_state.active_tab == "compress":
    st.subheader("Reduce File Size")
    st.caption("Re-encodes your video at a lower bitrate without changing the resolution or cropping.")

    # Check if output files were passed over from the convert tab
    _queued = st.session_state.get("compress_queued_files", [])
    _queued = [p for p in _queued if Path(p).exists()]  # drop any stale paths

    if _queued:
        st.info(f"📂 {len(_queued)} converted file(s) ready to compress — or upload different files below.")
        if st.button("✕ Clear queued files", type="secondary", key="clear_queued"):
            st.session_state.compress_queued_files = []
            st.rerun()

    comp_uploaded = st.file_uploader(
        "📤 Upload files to compress (MP4, MOV, GIF, JPG, PNG)",
        type=["mp4", "mov", "gif", "jpg", "jpeg", "png"],
        key=f"compress_upload_{st.session_state.compress_key}",
        accept_multiple_files=True,
    )

    if comp_uploaded or _queued:
        comp_level = st.select_slider(
            "Compression level",
            options=["Light", "Medium", "High", "Maximum"],
            value="Medium",
            key="comp_level_slider",
            help="Light = best quality / larger file.  Maximum = smallest file / lower quality.",
        )
        crf_map = {"Light": 22, "Medium": 28, "High": 34, "Maximum": 40}
        comp_crf = crf_map[comp_level]

        # Show file list using metadata only — no f.read() here, runs on every render
        total_orig_mb = 0.0
        file_meta = []   # (display_name, size_mb, is_queued, source)
        for f in comp_uploaded:
            try:
                mb = (getattr(f, "size", None) or len(f.getvalue())) / 1_000_000
            except Exception:
                mb = 0.0
            total_orig_mb += mb
            file_meta.append((f.name, mb, False, f))
        for p in _queued:
            p = Path(p)
            mb = p.stat().st_size / 1_000_000
            total_orig_mb += mb
            file_meta.append((p.name, mb, True, p))

        st.markdown(f"**{len(file_meta)} file(s) queued — total {total_orig_mb:.1f} MB**")
        for name, mb, _, _ in file_meta:
            st.caption(f"• {name}  —  {mb:.1f} MB")

        if st.button("🗜️ Compress All", type="primary", key="compress_btn"):
            st.session_state.pop("comp_results", None)

            # Save uploaded files to disk one at a time (inside handler — runs once)
            comp_inputs = []
            save_err = False
            for name, mb, is_queued, src in file_meta:
                if is_queued:
                    comp_inputs.append((Path(src), mb))
                else:
                    try:
                        comp_input = INPUT_DIR / f"compress_{src.name}"
                        data = src.getvalue() if hasattr(src, "getvalue") else src.read()
                        comp_input.write_bytes(data)
                        actual_mb = comp_input.stat().st_size / 1_000_000
                        comp_inputs.append((comp_input, actual_mb))
                    except Exception as exc:
                        st.error(f"Could not save {name}: {exc}")
                        save_err = True

            if save_err:
                st.stop()

            _comp_outputs = []
            _total_new_mb = 0.0
            progress = st.progress(0)
            status   = st.empty()

            for idx, (comp_input, orig_mb) in enumerate(comp_inputs):
                display_name = comp_input.name.replace("compress_", "")
                status.markdown(f"Compressing **{display_name}** ({idx+1}/{len(comp_inputs)})…")
                safe_stem = comp_input.stem.replace("compress_", "")
                comp_out  = OUTPUT_DIR / f"compressed_{safe_stem}.mp4"
                try:
                    is_image = comp_input.suffix.lower() in (".jpg", ".jpeg", ".png")
                    is_gif   = comp_input.suffix.lower() == ".gif"
                    if is_image:
                        image_to_video(comp_input, comp_out, duration=10)
                    elif is_gif:
                        colours_map = {"Light": 256, "Medium": 128, "High": 64, "Maximum": 32}
                        fps_map     = {"Light": 25,  "Medium": 15,  "High": 10, "Maximum": 8}
                        colours = colours_map[comp_level]
                        fps     = fps_map[comp_level]
                        palette = OUTPUT_DIR / f"_palette_{safe_stem}.png"
                        _run([_FFMPEG_BIN, "-y", "-i", str(comp_input),
                              "-vf", f"fps={fps},palettegen=max_colors={colours}:stats_mode=diff",
                              str(palette)])
                        comp_out = OUTPUT_DIR / f"compressed_{safe_stem}.gif"
                        _run([_FFMPEG_BIN, "-y",
                              "-i", str(comp_input), "-i", str(palette),
                              "-filter_complex", f"fps={fps}[x];[x][1:v]paletteuse=dither=bayer",
                              str(comp_out)])
                        palette.unlink(missing_ok=True)
                    else:
                        _run([_FFMPEG_BIN, "-y",
                              "-i", str(comp_input),
                              "-c:v", "libx264", "-crf", str(comp_crf),
                              "-preset", "medium", "-an",
                              "-movflags", "+faststart",
                              str(comp_out)])
                    new_mb = comp_out.stat().st_size / 1_000_000
                    _total_new_mb += new_mb
                    _comp_outputs.append((str(comp_out), orig_mb, new_mb))
                except Exception as exc:
                    st.error(f"Failed: {display_name} — {exc}")
                    log.error("Compress failed for %s: %s", display_name, exc)
                progress.progress((idx + 1) / len(comp_inputs))

            status.empty()

            if _comp_outputs:
                # Build ZIP once now — store path so results section never recreates it
                _zip_path_str = None
                if len(_comp_outputs) > 1:
                    try:
                        _zp = zip_outputs([Path(p) for p, _, _ in _comp_outputs], OUTPUT_DIR)
                        _zip_path_str = str(_zp)
                    except Exception as exc:
                        log.error("ZIP creation failed: %s", exc)

                st.session_state.comp_results = {
                    "outputs":       _comp_outputs,
                    "total_orig_mb": total_orig_mb,
                    "total_new_mb":  _total_new_mb,
                    "zip_path":      _zip_path_str,
                }
            st.rerun()

    # ── Results — driven by session state, survives all reruns ───────────────
    _res = st.session_state.get("comp_results")
    if _res:
        _outputs = _res["outputs"]
        _t_orig  = _res["total_orig_mb"]
        _t_new   = _res["total_new_mb"]
        _pct     = ((_t_orig - _t_new) / _t_orig * 100) if _t_orig else 0

        st.success(f"✅ {len(_outputs)} file(s) compressed — {_t_orig:.1f} MB → {_t_new:.1f} MB ({_pct:.0f}% smaller)")
        st.markdown("#### 📥 Download")

        for i, (path_str, orig_mb, new_mb) in enumerate(_outputs):
            _p = Path(path_str)
            if not _p.exists():
                st.warning(f"File no longer available: {_p.name}")
                continue
            pct_f = ((orig_mb - new_mb) / orig_mb * 100) if orig_mb else 0
            _mime = "image/gif" if _p.suffix.lower() == ".gif" else "video/mp4"
            st.download_button(
                label=f"⬇ {_p.name}  ({new_mb:.1f} MB, -{pct_f:.0f}%)",
                data=open(_p, "rb"),
                file_name=_p.name,
                mime=_mime,
                key=f"comp_dl_{i}",
            )

        # ZIP was built during compression — just serve from cached path
        _zip_str = _res.get("zip_path")
        if _zip_str and Path(_zip_str).exists():
            _zp = Path(_zip_str)
            st.download_button(
                label="📦 Download All as ZIP",
                data=open(_zp, "rb"),
                file_name=_zp.name,
                mime="application/zip",
                key="comp_zip",
            )

        st.markdown("---")
        st.markdown("#### What would you like to do next?")
        _ca, _cb, _cc = st.columns(3)
        with _ca:
            if st.button("🗜️ Compress More Files", type="secondary",
                         use_container_width=True, key="comp_next_more"):
                st.session_state.compress_key += 1
                st.session_state.pop("comp_results", None)
                st.session_state.compress_queued_files = []
                st.rerun()
        with _cb:
            if st.button("🖥️ Convert Files", type="secondary",
                         use_container_width=True, key="comp_next_convert"):
                st.session_state.active_tab = "convert"
                st.rerun()
        with _cc:
            if st.button("🗑️ Start Fresh", type="secondary",
                         use_container_width=True, key="comp_next_fresh"):
                for _k in ["selected_keys", "custom_formats", "ae_template",
                            "compress_queued_files", "comp_results"]:
                    st.session_state.pop(_k, None)
                st.session_state.upload_key += 1
                st.session_state.compress_key += 1
                st.session_state.active_tab = "convert"
                st.rerun()

    elif not (comp_uploaded or _queued):
        st.info("Upload one or more video files to get started.")


# ══════════════════════════════════════════════
# TAB 1 — CONVERT
# ══════════════════════════════════════════════

def _step_header(num: int, label: str, done: bool = False):
    badge = (
        "<span style='background:#16a34a;color:#fff;border-radius:50%;display:inline-flex;"
        "align-items:center;justify-content:center;width:28px;height:28px;font-size:0.85rem;"
        "font-weight:700;margin-right:10px;flex-shrink:0;'>✓</span>"
        if done else
        f"<span style='background:#3b82f6;color:#fff;border-radius:50%;display:inline-flex;"
        f"align-items:center;justify-content:center;width:28px;height:28px;font-size:0.85rem;"
        f"font-weight:700;margin-right:10px;flex-shrink:0;'>{num}</span>"
    )
    st.markdown(
        f"<div style='display:flex;align-items:center;margin:22px 0 8px;'>"
        f"{badge}<span style='font-size:1.1rem;font-weight:700;color:#f1f5f9;'>{label}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

if st.session_state.active_tab == "convert":
    templates = load_templates()

    if not templates:
        st.warning(
            "No templates found in `templates/`. "
            "Add a JSON file like the example below to get started."
        )
        st.code(
            json.dumps(
                {
                    "name": "Mall Atrium",
                    "description": "Landscape + portrait screens at main entrance",
                    "tags": ["mall", "atrium"],
                    "formats": [[1920, 1080], [1080, 1920], [1080, 1080]],
                    "safe_zones": [{"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8}],
                },
                indent=2,
            ),
            language="json",
        )
        st.stop()

    # ── Init session state ─────────────────────────
    if "selected_keys" not in st.session_state:
        st.session_state.selected_keys = []
    if "custom_formats" not in st.session_state:
        st.session_state.custom_formats = []

    all_keys = sorted(templates.keys())
    step1_done = bool(st.session_state.selected_keys or st.session_state.custom_formats)

    # ════════════════════════════════════
    # STEP 1 — Select Templates
    # ════════════════════════════════════
    _step_header(1, "Select Screen Template(s)", done=step1_done)

    # CSS — card-style template buttons (height scoped to pre-line cards only, not nav)
    st.markdown("""
<style>
/* Unselected template card */
[data-testid="stButton"] > button[kind="secondary"] {
    min-height: 72px !important;
    text-align: left !important;
    padding: 10px 14px !important;
    white-space: pre-line !important;
    line-height: 1.45 !important;
    font-size: 0.86rem !important;
    border-radius: 10px !important;
    background: #0f1f35 !important;
    border: 2px solid #2d3748 !important;
    color: #cbd5e1 !important;
}
[data-testid="stButton"] > button[kind="secondary"]:hover {
    border-color: #3b82f6 !important;
    background: #1a2f50 !important;
    color: #f1f5f9 !important;
}
[data-testid="stButton"] > button[kind="secondary"] p,
[data-testid="stButton"] > button[kind="secondary"] span {
    text-align: left !important;
    color: inherit !important;
    white-space: pre-line !important;
}
/* Selected template card */
[data-testid="stButton"] > button[kind="primary"] {
    min-height: 72px !important;
    text-align: left !important;
    padding: 10px 14px !important;
    white-space: pre-line !important;
    line-height: 1.45 !important;
    font-size: 0.86rem !important;
    border-radius: 10px !important;
    background: #1e3a5f !important;
    border: 2px solid #60a5fa !important;
    box-shadow: 0 0 0 3px rgba(96,165,250,0.2) !important;
}
[data-testid="stButton"] > button[kind="primary"] p,
[data-testid="stButton"] > button[kind="primary"] span {
    text-align: left !important;
    color: #f1f5f9 !important;
    white-space: pre-line !important;
    font-weight: 600 !important;
}
</style>
""", unsafe_allow_html=True)

    # Search bar — reruns on every keystroke (no Enter required)
    if "tmpl_search" not in st.session_state:
        st.session_state.tmpl_search = ""
    st.text_input(
        "Search templates",
        placeholder="🔍  Type to search templates…",
        label_visibility="collapsed",
        key="tmpl_search",
        on_change=lambda: None,  # fires rerun on every character change
    )
    search_q = st.session_state.tmpl_search

    if not search_q:
        # Show selected chips even when not searching
        if not st.session_state.selected_keys:
            st.caption("Type above to search and select templates.")
    else:
        filtered_keys = [k for k in all_keys if search_q.lower() in k.lower()]
        if not filtered_keys:
            st.caption("No templates match your search.")
        else:
            st.caption(f"{len(filtered_keys)} result(s) — click a card to select / deselect")
            COLS = 3
            rows = [filtered_keys[i:i+COLS] for i in range(0, len(filtered_keys), COLS)]
            for row in rows:
                cols = st.columns(COLS)
                for col, key in zip(cols, row):
                    t = templates[key]
                    selected = key in st.session_state.selected_keys
                    short_name = t.name if len(t.name) <= 26 else t.name[:24] + "…"
                    label = f"{'✓  ' if selected else ''}{short_name}\n{t.primary_resolution}  ·  {len(t.formats)} format(s)"
                    css_class = "tmpl-card-btn-sel" if selected else "tmpl-card-btn"
                    with col:
                        st.markdown(f'<div class="{css_class}">', unsafe_allow_html=True)
                        if st.button(label, key=f"tmpl_btn_{key}", use_container_width=True):
                            if selected:
                                st.session_state.selected_keys.remove(key)
                            else:
                                st.session_state.selected_keys.append(key)
                            st.rerun()
                        st.markdown('</div>', unsafe_allow_html=True)

    if st.session_state.selected_keys:
        st.markdown("---")
        c1, c2 = st.columns([6, 1])
        with c1:
            st.caption("**Selected:** " + "  ·  ".join(
                templates[k].name for k in st.session_state.selected_keys
            ))
        with c2:
            if st.button("✕ Clear all", type="secondary", key="clear_all_tmpl"):
                st.session_state.selected_keys = []
                st.rerun()

    # ── Optional: Custom screen size ───────────────
    _RATIOS = {
        "16:9  (Landscape HD)":     (16, 9),
        "9:16  (Portrait HD)":      (9, 16),
        "4:3   (Classic)":          (4, 3),
        "3:4   (Portrait classic)": (3, 4),
        "21:9  (Ultrawide)":        (21, 9),
        "32:9  (Super ultrawide)":  (32, 9),
        "2:1   (Slim landscape)":   (2, 1),
        "1:2   (Slim portrait)":    (1, 2),
        "1:1   (Square)":           (1, 1),
        "Custom ratio":             None,
    }

    with st.expander("➕ Add Custom Screen Size (optional)", expanded=False):
        mode = st.radio("Input mode", ["By Dimensions", "By Ratio"], horizontal=True, key="custom_mode")

        if mode == "By Dimensions":
            cc1, cc2, cc3, cc4 = st.columns([2, 2, 3, 1])
            with cc1:
                custom_w = st.number_input("Width (px)", min_value=1, max_value=7680, value=1920, step=1, key="custom_w")
            with cc2:
                custom_h = st.number_input("Height (px)", min_value=1, max_value=4320, value=1080, step=1, key="custom_h")
            with cc3:
                custom_label = st.text_input("Label (optional)", placeholder="e.g. Lobby Screen", key="custom_label")
            with cc4:
                st.markdown("<div style='margin-top:28px'>", unsafe_allow_html=True)
                add_clicked = st.button("Add", key="add_custom_dim")
                st.markdown("</div>", unsafe_allow_html=True)
            final_w, final_h = int(custom_w), int(custom_h)
            final_label = custom_label.strip() or f"{final_w}×{final_h}"
            if add_clicked:
                entry = (final_w, final_h, final_label)
                if entry not in st.session_state.custom_formats:
                    st.session_state.custom_formats.append(entry)
                    st.rerun()

        else:  # By Ratio
            rc1, rc2, rc3 = st.columns([3, 2, 2])
            with rc1:
                ratio_choice = st.selectbox("Aspect ratio", list(_RATIOS.keys()), key="custom_ratio")
            ratio_val = _RATIOS[ratio_choice]
            if ratio_val is None:
                cr1, cr2 = st.columns(2)
                with cr1:
                    rw_in = st.number_input("Ratio W", min_value=1, value=16, key="cratio_w")
                with cr2:
                    rh_in = st.number_input("Ratio H", min_value=1, value=9, key="cratio_h")
                ratio_val = (int(rw_in), int(rh_in))

            with rc2:
                anchor = st.selectbox("Set", ["Width", "Height"], key="custom_anchor")
            with rc3:
                anchor_px = st.number_input(
                    f"{anchor} (px)", min_value=8, max_value=7680, value=1920, step=8, key="custom_anchor_px"
                )

            rw, rh = ratio_val
            if anchor == "Width":
                final_w = int(anchor_px)
                final_h = int(round(anchor_px * rh / rw / 2) * 2)
            else:
                final_h = int(anchor_px)
                final_w = int(round(anchor_px * rw / rh / 2) * 2)

            ratio_label_str = f"{rw}:{rh}"
            st.info(f"Calculated resolution: **{final_w}×{final_h}** ({ratio_label_str} at {int(anchor_px)}px {anchor.lower()})")

            rl1, rl2 = st.columns([4, 1])
            with rl1:
                custom_label_r = st.text_input("Label (optional)", placeholder=f"e.g. {ratio_label_str} Screen", key="custom_label_r")
            with rl2:
                st.markdown("<div style='margin-top:28px'>", unsafe_allow_html=True)
                add_clicked_r = st.button("Add", key="add_custom_ratio")
                st.markdown("</div>", unsafe_allow_html=True)
            final_label = custom_label_r.strip() or f"{ratio_label_str} — {final_w}×{final_h}"
            if add_clicked_r:
                entry = (final_w, final_h, final_label)
                if entry not in st.session_state.custom_formats:
                    st.session_state.custom_formats.append(entry)
                    st.rerun()

        if st.session_state.custom_formats:
            st.markdown("**Custom sizes queued:**")
            for i, (cw, ch, cl) in enumerate(st.session_state.custom_formats):
                col_info, col_del = st.columns([5, 1])
                with col_info:
                    ar = cw / ch
                    st.markdown(f"- **{cl}** — {cw}×{ch} &nbsp;`{ar:.2f}:1`")
                with col_del:
                    if st.button("✕", key=f"del_custom_{i}"):
                        st.session_state.custom_formats.pop(i)
                        st.rerun()

    # ── Optional: After Effects import ─────────────
    with st.expander("🎬 Import After Effects compositions (optional)", expanded=False):
        st.markdown(
            "Run **`export_comps.jsx`** inside After Effects *(File → Scripts → Run Script File…)*  "
            "then upload the generated `_comps.json` here."
        )
        ae_json_file = st.file_uploader(
            "Upload AE compositions JSON",
            type=["json"],
            key="ae_json_upload",
            label_visibility="collapsed",
        )
        if ae_json_file:
            try:
                ae_data = json.loads(ae_json_file.read())
                if ae_data.get("source") != "AfterEffects" or "compositions" not in ae_data:
                    st.error("This doesn't look like a file exported by export_comps.jsx.")
                else:
                    comps = ae_data["compositions"]
                    project_name = ae_data.get("project", "AfterEffects")
                    ae_formats = [
                        (int(c["width"]), int(c["height"]), c["name"])
                        for c in comps
                        if c.get("width") and c.get("height") and c.get("name")
                    ]
                    if not ae_formats:
                        st.warning("No valid compositions found in the JSON.")
                    else:
                        ae_tmpl = Template(
                            name=project_name,
                            formats=ae_formats,
                            description=f"Imported from After Effects — {ae_data.get('exported', '')}",
                            tags=["aftereffects"],
                        )
                        st.session_state["ae_template"] = ae_tmpl
                        st.success(f"✅ Loaded **{len(ae_formats)}** composition(s) from **{project_name}**")
                        ae_rows = [
                            f"| {c['name']} | {c['width']}×{c['height']} | {round(c.get('frameRate', 0), 2)} fps | {round(c.get('duration', 0), 2)}s |"
                            for c in comps
                        ]
                        st.markdown(
                            "| Composition | Resolution | Frame Rate | Duration |\n"
                            "|---|---|---|---|\n" + "\n".join(ae_rows)
                        )
            except Exception as exc:
                st.error(f"Could not parse JSON: {exc}")

    ae_tmpl_extra: Optional[Template] = st.session_state.get("ae_template")

    # ── Gate: must have at least one template ──────
    selected_keys = st.session_state.selected_keys
    selected_templates: list[Template] = [templates[k] for k in selected_keys]

    custom_tmpl: Optional[Template] = None
    if st.session_state.custom_formats:
        custom_tmpl = Template(
            name="Custom",
            formats=list(st.session_state.custom_formats),
            description="User-defined custom screen sizes",
            tags=["custom"],
        )

    if not selected_keys and not st.session_state.custom_formats:
        st.markdown("---")
        st.info("👆 Select at least one template above to continue.")
        st.stop()

    # ════════════════════════════════════
    # STEP 2 — Upload Master File
    # ════════════════════════════════════
    total_formats = sum(len(t.formats) for t in selected_templates)
    if custom_tmpl:
        total_formats += len(custom_tmpl.formats)

    st.markdown("---")
    _step_header(2, "Upload Your Master File")

    uploaded = st.file_uploader(
        "Upload master file",
        type=["mp4", "mov", "jpg", "jpeg", "png"],
        label_visibility="collapsed",
        key=f"master_upload_{st.session_state.upload_key}",
    )

    if not uploaded:
        st.info("Upload an MP4, MOV, JPG or PNG file to continue.")
        st.stop()

    for _old in INPUT_DIR.iterdir():
        try:
            _old.unlink()
        except Exception:
            pass

    file_ext = Path(uploaded.name).suffix.lower()
    input_path = INPUT_DIR / uploaded.name
    input_path.write_bytes(uploaded.read())
    st.success(f"✅ Uploaded **{uploaded.name}**")

    # ── Image → Video conversion ───────────────────
    if file_ext in (".jpg", ".jpeg", ".png"):
        st.info(f"Still image detected — will be converted to a {img_duration}s video.")
        video_path = INPUT_DIR / "converted_video.mp4"
        with st.spinner("Converting image to video…"):
            try:
                image_to_video(input_path, video_path, img_duration)
                input_path = video_path
                st.success("Image converted to video ✓")
            except RuntimeError as exc:
                st.error(f"Conversion failed: {exc}")
                st.stop()

    # ── Probe & preview ────────────────────────────
    try:
        video_w, video_h, duration = probe_video(input_path)
    except RuntimeError as exc:
        st.error(f"Could not read video metadata: {exc}")
        st.stop()

    thumb_path = PREVIEW_DIR / "thumb.jpg"
    generate_thumbnail(input_path, thumb_path)

    # ── Smart crop detection ────────────────────────
    importance_boxes: list[tuple[int, int, int, int]] = []
    if use_smart_crop:
        with st.spinner("🧠 Analysing content (faces, text, logos)…"):
            try:
                from smart_crop import extract_keyframes, detect_importance_regions, draw_detections
                frames = extract_keyframes(input_path)
                importance_boxes = detect_importance_regions(frames)
                if importance_boxes and thumb_path.exists():
                    overlay_path = PREVIEW_DIR / "thumb_overlay.jpg"
                    draw_detections(thumb_path, importance_boxes, overlay_path)
            except Exception as exc:
                st.warning(f"Smart crop analysis unavailable: {exc}")

    col_a, col_b = st.columns([1, 2])
    with col_a:
        if use_smart_crop and importance_boxes:
            overlay_path = PREVIEW_DIR / "thumb_overlay.jpg"
            if overlay_path.exists():
                st.image(str(overlay_path), caption=f"Detected {len(importance_boxes)} region(s)", use_container_width=True)
            elif thumb_path.exists():
                st.image(str(thumb_path), caption="Preview", use_container_width=True)
        elif thumb_path.exists():
            st.image(str(thumb_path), caption="Preview", use_container_width=True)
    with col_b:
        st.markdown("**Source file**")
        st.markdown(f"- Resolution: **{video_w}×{video_h}**")
        if duration:
            st.markdown(f"- Duration: **{duration:.1f}s**")
        st.markdown(f"- Templates selected: **{len(selected_templates)}**")
        st.markdown(f"- Total outputs: **{total_formats}**")
        if use_smart_crop:
            if importance_boxes:
                st.success(f"🧠 Smart crop active — {len(importance_boxes)} important region(s) detected")
            else:
                st.info("🧠 Smart crop: no regions detected, using centre-crop")

    # ── Pre-export plan ─────────────────────────────
    effective_templates = list(selected_templates)
    if ae_tmpl_extra and ae_tmpl_extra not in effective_templates:
        effective_templates.append(ae_tmpl_extra)
    if custom_tmpl:
        effective_templates.append(custom_tmpl)

    all_export_jobs: list[tuple[Template, FormatPlan]] = []
    for tmpl in effective_templates:
        for plan in plan_exports(tmpl, video_w, video_h,
                                 importance_boxes=importance_boxes or None):
            all_export_jobs.append((tmpl, plan))

    # ════════════════════════════════════
    # STEP 3 — Export
    # ════════════════════════════════════
    st.markdown("---")
    _step_header(3, "Export All Formats")
    st.caption(
        f"{len(all_export_jobs)} output(s) will be generated across "
        f"{len(effective_templates)} template(s)."
    )

    if not st.button("🚀 Export Now", type="primary", use_container_width=True):
        st.stop()

    for _old in OUTPUT_DIR.iterdir():
        try:
            _old.unlink()
        except Exception:
            pass
    for _old in PREVIEW_DIR.iterdir():
        try:
            _old.unlink()
        except Exception:
            pass

    output_files: list[Path] = []
    errors: list[str]        = []

    progress_bar = st.progress(0)
    status_text  = st.empty()
    total        = len(all_export_jobs)

    for i, (tmpl, plan) in enumerate(all_export_jobs):
        file_label = plan.label if plan.label else tmpl.name.replace(' ', '_')
        out = OUTPUT_DIR / f"{file_label}_{plan.width}x{plan.height}.mp4"
        status_text.markdown(
            f"⏳ Exporting **{tmpl.name}** — {plan.width}×{plan.height}  ({i+1}/{total})…"
        )

        try:
            export_format(
                src=input_path,
                dst=out,
                width=plan.width,
                height=plan.height,
                crop_x=plan.crop_x,
                crop_y=plan.crop_y,
                crf=crf,
                preset=preset,
                resize_mode=resize_mode_key,
            )
            output_files.append(out)
            log.info("Exported %s", out.name)
        except RuntimeError as exc:
            err_msg = f"{tmpl.name} {plan.width}×{plan.height}: {exc}"
            errors.append(err_msg)
            log.error(err_msg)

        progress_bar.progress((i + 1) / total)

    status_text.empty()

    # ── Results ────────────────────────────────────
    st.markdown("---")

    if output_files:
        st.success(f"✅ {len(output_files)} of {total} format(s) exported successfully.")

        # Thumbnail previews
        thumb_cols = st.columns(min(len(output_files), 4))
        for idx, out_file in enumerate(output_files):
            thumb = PREVIEW_DIR / f"thumb_{out_file.stem}.jpg"
            generate_thumbnail(out_file, thumb)
            res = out_file.stem.rsplit("_", 1)[-1].replace("x", "×") if "_" in out_file.stem else out_file.stem
            file_size = out_file.stat().st_size / 1_000_000
            with thumb_cols[idx % 4]:
                if thumb.exists():
                    st.image(str(thumb), caption=f"{res} — {file_size:.1f} MB", use_container_width=True)
                else:
                    st.markdown(f"**{res}** — {file_size:.1f} MB")

        # Download buttons
        st.markdown("#### 📥 Download")
        dl_cols = st.columns(min(len(output_files), 4))
        for idx, out_file in enumerate(output_files):
            res = out_file.stem.rsplit("_", 1)[-1].replace("x", "×") if "_" in out_file.stem else out_file.stem
            with dl_cols[idx % 4]:
                st.download_button(
                    label=f"⬇ {res}",
                    data=out_file.read_bytes(),
                    file_name=out_file.name,
                    mime="video/mp4",
                    key=f"dl_{idx}",
                    use_container_width=True,
                )

        if len(output_files) > 1:
            zip_path = zip_outputs(output_files, OUTPUT_DIR)
            st.download_button(
                label="📦 Download All as ZIP",
                data=zip_path.read_bytes(),
                file_name=zip_path.name,
                mime="application/zip",
                use_container_width=True,
            )

        # ── Post-download actions ──────────────────────────────────────────
        st.markdown("---")
        st.markdown("#### What would you like to do next?")
        _na, _nb, _nc = st.columns(3)
        with _na:
            if st.button("🔄 Convert Another File", type="secondary",
                         use_container_width=True, key="next_convert_another"):
                st.session_state.upload_key += 1
                st.rerun()
        with _nb:
            if st.button("🗜️ Compress These Files", type="secondary",
                         use_container_width=True, key="next_compress_these"):
                st.session_state.compress_queued_files = [str(p) for p in output_files]
                st.session_state.active_tab = "compress"
                st.rerun()
        with _nc:
            if st.button("🗑️ Start Fresh", type="secondary",
                         use_container_width=True, key="next_start_fresh"):
                for _k in ["selected_keys", "custom_formats", "ae_template",
                            "compress_queued_files"]:
                    st.session_state.pop(_k, None)
                st.session_state.upload_key += 1
                st.session_state.compress_key += 1
                st.rerun()

    if errors:
        with st.expander(f"⚠️ {len(errors)} format(s) failed — click to see details"):
            for e in errors:
                st.error(e)
