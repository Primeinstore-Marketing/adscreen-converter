
import streamlit as st
import ui
import hashlib
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


# ── Keep folder paths on folder uploads ───────────────────────────────────────
# For a folder upload the browser reports each file as "Campaign/Menlyn/ad.jpg"
# in the widget state, but Streamlit's server stores only the multipart file
# name ("ad.jpg") and UploadedFile.name comes from that — so the folder and its
# subfolders were lost before the app ever saw them. Restore the reported path.
# Wrapped defensively: if Streamlit's internals change, uploads still work and
# simply arrive without their folder path.
def _keep_upload_paths() -> None:
    try:
        import streamlit.elements.widgets.file_uploader as _fu
        from streamlit.runtime.uploaded_file_manager import UploadedFile as _UF
    except Exception:
        return
    _orig = getattr(_fu, "_get_upload_files", None)
    if _orig is None or getattr(_orig, "_keeps_paths", False):
        return

    def _get_upload_files(widget_value):
        files = _orig(widget_value)
        try:
            names = {i.file_id: i.name for i in widget_value.uploaded_file_info}
            for f in files:
                if isinstance(f, _UF) and "/" in names.get(f.file_id, ""):
                    f.name = names[f.file_id]
        except Exception:
            pass
        return files

    _get_upload_files._keeps_paths = True
    _fu._get_upload_files = _get_upload_files

_keep_upload_paths()

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

# ── Static file serving: files written here are served at /app/static/… ─────
# This lets downloads bypass Streamlit's Python process RAM entirely.
STATIC_SESS_DIR = BASE_DIR / "static" / "sessions" / _sess
STATIC_SESS_DIR.mkdir(parents=True, exist_ok=True)

# Sweep old session static dirs (> 2 h) to keep disk usage low
import time as _time
_static_sessions_root = BASE_DIR / "static" / "sessions"
try:
    _now_ts = _time.time()
    for _old_sdir in _static_sessions_root.iterdir():
        if _old_sdir.name != _sess and _old_sdir.is_dir():
            try:
                if _now_ts - _old_sdir.stat().st_mtime > 7200:
                    shutil.rmtree(_old_sdir, ignore_errors=True)
            except Exception:
                pass
except Exception:
    pass

# ── Keepalive: ping Streamlit's health endpoint every 30 s to prevent sleep ───
import streamlit.components.v1 as _stc
_stc.html("""
<script>
(function() {
    function ping() {
        fetch('/_stcore/health').catch(function(){});
    }
    ping();
    setInterval(ping, 30000);
})();
</script>
""", height=1)

# ── Global colour theme ────────────────────────────────────────────────────────
ui.inject_theme()

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
    max_duration: "int | None" = None,
    scaled_w: int = 0,
    scaled_h: int = 0,
) -> subprocess.CompletedProcess:
    """Export a video to the target resolution.

    resize_mode:
      "crop"    — scale up then crop (no distortion, may cut edges)
      "stretch" — scale directly to exact dimensions (may squash/stretch)

    crop_x / crop_y are in the coordinate space of the scaled frame
    (scaled_w × scaled_h). The scale is given explicitly rather than left to
    force_original_aspect_ratio, whose rounding differed from the planner's —
    the crop origin then pointed at a frame of a different size.

    max_duration:
      Hard cap in seconds. DOOH playout slots are fixed, so anything longer is
      trimmed to fit. Sources already under the cap are left untouched — padding
      them would alter the creative. Previously no cap existed at all and
      uploaded videos exported at their original length.
    """
    if resize_mode == "stretch" or not (scaled_w and scaled_h):
        vf = f"scale={width}:{height}:flags=lanczos,setsar=1"
    else:
        vf = (
            f"scale={scaled_w}:{scaled_h}:flags=lanczos,"
            f"crop={width}:{height}:{crop_x}:{crop_y},setsar=1"
        )
    cmd = [_FFMPEG_BIN, "-y", "-i", str(src)]
    if max_duration:
        cmd += ["-t", str(max_duration)]
    cmd += [
        "-vf", vf,
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", preset,
        # Without this, 4:2:2 / 4:4:4 masters (ProRes, PNG) produced High 4:2:2
        # profile files that many screen players refuse to decode.
        "-pix_fmt", "yuv420p",
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
    scaled_w: int = 0
    scaled_h: int = 0


def plan_exports(
    template: Template,
    video_w: int,
    video_h: int,
    importance_boxes: list = None,
) -> list[FormatPlan]:
    """Centre-crop plan, or smart-crop when importance_boxes are provided.

    Crop origins are in the scaled frame. The centre crop used to be computed
    from the *source* size, so any target larger than or shaped differently
    from the source came out off-centre (a 1920×1080 master cropped to
    1080×1920 was cut 420px from the left instead of 1166px).
    """
    from smart_crop import smart_crop_origin, _scaled_dims
    plans = []
    for fmt in template.formats:
        target_w, target_h = fmt[0], fmt[1]
        label = fmt[2] if len(fmt) > 2 else ""
        scaled_w, scaled_h = _scaled_dims(video_w, video_h, target_w, target_h)
        if importance_boxes:
            crop_x, crop_y = smart_crop_origin(
                video_w, video_h, target_w, target_h, importance_boxes
            )
        else:
            crop_x = (scaled_w - target_w) // 2
            crop_y = (scaled_h - target_h) // 2
        plans.append(FormatPlan(width=target_w, height=target_h, crop_x=crop_x,
                                crop_y=crop_y, label=label,
                                scaled_w=scaled_w, scaled_h=scaled_h))
    return plans


def _keep_name(text: str) -> str:
    """The user's own file name, kept as typed (spaces, case, punctuation) —
    only characters that are invalid in file names on Windows or macOS go."""
    import re
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text).strip(" .") or "export"


def _safe_name(text: str) -> str:
    """Filesystem-safe file stem: no path separators or shell-hostile characters."""
    import re
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._") or "export"


# ──────────────────────────────────────────────
# ZIP HELPER
# ──────────────────────────────────────────────

def zip_outputs(files: list[Path], output_dir: Path, root: Optional[Path] = None,
                name: str = "export") -> Path:
    """Bundle files into a ZIP. With `root`, entries keep their folder path
    relative to it; otherwise they are stored flat by file name.

    Entries are stored, not deflated: MP4/JPEG/PNG are already compressed, so
    deflate only burned CPU time for a ~1% gain."""
    # Named after the user's own files, without a timestamp suffix. Each
    # export/compress run writes into a freshly cleared directory, so this
    # cannot collide with a previous run's ZIP.
    zip_path = output_dir / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED) as zf:
        for f in files:
            zf.write(f, str(f.relative_to(root)) if root else f.name)
    return zip_path


# ──────────────────────────────────────────────
# UI  ─  WELCOME SCREEN  (must come first so st.stop() blocks everything below)
# ──────────────────────────────────────────────

if "welcomed" not in st.session_state:
    st.session_state.welcomed = False

if not st.session_state.welcomed:
    import base64 as _b64
    _logo_path = BASE_DIR / "img" / "logo_white.png"
    _logo_tag = ""
    if _logo_path.exists():
        _b = _b64.b64encode(_logo_path.read_bytes()).decode()
        _logo_tag = (f"<img src='data:image/png;base64,{_b}' alt='Primedia Malls' "
                     "style='max-width:180px;width:100%;display:block;margin:0 auto 28px' />")

    st.markdown(f"""
<style>
[data-testid="stSidebar"] {{ display: none; }}
[data-testid="stMainBlockContainer"] {{ max-width: 720px; padding-top: 8vh; }}
.w-eyebrow {{
    color: var(--c-text-muted); font-size: .76rem; font-weight: 600;
    letter-spacing: .14em; text-transform: uppercase; text-align: center;
}}
.w-title {{
    color: var(--c-text); font-size: 2.1rem; font-weight: 700;
    letter-spacing: -.025em; text-align: center; margin: var(--s-3) 0 var(--s-2);
}}
.w-sub {{
    color: var(--c-text-secondary); font-size: .96rem; text-align: center;
    max-width: 46ch; margin: 0 auto var(--s-6);
}}
.w-feats {{
    display: grid; grid-template-columns: repeat(2, 1fr); gap: var(--s-3);
    margin-top: var(--s-6);
}}
.w-feat {{
    background: var(--c-surface); border: 1px solid var(--c-border);
    border-radius: var(--r-card); padding: var(--s-4);
}}
.w-feat-lbl  {{ color: var(--c-text); font-weight: 600; font-size: .9rem; }}
.w-feat-desc {{ color: var(--c-text-muted); font-size: .82rem; margin-top: 2px; }}
@media (max-width: 640px) {{
    .w-feats {{ grid-template-columns: 1fr; }}
    .w-title {{ font-size: 1.6rem; }}
}}
</style>

{_logo_tag}
<div class="w-eyebrow">Primedia Out of Home</div>
<div class="w-title">AdScreen Converter</div>
<div class="w-sub">Export one master file to every mall screen format — correctly
sized, cropped and compressed.</div>
""", unsafe_allow_html=True)

    _wa, _wb, _wc = st.columns([1, 1.4, 1])
    with _wb:
        if st.button("Get started", type="primary", use_container_width=True,
                     key="welcome_start"):
            st.session_state.welcomed = True
            st.rerun()

    st.markdown("""
<div class="w-feats">
  <div class="w-feat"><div class="w-feat-lbl">Multi-format export</div>
    <div class="w-feat-desc">Every selected screen size in one run</div></div>
  <div class="w-feat"><div class="w-feat-lbl">Smart crop</div>
    <div class="w-feat-desc">Keeps the busiest part of the frame in shot</div></div>
  <div class="w-feat"><div class="w-feat-lbl">Compression</div>
    <div class="w-feat-desc">Hit a target file size without visible loss</div></div>
  <div class="w-feat"><div class="w-feat-lbl">Custom sizes</div>
    <div class="w-feat-desc">Any resolution or aspect ratio</div></div>
</div>
""", unsafe_allow_html=True)

    st.stop()


# ──────────────────────────────────────────────
# UI  ─  SIDEBAR
# ──────────────────────────────────────────────

with st.sidebar:
    _sidebar_logo = BASE_DIR / "img" / "logo_white.png"
    if _sidebar_logo.exists():
        st.image(str(_sidebar_logo), use_container_width=True)

    # Smart Crop's capability depends on what is actually installed. The UI used
    # to promise "YOLOv8 + EasyOCR" unconditionally; neither is in
    # requirements.txt, so it silently ran OpenCV saliency instead. Report the
    # tier that will really be used.
    @st.cache_data(ttl=300)
    def _detection_tier() -> str:
        try:
            import ultralytics  # noqa: F401
            return "object detection (YOLOv8)"
        except Exception:
            return "edge & saliency detection (OpenCV)"

    use_smart_crop = st.toggle(
        "Smart crop",
        value=True,
        help=f"Finds the busiest part of the frame using {_detection_tier()} and "
             "shifts the crop so it stays in shot. Applies to Crop to Fit only.",
    )
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

    # DOOH playout slots are fixed lengths, so every export is capped here.
    # 10s and 15s are the standard slots; Custom covers non-standard bookings.
    _slot_choice = st.radio(
        "Output duration",
        options=[10, 15, "custom"],
        format_func=lambda s_: "Custom" if s_ == "custom" else f"{s_} seconds",
        index=0,
        horizontal=True,
        help="Stills are rendered to exactly this length. Videos longer than "
             "this are trimmed to fit; shorter videos are left as they are.",
    )
    if _slot_choice == "custom":
        slot_duration = int(st.number_input(
            "Custom duration (seconds)",
            min_value=1, max_value=300, value=20, step=1,
            key="custom_slot_duration",
        ))
    else:
        slot_duration = _slot_choice

    # Encoder settings are set-once expert controls — progressive disclosure
    # rather than permanent real estate.
    with st.expander("Encoding options"):
        crf    = st.slider("Quality (CRF — lower is better)", 12, 28, 18,
                           help="18 is visually lossless for H.264.")
        preset = st.selectbox("Encoding preset",
                              ["ultrafast", "fast", "medium", "slow", "veryslow"], index=3,
                              help="Slower presets give smaller files at the same quality.")

    with st.expander("Diagnostics"):
        if ffmpeg_available():
            st.caption(f"FFmpeg ready — `{_FFMPEG_BIN}`")
        else:
            st.error(f"FFmpeg not found (tried `{_FFMPEG_BIN}`). Exports will fail.")
        st.caption(f"Smart crop tier: {_detection_tier()}")
        if st.button("Show recent log", use_container_width=True):
            try:
                st.text_area("Recent log output", _log_path.read_text()[-3000:],
                             height=200, label_visibility="collapsed")
            except FileNotFoundError:
                st.caption("No log file yet.")


# ──────────────────────────────────────────────
# UI  ─  WELCOME SCREEN
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# UI  ─  MAIN
# ──────────────────────────────────────────────

ui.page_header("AdScreen Converter",
               "Multi-format video export for digital out-of-home advertising")

# ── Page navigation buttons ────────────────────────────────────────────────────
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "convert"


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

st.markdown("---")

# ══════════════════════════════════════════════
# PAGE 2 — COMPRESS
# ══════════════════════════════════════════════

_IMG_EXT   = {".jpg", ".jpeg", ".png", ".webp"}
_VID_EXT   = {".mp4", ".mov", ".m4v"}
_GIF_EXT   = {".gif"}
_MEDIA_EXT = _IMG_EXT | _VID_EXT | _GIF_EXT

# level: (video CRF, JPEG/WebP quality, GIF colours, GIF fps)
# Resolution is never changed. The old CRF ladder (22/28/34/40) went well past
# the point of visible blocking; this one tops out where H.264 still holds up
# on a large screen.
COMPRESS_LEVELS = {
    "Light":   (20, 92, 256, 25),
    "Medium":  (23, 86, 192, 20),
    "High":    (26, 80, 128, 15),
    "Maximum": (30, 72, 64, 12),
}

COMPRESS_ROOT = _BASE_IO_DIR / "sessions" / _sess / "compress"

import os as _os
_COMPRESS_WORKERS = max(1, min(4, (_os.cpu_count() or 2) // 2))
_FFMPEG_THREADS   = max(1, (_os.cpu_count() or 2) // _COMPRESS_WORKERS)


def _clean_relpath(name: str) -> Optional[Path]:
    """Turn an uploaded name ("Campaign/Rosebank/ad.jpg") into a safe relative
    path, dropping anything that could climb out of the work directory."""
    from pathlib import PurePosixPath
    parts = [p for p in PurePosixPath(name.replace("\\", "/")).parts
             if p not in ("", ".", "..", "/")]
    # __MACOSX/ and ._name are Finder metadata inside ZIPs, not media.
    if not parts or parts[0] == "__MACOSX" or parts[-1].startswith("."):
        return None
    return Path(*parts)


def _batch_name(rel_paths: list[str]) -> str:
    """ZIP name for a compress batch, taken from the user's own names:
    the uploaded folder if everything came from one; otherwise the part the
    file names share ("Summer Sale 1080x1920", "Summer Sale 600x720" →
    "Summer Sale"); otherwise the first file's name."""
    import os.path
    paths = [Path(p) for p in rel_paths]
    tops = {p.parts[0] for p in paths if len(p.parts) > 1}
    if len(tops) == 1 and all(len(p.parts) > 1 for p in paths):
        return _keep_name(tops.pop())
    stems = sorted(p.stem for p in paths)
    shared = os.path.commonprefix(stems).rstrip(" _-.(")
    if len(paths) > 1 and len(shared) >= 3:
        return _keep_name(shared)
    return _keep_name(stems[0])


def compress_one(src: Path, dst: Path, level: str) -> tuple[Path, bool]:
    """Compress one file at its original resolution.

    Returns (written_path, kept_original). If the re-encode comes out no
    smaller than the source — common for files that were already exported
    efficiently — the original is copied through instead.
    """
    v_crf, img_q, gif_colours, gif_fps = COMPRESS_LEVELS[level]
    ext = src.suffix.lower()
    dst.parent.mkdir(parents=True, exist_ok=True)

    if ext in _IMG_EXT:
        from PIL import Image
        out = dst.with_suffix(ext)
        with Image.open(src) as im:
            extra = {k: im.info[k] for k in ("icc_profile", "exif") if im.info.get(k)}
            if ext in (".jpg", ".jpeg"):
                if im.mode not in ("RGB", "L", "CMYK"):
                    im = im.convert("RGB")
                im.save(out, "JPEG", quality=img_q, optimize=True, progressive=True,
                        subsampling=0 if level == "Light" else 2, **extra)
            elif ext == ".webp":
                im.save(out, "WEBP", quality=img_q, method=6, **extra)
            else:  # PNG — lossless at Light/Medium, palette at High/Maximum
                if level in ("High", "Maximum"):
                    colours = 256 if level == "High" else 128
                    method = (Image.Quantize.FASTOCTREE if im.mode == "RGBA"
                              else Image.Quantize.MEDIANCUT)
                    im = im.convert("RGBA" if "A" in im.mode else "RGB")
                    im = im.quantize(colors=colours, method=method,
                                     dither=Image.Dither.FLOYDSTEINBERG)
                im.save(out, "PNG", optimize=True,
                        **{k: v for k, v in extra.items() if k == "icc_profile"})
    elif ext in _GIF_EXT:
        out = dst.with_suffix(".gif")
        palette = out.with_name(f"_palette_{out.stem}.png")
        _run([_FFMPEG_BIN, "-y", "-i", str(src),
              "-vf", f"fps={gif_fps},palettegen=max_colors={gif_colours}:stats_mode=diff",
              str(palette)])
        _run([_FFMPEG_BIN, "-y", "-i", str(src), "-i", str(palette),
              "-filter_complex", f"fps={gif_fps}[x];[x][1:v]paletteuse=dither=bayer",
              str(out)])
        palette.unlink(missing_ok=True)
    else:
        out = dst.with_suffix(".mp4")
        _run([_FFMPEG_BIN, "-y", "-i", str(src),
              "-c:v", "libx264", "-crf", str(v_crf), "-preset", "medium",
              "-pix_fmt", "yuv420p", "-threads", str(_FFMPEG_THREADS),
              "-an", "-movflags", "+faststart",
              str(out)])

    if out.stat().st_size >= src.stat().st_size:
        out.unlink(missing_ok=True)
        out = dst.with_suffix(src.suffix)
        shutil.copyfile(src, out)
        return out, True
    return out, False


if st.session_state.active_tab == "compress":
    st.subheader("Reduce File Size")
    st.caption("Re-encodes images and videos to smaller files. Resolution is never "
               "changed and nothing is cropped. Folders keep their structure in the ZIP.")

    # Check if output files were passed over from the convert tab
    _queued = st.session_state.get("compress_queued_files", [])
    _queued = [p for p in _queued if Path(p).exists()]  # drop any stale paths

    if _queued:
        st.info(f"📂 {len(_queued)} converted file(s) ready to compress — or upload different files below.")
        if st.button("✕ Clear queued files", type="secondary", key="clear_queued"):
            st.session_state.compress_queued_files = []
            st.rerun()

    _src_mode = st.radio(
        "Upload",
        ["Files", "Folder"],
        horizontal=True,
        key="comp_src_mode",
        help="**Files** — pick any number of files, or ZIP archives of folders.\n\n"
             "**Folder** — pick one folder; every image and video inside it "
             "(including subfolders) is compressed.",
    )
    if _src_mode == "Folder":
        comp_uploaded = st.file_uploader(
            "📁 Choose a folder (JPG, PNG, WEBP, GIF, MP4, MOV)",
            type=sorted(e.lstrip(".") for e in _MEDIA_EXT),
            key=f"compress_dir_{st.session_state.compress_key}",
            accept_multiple_files="directory",
        )
    else:
        comp_uploaded = st.file_uploader(
            "📤 Upload files to compress (JPG, PNG, WEBP, GIF, MP4, MOV, or ZIP)",
            type=sorted(e.lstrip(".") for e in _MEDIA_EXT) + ["zip"],
            key=f"compress_upload_{st.session_state.compress_key}",
            accept_multiple_files=True,
        )
    comp_uploaded = comp_uploaded or []

    if comp_uploaded or _queued:
        comp_level = st.select_slider(
            "Compression level",
            options=list(COMPRESS_LEVELS),
            value="Medium",
            key="comp_level_slider",
            help="Light = best quality / larger file.  Maximum = smallest file. "
                 "Resolution stays the same at every level.",
        )

        # Show file list using metadata only — no f.read() here, runs on every render
        total_orig_mb = 0.0
        file_meta = []   # (display_name, size_mb, is_queued, source)
        for f in comp_uploaded:
            mb = (getattr(f, "size", None) or 0) / 1_000_000
            total_orig_mb += mb
            file_meta.append((f.name, mb, False, f))
        for p in _queued:
            p = Path(p)
            mb = p.stat().st_size / 1_000_000
            total_orig_mb += mb
            file_meta.append((p.name, mb, True, p))

        st.markdown(f"**{len(file_meta)} item(s) — total {total_orig_mb:.1f} MB**")
        with st.expander("Show files", expanded=len(file_meta) <= 8):
            for name, mb, _, _ in file_meta:
                st.caption(f"• {name}  —  {mb:.1f} MB")

        if st.button("🗜️ Compress All", type="primary", key="compress_btn"):
            st.session_state.pop("comp_results", None)

            # Fresh work area per run: earlier runs' files never leak into this ZIP.
            shutil.rmtree(COMPRESS_ROOT, ignore_errors=True)
            _run_dir = COMPRESS_ROOT / _uuid.uuid4().hex[:8]
            _in_dir, _out_dir = _run_dir / "in", _run_dir / "out"
            _in_dir.mkdir(parents=True)
            _out_dir.mkdir(parents=True)

            # Gather (relative path, source file) for every media file.
            jobs: list[tuple[Path, Path]] = []
            _taken: set[Path] = set()

            def _claim(rel: Path) -> Path:
                cand, n = rel, 1
                while cand.with_suffix("") in _taken:
                    n += 1
                    cand = rel.with_name(f"{rel.stem}_{n}{rel.suffix}")
                _taken.add(cand.with_suffix(""))
                return cand

            with st.spinner("Reading files…"):
                for name, mb, is_queued, src in file_meta:
                    try:
                        if is_queued:
                            jobs.append((_claim(Path(src.name)), Path(src)))
                            continue
                        rel = _clean_relpath(src.name)
                        if rel is None:
                            continue
                        if rel.suffix.lower() == ".zip":
                            import io
                            with zipfile.ZipFile(io.BytesIO(src.getvalue())) as zf:
                                for info in zf.infolist():
                                    member = _clean_relpath(info.filename)
                                    if (info.is_dir() or member is None
                                            or member.suffix.lower() not in _MEDIA_EXT):
                                        continue
                                    member = _claim(Path(rel.stem) / member)
                                    dest = _in_dir / member
                                    dest.parent.mkdir(parents=True, exist_ok=True)
                                    with zf.open(info) as zsrc, open(dest, "wb") as zdst:
                                        shutil.copyfileobj(zsrc, zdst)
                                    jobs.append((member, dest))
                        elif rel.suffix.lower() in _MEDIA_EXT:
                            rel = _claim(rel)
                            dest = _in_dir / rel
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(src.getvalue())
                            jobs.append((rel, dest))
                    except Exception as exc:
                        st.error(f"Could not read {name}: {exc}")
                        log.error("Compress intake failed for %s: %s", name, exc)

            if not jobs:
                st.warning("No images or videos found to compress.")
                st.stop()

            # Encode in parallel. Each ffmpeg gets a share of the cores, so a
            # batch of short clips or stills no longer queues behind one file.
            from concurrent.futures import ThreadPoolExecutor, as_completed
            _comp_outputs = []   # (out_path, rel_out, orig_mb, new_mb, kept_original)
            _failures = []
            progress = st.progress(0.0, text=f"Compressing 0 of {len(jobs)}…")
            with ThreadPoolExecutor(max_workers=_COMPRESS_WORKERS) as pool:
                futs = {pool.submit(compress_one, src, _out_dir / rel, comp_level): (rel, src)
                        for rel, src in jobs}
                for done_n, fut in enumerate(as_completed(futs), 1):
                    rel, src = futs[fut]
                    try:
                        out, kept = fut.result()
                        _comp_outputs.append((
                            str(out), str(out.relative_to(_out_dir)),
                            src.stat().st_size / 1_000_000,
                            out.stat().st_size / 1_000_000, kept,
                        ))
                    except Exception as exc:
                        _failures.append(f"{rel}: {str(exc)[-300:]}")
                        log.error("Compress failed for %s: %s", rel, exc)
                    progress.progress(done_n / len(jobs),
                                      text=f"Compressing {done_n} of {len(jobs)}…")
            progress.empty()
            _comp_outputs.sort(key=lambda r: r[1])

            _zip_path_str = None
            if len(_comp_outputs) > 1:
                try:
                    _zp = zip_outputs([Path(r[0]) for r in _comp_outputs], _run_dir,
                                      root=_out_dir,
                                      name=_batch_name([r[1] for r in _comp_outputs]))
                    _zip_path_str = str(_zp)
                except Exception as _exc:
                    log.error("ZIP creation failed: %s", _exc)

            st.session_state.comp_results = {
                "outputs":       _comp_outputs,
                "failures":      _failures,
                "total_orig_mb": sum(r[2] for r in _comp_outputs),
                "total_new_mb":  sum(r[3] for r in _comp_outputs),
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

        if _outputs:
            st.success(f"✅ {len(_outputs)} file(s) compressed — {_t_orig:.1f} MB → "
                       f"{_t_new:.1f} MB ({_pct:.0f}% smaller)")
        _kept = sum(1 for r in _outputs if r[4])
        if _kept:
            st.caption(f"{_kept} file(s) were already smaller than a re-encode would be "
                       "and are included unchanged.")
        if _res.get("failures"):
            with st.expander(f"⚠️ {len(_res['failures'])} file(s) failed"):
                for _f in _res["failures"]:
                    st.error(_f)

        st.markdown("#### 📥 Download")
        st.dataframe(
            [{"File": rel, "Original (MB)": round(o, 2), "Compressed (MB)": round(n, 2),
              "Saved": "unchanged" if kept else f"−{((o - n) / o * 100) if o else 0:.0f}%"}
             for _, rel, o, n, kept in _outputs],
            hide_index=True, use_container_width=True,
            height=min(38 + 35 * len(_outputs), 400),
        )

        _valid = [r for r in _outputs if Path(r[0]).exists()]
        if _outputs and not _valid:
            st.warning("Output files no longer available. Please compress again.")
        elif _valid:
            # ZIP first — for a folder batch it is the only sensible download.
            _zip_path = _res.get("zip_path")
            if _zip_path and Path(_zip_path).exists():
                st.download_button(
                    label=f"📦 Download All as ZIP ({Path(_zip_path).stat().st_size / 1_000_000:.1f} MB)",
                    data=open(_zip_path, "rb"),
                    file_name=Path(_zip_path).name,
                    mime="application/zip",
                    key="comp_dl_zip",
                    type="primary",
                    use_container_width=True,
                )
            # Individual buttons only for small batches; a folder of hundreds
            # of files would bury the page in buttons.
            if len(_valid) <= 6:
                _mimes = {".gif": "image/gif", ".png": "image/png", ".webp": "image/webp",
                          ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".mov": "video/quicktime"}
                _dl_cols = st.columns(min(len(_valid), 3))
                for _vi, (_vp, _rel, _vo, _vn, _vk) in enumerate(_valid):
                    _vp = Path(_vp)
                    with _dl_cols[_vi % 3]:
                        st.download_button(
                            label=f"⬇ {_vp.name}  ({_vn:.1f} MB)",
                            data=open(_vp, "rb"),
                            file_name=_vp.name,
                            mime=_mimes.get(_vp.suffix.lower(), "video/mp4"),
                            key=f"comp_dl_{_vi}",
                            type="secondary" if len(_valid) > 1 else "primary",
                            use_container_width=True,
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
                            "compress_queued_files", "comp_results", "export_results",
                            "source_sig", "source_path", "importance_boxes"]:
                    st.session_state.pop(_k, None)
                st.session_state.upload_key += 1
                st.session_state.compress_key += 1
                st.session_state.active_tab = "convert"
                st.rerun()

    elif not (comp_uploaded or _queued):
        st.info("Upload images or videos — individually, as a folder, or as a ZIP — to get started.")


# ══════════════════════════════════════════════
# TAB 1 — CONVERT
# ══════════════════════════════════════════════

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
    ui.step_header(1, "Select screen templates", done=step1_done)

    # Venue grouping is derived from the template name ("Menlyn Sky", "Monte
    # Casino Piazza"). Tags can't carry this — 40 of the 60 templates are
    # tagged simply "malls" — so the group label is the longest run of leading
    # words every template in the group shares.
    _groups: dict[str, list[str]] = {}
    for _k in all_keys:
        _groups.setdefault(templates[_k].name.split()[0], []).append(_k)

    def _group_label(keys: list[str]) -> str:
        words = [templates[k].name.split() for k in keys]
        shared: list[str] = []
        for i in range(min(len(w) for w in words)):
            if all(w[i] == words[0][i] for w in words):
                shared.append(words[0][i])
            else:
                break
        return (" ".join(shared)).rstrip(" -—·") or templates[keys[0]].name

    _labelled = sorted(
        ((_group_label(ks), ks) for ks in _groups.values()),
        key=lambda pair: (-len(pair[1]), pair[0]),
    )
    _venue_of = {k: lbl for lbl, ks in _labelled for k in ks}

    def _option_label(key: str) -> str:
        t = templates[key]
        detail = (f"{len(t.formats)} formats" if len(t.formats) > 1
                  else t.primary_resolution)
        return f"{t.name}  ·  {detail}"

    # ── Venue, then screens ────────────────────────
    # A grid of 60 cards meant scrolling past every mall to reach one screen.
    # Narrowing by venue first keeps the list short, and the multiselect itself
    # accepts typing, so searching by mall, screen or resolution still works
    # without a separate search field.
    c_venue, c_all = st.columns([3, 1])
    with c_venue:
        _venue_pick = st.selectbox(
            "Venue",
            ["All venues"] + [f"{lbl}  ({len(ks)})" for lbl, ks in _labelled],
            key="tmpl_venue",
            help="Narrow the list to one mall, or leave on All venues to search everything.",
        )
    _venue_name = _venue_pick.split("  (")[0]
    _option_keys = ([k for lbl, ks in _labelled if lbl == _venue_name for k in ks]
                    if _venue_pick != "All venues" else all_keys)

    with c_all:
        st.markdown('<div style="height:28px"></div>', unsafe_allow_html=True)
        if _venue_pick != "All venues":
            if st.button(f"Add all {len(_option_keys)}", use_container_width=True,
                         key="add_all_venue"):
                for k in _option_keys:
                    if k not in st.session_state.selected_keys:
                        st.session_state.selected_keys.append(k)
                st.rerun()

    # Selections made under a different venue filter must survive the switch,
    # so they are held aside and merged back after the widget reports.
    _held = [k for k in st.session_state.selected_keys if k not in _option_keys]
    _picked = st.multiselect(
        "Screens",
        options=_option_keys,
        default=[k for k in st.session_state.selected_keys if k in _option_keys],
        format_func=_option_label,
        placeholder="Type a mall, screen or resolution…",
        label_visibility="collapsed",
    )
    st.session_state.selected_keys = _held + _picked

    # ── Selection summary ──────────────────────────
    if st.session_state.selected_keys:
        _total_outputs = sum(len(templates[k].formats)
                             for k in st.session_state.selected_keys)
        c1, c2 = st.columns([6, 1])
        with c1:
            st.markdown(
                ui.badge(f"{len(st.session_state.selected_keys)} screens", "info")
                + "&nbsp;&nbsp;"
                + ui.badge(f"{_total_outputs} files will export", "neutral"),
                unsafe_allow_html=True,
            )
        with c2:
            if st.button("Clear", type="secondary", key="clear_all_tmpl",
                         use_container_width=True):
                st.session_state.selected_keys = []
                st.rerun()
    else:
        st.caption(f"{len(all_keys)} templates across {len(_labelled)} venues. "
                   "Pick a venue, or type to search all of them.")

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
    ui.step_header(2, "Upload your master file")

    uploaded = st.file_uploader(
        "Upload master file",
        type=["mp4", "mov", "jpg", "jpeg", "png"],
        label_visibility="collapsed",
        key=f"master_upload_{st.session_state.upload_key}",
    )

    if not uploaded:
        st.info("Upload an MP4, MOV, JPG or PNG file to continue.")
        st.stop()

    # Streamlit re-runs this whole script on every widget interaction. Without a
    # guard, each click re-wrote the upload to disk, re-encoded stills to video,
    # re-probed metadata and re-ran crop analysis — several seconds of ffmpeg per
    # click, and the cause of the sluggishness and the download races. Everything
    # below is therefore keyed on a hash of the file's contents.
    _payload = uploaded.getvalue()
    _sig = hashlib.sha256(_payload).hexdigest()[:16] + f"-{slot_duration}"

    file_ext   = Path(uploaded.name).suffix.lower()
    input_path = INPUT_DIR / uploaded.name

    if st.session_state.get("source_sig") != _sig:
        # A new master invalidates everything derived from the old one. The
        # preview thumbnail was only generated "if not exists", and the previous
        # export results were re-shown until Export was clicked again — so a
        # fresh upload kept showing (and offering for download) the last file.
        for _dir in (INPUT_DIR, PREVIEW_DIR):
            for _old in _dir.iterdir():
                try:
                    _old.unlink()
                except Exception:
                    pass
        st.session_state.pop("export_results", None)
        input_path.write_bytes(_payload)

        # ── Image → Video conversion ───────────────
        if file_ext in (".jpg", ".jpeg", ".png"):
            video_path = INPUT_DIR / "converted_video.mp4"
            with st.spinner(f"Converting still image to a {slot_duration}s video…"):
                try:
                    image_to_video(input_path, video_path, slot_duration)
                    input_path = video_path
                except RuntimeError as exc:
                    st.error(f"Conversion failed: {exc}")
                    st.stop()

        st.session_state.source_sig  = _sig
        st.session_state.source_path = str(input_path)
        st.session_state.pop("importance_boxes", None)
    else:
        input_path = Path(st.session_state.source_path)
        if not input_path.exists():
            # Session scratch was swept — force a clean re-ingest next run
            st.session_state.pop("source_sig", None)
            st.rerun()

    st.success(f"Uploaded **{uploaded.name}**")
    if file_ext in (".jpg", ".jpeg", ".png"):
        st.caption(f"Still image — converted to a {slot_duration}s video for export.")

    # ── Probe & preview ────────────────────────────
    try:
        video_w, video_h, duration = probe_video(input_path)
    except RuntimeError as exc:
        st.error(f"Could not read video metadata: {exc}")
        st.stop()

    thumb_path = PREVIEW_DIR / "thumb.jpg"
    if not thumb_path.exists():
        generate_thumbnail(input_path, thumb_path)

    # ── Smart crop detection ────────────────────────
    importance_boxes: list[tuple[int, int, int, int]] = []
    if use_smart_crop:
        if "importance_boxes" in st.session_state:
            importance_boxes = st.session_state.importance_boxes
        else:
            with st.spinner("Analysing content to keep key elements in frame…"):
                try:
                    from smart_crop import extract_keyframes, detect_importance_regions, draw_detections
                    frames = extract_keyframes(input_path)
                    importance_boxes = detect_importance_regions(frames)
                    if importance_boxes and thumb_path.exists():
                        overlay_path = PREVIEW_DIR / "thumb_overlay.jpg"
                        draw_detections(thumb_path, importance_boxes, overlay_path)
                    st.session_state.importance_boxes = importance_boxes
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
            _out_secs = min(duration, slot_duration)
            st.markdown(
                f"- Duration: **{duration:.1f}s** → exports at **{_out_secs:.1f}s**"
            )
        st.markdown(f"- Templates selected: **{len(selected_templates)}**")
        st.markdown(f"- Total outputs: **{total_formats}**")

        # Say plainly what the duration cap will do to this specific file,
        # before the user spends time on an export.
        if duration:
            if duration > slot_duration + 0.05:
                st.warning(
                    f"This clip is {duration:.1f}s and will be **trimmed to the "
                    f"first {slot_duration}s**. Change the output duration in the "
                    "sidebar if that is wrong."
                )
            elif duration < slot_duration - 0.05:
                st.info(
                    f"This clip is {duration:.1f}s — shorter than the "
                    f"{slot_duration}s slot. It exports at its own length; "
                    "it is not padded or looped."
                )
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

    # Each job gets its output file name up front. Names were previously built
    # as "<label>_<WxH>.mp4" at export time, and ten of them collide across
    # templates (e.g. "Boardwalk_600x720" from both Boardwalk and Portrait
    # Screens). The later export overwrote the earlier one and the ZIP held the
    # same entry twice. Identical name + size is the identical file, so those
    # are exported once.
    all_export_jobs: list[tuple[Template, FormatPlan, str]] = []
    _seen_names: set[str] = set()
    _source_stem = _keep_name(Path(uploaded.name).stem)
    for tmpl in effective_templates:
        for plan in plan_exports(tmpl, video_w, video_h,
                                 importance_boxes=importance_boxes or None):
            # Outputs keep the master's own name, then the screen and size:
            # "Summer Sale_Boardwalk_600x720.mp4".
            _stem = _safe_name(plan.label or tmpl.name)
            _fname = f"{_source_stem}_{_stem}_{plan.width}x{plan.height}.mp4"
            if _fname in _seen_names:
                continue
            _seen_names.add(_fname)
            all_export_jobs.append((tmpl, plan, _fname))

    # Results are only valid for the exact source + jobs + settings that made
    # them; any change hides the stale results until the next export.
    _export_sig = hashlib.sha256(json.dumps([
        st.session_state.get("source_sig"),
        [(f, p.width, p.height, p.crop_x, p.crop_y) for _, p, f in all_export_jobs],
        crf, preset, resize_mode_key, slot_duration,
    ]).encode()).hexdigest()[:16]

    # ════════════════════════════════════
    # STEP 3 — Export
    # ════════════════════════════════════
    st.markdown("---")
    ui.step_header(3, "Export all formats")
    st.caption(
        f"{len(all_export_jobs)} output(s) will be generated across "
        f"{len(effective_templates)} template(s)."
    )

    _export_clicked = st.button("🚀 Export Now", type="primary", use_container_width=True)

    # st.button() is only True on the rerun immediately after its own click, and
    # clicking a download button also triggers a rerun. Gating the results
    # section on the button therefore tore down the download widgets mid-
    # download — the browser's request for the file arrived after the widget
    # (and its registered data) was gone. Keep the results in session_state so
    # they survive those reruns.
    if _export_clicked:
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

        for i, (tmpl, plan, fname) in enumerate(all_export_jobs):
            out = OUTPUT_DIR / fname
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
                    max_duration=slot_duration,
                    scaled_w=plan.scaled_w,
                    scaled_h=plan.scaled_h,
                )
                output_files.append(out)
                log.info("Exported %s", out.name)
            except RuntimeError as exc:
                err_msg = f"{tmpl.name} {plan.width}×{plan.height}: {exc}"
                errors.append(err_msg)
                log.error(err_msg)

            progress_bar.progress((i + 1) / total)

        status_text.empty()

        st.session_state.export_results = {
            "files":  [str(p) for p in output_files],
            "errors": errors,
            "total":  total,
            "zip":    None,
            "sig":    _export_sig,
        }
    else:
        _prev = st.session_state.get("export_results")
        if not _prev or _prev.get("sig") != _export_sig:
            st.stop()
        output_files = [Path(p) for p in _prev["files"] if Path(p).exists()]
        errors       = list(_prev["errors"])
        total        = _prev["total"]
        if not output_files:
            st.stop()

    # ── Results ────────────────────────────────────
    st.markdown("---")

    if output_files:
        st.success(f"✅ {len(output_files)} of {total} format(s) exported successfully.")

        # Thumbnail previews
        thumb_cols = st.columns(min(len(output_files), 4))
        for idx, out_file in enumerate(output_files):
            thumb = PREVIEW_DIR / f"thumb_{out_file.stem}.jpg"
            if not thumb.exists():
                generate_thumbnail(out_file, thumb)
            res = out_file.stem.rsplit("_", 1)[-1].replace("x", "×") if "_" in out_file.stem else out_file.stem
            file_size = out_file.stat().st_size / 1_000_000
            with thumb_cols[idx % 4]:
                if thumb.exists():
                    st.image(str(thumb), caption=f"{res} — {file_size:.1f} MB", use_container_width=True)
                else:
                    st.markdown(f"**{res}** — {file_size:.1f} MB")

        # Download buttons — one per output file, then a ZIP for multi-file
        st.markdown("#### 📥 Download")
        dl_cols = st.columns(min(len(output_files), 4))
        for idx, out_file in enumerate(output_files):
            _res_label = out_file.stem.rsplit("_", 1)[-1].replace("x", "×") if "_" in out_file.stem else out_file.stem
            _sz = out_file.stat().st_size / 1_000_000
            with dl_cols[idx % 4]:
                st.download_button(
                    label=f"⬇ {_res_label} ({_sz:.1f} MB)",
                    data=out_file.read_bytes(),
                    file_name=out_file.name,
                    mime="video/mp4",
                    key=f"dl_{idx}",
                    use_container_width=True,
                )
        if len(output_files) > 1:
            # Build the ZIP once per export. zip_outputs() names the file with a
            # timestamp, so rebuilding it on every rerun moved the target out
            # from under the download token. Mirrors the compress tab's fix.
            _res = st.session_state.get("export_results") or {}
            _cached_zip = _res.get("zip")
            if _cached_zip and Path(_cached_zip).exists():
                zip_path = Path(_cached_zip)
            else:
                zip_path = zip_outputs(output_files, OUTPUT_DIR, name=_source_stem)
                _res["zip"] = str(zip_path)
                st.session_state.export_results = _res
            st.download_button(
                label="📦 Download All as ZIP",
                data=zip_path.read_bytes(),
                file_name=zip_path.name,
                mime="application/zip",
                use_container_width=True,
                key="dl_zip",
            )

        # ── Post-download actions ──────────────────────────────────────────
        st.markdown("---")
        st.markdown("#### What would you like to do next?")
        _na, _nb, _nc = st.columns(3)
        with _na:
            if st.button("🔄 Convert Another File", type="secondary",
                         use_container_width=True, key="next_convert_another"):
                for _k in ["export_results", "source_sig", "source_path",
                           "importance_boxes"]:
                    st.session_state.pop(_k, None)
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
                            "compress_queued_files", "export_results",
                            "source_sig", "source_path", "importance_boxes"]:
                    st.session_state.pop(_k, None)
                st.session_state.upload_key += 1
                st.session_state.compress_key += 1
                st.rerun()

    if errors:
        with st.expander(f"⚠️ {len(errors)} format(s) failed — click to see details"):
            for e in errors:
                st.error(e)
