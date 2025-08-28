# -*- coding: utf-8 -*-
"""
App main — cleaned & organized
- Admin auth unified (session/header/?key) + key stripping
- Admin API decorator (always JSON)
- Unified short-link system (single DB + single route)
- Uploads (pdf/mp3/image) -> long_url + short_url auto
- Dashboard analytics, QR generation
"""

from __future__ import annotations
from functools import wraps
from io import BytesIO
from pathlib import Path
from threading import Lock
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import os
import secrets
import time

import numpy as np
from PIL import Image, ImageDraw, ImageColor
from flask import (
    Flask, render_template, request, send_file, jsonify,
    url_for, redirect, abort, session
)
from qrcode import QRCode, constants
from qrcode.image.svg import SvgPathImage
from werkzeug.utils import secure_filename, safe_join

# ------------------------------------------------------------------------------
# App & Config
# ------------------------------------------------------------------------------

app = Flask(__name__)

# security / env
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
ADMIN_KEY = os.getenv("ADMIN_KEY", "changeme")  # ตั้งใน ENV ในโปรดักชัน
app.config.setdefault("PREFERRED_URL_SCHEME", "https")

# uploads/global limits
GLOBAL_MAX_UPLOAD_MB = int(os.getenv("GLOBAL_MAX_UPLOAD_MB", "64"))
app.config["MAX_CONTENT_LENGTH"] = GLOBAL_MAX_UPLOAD_MB * 1024 * 1024

# static dirs
UPLOAD_FOLDER = os.path.join("static", "logo")
REQUIRED_DIRS = [
    UPLOAD_FOLDER,
    os.path.join("static", "files"),
    os.path.join("static", "files", "pdf"),
    os.path.join("static", "files", "mp3"),
    os.path.join("static", "files", "image"),
    os.path.join("static"),
]
for d in REQUIRED_DIRS:
    os.makedirs(d, exist_ok=True)

# ------------------------------------------------------------------------------
# Helpers: Admin auth (HTML & API)
# ------------------------------------------------------------------------------

def _strip_key_redirect():
    """ถ้ามี ?key= ใน URL ให้ redirect ออก โดยคงพารามิเตอร์อื่นๆ ไว้"""
    if "key" in request.args:
        args = request.args.to_dict(flat=True)
        args.pop("key", None)
        clean = url_for(request.endpoint, **(request.view_args or {}), **args)
        return redirect(clean, code=302)
    return None


def _ensure_admin():
    """ยืนยันสิทธิ์แอดมิน: header X-Admin-Key, query ?key= หรือ session"""
    # header -> จำใน session
    hdr = request.headers.get("X-Admin-Key")
    if hdr and hmac.compare_digest(str(hdr), str(ADMIN_KEY)):
        session["is_admin"] = True

    # query -> จำใน session และ strip ออก
    qk = request.args.get("key")
    if qk and hmac.compare_digest(str(qk), str(ADMIN_KEY)):
        session["is_admin"] = True
        return _strip_key_redirect()

    # session ผ่านอยู่แล้ว
    if session.get("is_admin"):
        return _strip_key_redirect()

    abort(403, description="Forbidden")

def is_admin_logged_in() -> bool:
    """ใช้กับ API (ไม่ redirect)"""
    if session.get("is_admin") is True:
        return True
    hdr = request.headers.get("X-Admin-Key")
    return bool(hdr and hmac.compare_digest(str(hdr), str(ADMIN_KEY)))

def admin_required(fn):
    """ใช้กับหน้า HTML (อาจ redirect ตัด key)"""
    @wraps(fn)
    def _wrap(*args, **kwargs):
        maybe = _ensure_admin()
        if maybe:
            return maybe
        return fn(*args, **kwargs)
    return _wrap

def admin_api_required(fn):
    """ใช้กับ API — ถ้าไม่ผ่านให้ตอบ JSON 401"""
    @wraps(fn)
    def _wrap(*args, **kwargs):
        if not is_admin_logged_in():
            return jsonify(success=False, error="unauthorized"), 401
        return fn(*args, **kwargs)
    return _wrap

# ------------------------------------------------------------------------------
# Analytics (visits/unique/downloads/uploads) — Asia/Bangkok
# ------------------------------------------------------------------------------

try:
    from zoneinfo import ZoneInfo
    BKK_TZ = ZoneInfo("Asia/Bangkok")
except Exception:
    BKK_TZ = timezone(timedelta(hours=7))

ANALYTICS_DB_PATH = os.path.join("static", "analytics.json")
_ANALYTICS_LOCK = Lock()
ANALYTICS_SALT = os.getenv("ANALYTICS_SALT", "change_me_salt")

ALLOWED_DAYS = (7, 14, 30, 60)

def _today_key(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(BKK_TZ)
    return dt.strftime("%Y-%m-%d")

def _hash_ip(ip: str) -> str:
    h = hashlib.sha256()
    h.update((ip + "|" + ANALYTICS_SALT).encode())
    return h.hexdigest()[:24]

def _read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _write_json(path: str, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)

def track_visit(ip: str):
    key = _today_key()
    with _ANALYTICS_LOCK:
        db = _read_json(ANALYTICS_DB_PATH, {})
        day = db.setdefault(key, {"visits": 0, "unique": [], "downloads": 0, "uploads": 0})
        day["visits"] += 1
        h = _hash_ip(ip or "unknown")
        if h not in day["unique"]:
            day["unique"].append(h)
        _write_json(ANALYTICS_DB_PATH, db)

def track_download():
    key = _today_key()
    with _ANALYTICS_LOCK:
        db = _read_json(ANALYTICS_DB_PATH, {})
        day = db.setdefault(key, {"visits": 0, "unique": [], "downloads": 0, "uploads": 0})
        day["downloads"] += 1
        _write_json(ANALYTICS_DB_PATH, db)

def track_upload():
    key = _today_key()
    with _ANALYTICS_LOCK:
        db = _read_json(ANALYTICS_DB_PATH, {})
        day = db.setdefault(key, {"visits": 0, "unique": [], "downloads": 0, "uploads": 0})
        day["uploads"] += 1
        _write_json(ANALYTICS_DB_PATH, db)

def analytics_series(days: int = 30):
    db = _read_json(ANALYTICS_DB_PATH, {})
    labels, visits, uniques, downloads, uploads = [], [], [], [], []
    today = datetime.now(BKK_TZ).date()
    for i in range(days - 1, -1, -1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        row = db.get(d, {"visits": 0, "unique": [], "downloads": 0, "uploads": 0})
        labels.append(d)
        visits.append(row.get("visits", 0))
        uniques.append(len(row.get("unique", [])))
        downloads.append(row.get("downloads", 0))
        uploads.append(row.get("uploads", 0))
    return {
        "labels": labels,
        "visits": visits,
        "uniques": uniques,
        "downloads": downloads,
        "uploads": uploads,
    }

def analytics_totals(days: int = 30):
    s = analytics_series(days)
    return {
        "days": days,
        "visits": sum(s["visits"]),
        "uniques": sum(s["uniques"]),
        "downloads": sum(s["downloads"]),
        "uploads": sum(s["uploads"]),
        "today": {
            "visits": s["visits"][-1],
            "uniques": s["uniques"][-1],
            "downloads": s["downloads"][-1],
            "uploads": s["uploads"][-1],
        },
    }

# ------------------------------------------------------------------------------
# QR Code utilities (PNG/Gradient/Logo + SVG)
# ------------------------------------------------------------------------------

ECC_MAP = {
    "L": constants.ERROR_CORRECT_L,
    "M": constants.ERROR_CORRECT_M,
    "Q": constants.ERROR_CORRECT_Q,
    "H": constants.ERROR_CORRECT_H,
}

def parse_ecc(val: str):
    return ECC_MAP.get((val or "H").upper(), constants.ERROR_CORRECT_H)

def trim_transparent(img: Image.Image) -> Image.Image:
    bbox = img.getbbox()
    return img.crop(bbox) if bbox else img

def resize_logo_keep_ratio_with_padding(logo_path: str, box_size: int, pad_ratio: float = 0.1):
    logo = Image.open(logo_path).convert("RGBA")
    logo = trim_transparent(logo)
    w, h = logo.size
    pad = int(box_size * pad_ratio)
    max_logo_size = box_size - 2 * pad
    if w > h:
        new_w = max_logo_size
        new_h = int(max_logo_size * h / w)
    else:
        new_h = max_logo_size
        new_w = int(max_logo_size * w / h)
    logo_resized = logo.resize((new_w, new_h), Image.LANCZOS)
    logo_square = Image.new("RGBA", (box_size, box_size), (0, 0, 0, 0))
    paste_x = (box_size - new_w) // 2
    paste_y = (box_size - new_h) // 2
    logo_square.paste(logo_resized, (paste_x, paste_y), mask=logo_resized)
    return logo_square

def _linear_gradient(size, c1, c2):
    w, h = size
    x = np.linspace(0.0, 1.0, w, dtype=np.float32)
    y = np.linspace(0.0, 1.0, h, dtype=np.float32)
    t = (x + y[:, None]) * 0.5
    c1 = np.array(c1, dtype=np.float32)
    c2 = np.array(c2, dtype=np.float32)
    rgb = (c1 + (c2 - c1) * t[..., None]).clip(0, 255).astype(np.uint8)
    a = np.full((h, w, 1), 255, dtype=np.uint8)
    return Image.fromarray(np.concatenate([rgb, a], axis=2), "RGBA")

def _radial_gradient(size, c1, c2):
    w, h = size
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    yy, xx = np.ogrid[0:h, 0:w]
    r = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    r = (r / r.max()).astype(np.float32)
    c1 = np.array(c1, dtype=np.float32)
    c2 = np.array(c2, dtype=np.float32)
    rgb = (c1 + (c2 - c1) * r[..., None]).clip(0, 255).astype(np.uint8)
    a = np.full((h, w, 1), 255, dtype=np.uint8)
    return Image.fromarray(np.concatenate([rgb, a], axis=2), "RGBA")

def generate_qr_code_png(
    data, logo_path=None, fill_color="#000", back_color="#fff", transparent=False,
    size_px: int | None = None, ecc="H", fill_style="solid", fill_color2="#000000"
) -> Image.Image:
    qr = QRCode(version=5, error_correction=parse_ecc(ecc), box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)

    if size_px:
        modules = qr.modules_count + qr.border * 2
        qr.box_size = max(1, math.ceil(size_px / modules))

    mask_gray = qr.make_image(fill_color="#000", back_color="#fff").convert("L")
    w, h = mask_gray.size
    mask = mask_gray.point(lambda p: 255 - p)  # invert

    base = Image.new("RGBA", (w, h), (0, 0, 0, 0) if transparent else (*ImageColor.getrgb(back_color), 255))

    c1 = ImageColor.getrgb(fill_color)
    if fill_style == "linear":
        c2 = ImageColor.getrgb(fill_color2 or fill_color)
        color_img = _linear_gradient((w, h), c1, c2)
    elif fill_style == "radial":
        c2 = ImageColor.getrgb(fill_color2 or fill_color)
        color_img = _radial_gradient((w, h), c1, c2)
    else:
        color_img = Image.new("RGBA", (w, h), (*c1, 255))

    base.paste(color_img, (0, 0), mask)

    if logo_path and os.path.exists(logo_path):
        logo_size = w // 4
        logo = resize_logo_keep_ratio_with_padding(logo_path, logo_size, pad_ratio=0.13)
        x = (w - logo_size) // 2
        y = (h - logo_size) // 2
        box_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(box_layer).rectangle([x, y, x + logo_size, y + logo_size],
                                            fill=(255, 255, 255, 255) if not transparent else (255, 255, 255, 0))
        base = Image.alpha_composite(base, box_layer)
        base.paste(logo, (x, y), mask=logo)

    if size_px and (base.width != size_px or base.height != size_px):
        base = base.resize((size_px, size_px), Image.NEAREST)

    return base

def generate_qr_code_svg(data, fill_color="#000", back_color="#fff", transparent=False, ecc="H") -> bytes:
    qr = QRCode(version=5, error_correction=parse_ecc(ecc), box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    bg = None if transparent else back_color
    img = qr.make_image(image_factory=SvgPathImage, fill_color=fill_color, back_color=bg)
    return img.to_string()

# ------------------------------------------------------------------------------
# Routes: Home / QR / Uploads
# ------------------------------------------------------------------------------

def _human_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    s = float(n)
    for u in units:
        if s < 1024 or u == units[-1]:
            return f"{int(s)} {u}" if u == "B" else f"{s:.2f} {u}"
        s /= 1024.0

@app.route("/", methods=["GET", "POST"])
def index():
    track_visit(request.headers.get("X-Forwarded-For", request.remote_addr))

    logos = [f for f in os.listdir(UPLOAD_FOLDER) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
    if request.method == "POST":
        data = request.form.get("data", "")
        fill_color = request.form.get("fill_color", "#000")
        back_color = request.form.get("back_color", "#fff")
        transparent = bool(request.form.get("transparent"))
        fill_style = (request.form.get("fill_style") or "solid").lower()
        fill_color2 = request.form.get("fill_color2", "#000000")
        out_format = (request.form.get("out_format") or "png").lower()
        try:
            size_px = int(request.form.get("size_px") or "1024")
        except Exception:
            size_px = 1024

        logo_name = request.form.get("logo")
        logo_path = os.path.join(UPLOAD_FOLDER, logo_name) if logo_name else None
        ecc = (request.form.get("ecc") or "H").upper()

        if out_format == "svg":
            if logo_path:
                return "SVG download does not support logo in this version.", 400
            svg_bytes = generate_qr_code_svg(data, fill_color, back_color, transparent, ecc)
            buf = BytesIO(svg_bytes)
            buf.seek(0)
            track_download()
            return send_file(buf, mimetype="image/svg+xml",
                             as_attachment=True, download_name="qr_code.svg")

        img = generate_qr_code_png(
            data, logo_path, fill_color, back_color, transparent,
            size_px=size_px, ecc=ecc, fill_style=fill_style, fill_color2=fill_color2
        )
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        track_download()
        return send_file(buf, mimetype="image/png",
                         as_attachment=True, download_name="qr_code.png")

    return render_template("index.html", logos=logos)

# ---- asset upload (pdf/mp3/image) ----
ASSET_FOLDERS = {
    "pdf":   os.path.join("static", "files", "pdf"),
    "mp3":   os.path.join("static", "files", "mp3"),
    "image": os.path.join("static", "files", "image"),
}
ASSET_EXTS = {
    "pdf": {"pdf"},
    "mp3": {"mp3"},
    "image": {"png", "jpg", "jpeg"},
}
ASSET_MAX_MB = {"pdf": 10, "mp3": 15, "image": 5}
for d in ASSET_FOLDERS.values():
    os.makedirs(d, exist_ok=True)

@app.post("/upload_asset/<atype>")
def upload_asset(atype):
    atype = (atype or "").lower()
    if atype not in ASSET_FOLDERS:
        return jsonify(error="unsupported asset type"), 400

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="no file"), 400

    orig_name = f.filename
    orig_ext = os.path.splitext(orig_name)[1].lower().lstrip(".")
    if not orig_ext:
        mime = (f.mimetype or "").lower()
        mime_map = {
            "application/pdf": "pdf",
            "audio/mpeg": "mp3",
            "image/jpeg": "jpg",
            "image/png": "png",
        }
        orig_ext = mime_map.get(mime, "")

    if orig_ext not in ASSET_EXTS.get(atype, set()):
        return jsonify(error="invalid extension"), 400

    base_stem = secure_filename(os.path.splitext(orig_name)[0]) or "file"

    # size check
    pos = f.stream.tell()
    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(pos)
    if size > ASSET_MAX_MB[atype] * 1024 * 1024:
        return jsonify(error=f"file too large (>{ASSET_MAX_MB[atype]} MB)"), 400

    final_name = f"{base_stem}_{int(time.time())}.{orig_ext}"
    save_path = os.path.join(ASSET_FOLDERS[atype], final_name)
    f.save(save_path)

    long_url = url_for("static", filename=f"files/{atype}/{final_name}", _external=True)
    short_url = get_or_create_short(long_url)
    track_upload()
    return jsonify(success=True, url=long_url, short_url=short_url,
                   filename=final_name, size=size)

@app.route("/upload_logo", methods=["POST"])
def upload_logo():
    file = request.files.get("logo")
    if not file or file.filename == "":
        return "No file selected", 400
    if os.path.splitext(file.filename)[1].lower() not in (".png", ".jpg", ".jpeg"):
        return "Invalid file type (png/jpg/jpeg)", 400

    fname = secure_filename(file.filename)
    save_path = os.path.join(UPLOAD_FOLDER, fname)
    try:
        img = Image.open(file.stream); img.verify()
        if (img.format or "").upper() not in {"PNG", "JPEG"}:
            return "Invalid image format (PNG/JPEG only)", 400
        file.stream.seek(0)
    except Exception:
        return "Corrupted or unsupported image file", 400

    file.save(save_path)
    return "OK"

@app.route("/preview_qr", methods=["POST"])
def preview_qr():
    data = request.form.get("data", "")
    fill_color = request.form.get("fill_color", "#000")
    back_color = request.form.get("back_color", "#fff")
    transparent = bool(request.form.get("transparent"))
    ecc = (request.form.get("ecc") or "H").upper()
    fill_style = (request.form.get("fill_style") or "solid").lower()
    fill_color2 = request.form.get("fill_color2", "#000000")

    try:
        size_px = int(request.form.get("size_px") or "512")
    except Exception:
        size_px = 512

    logo_name = secure_filename(request.form.get("logo") or "")
    logo_path = os.path.join(UPLOAD_FOLDER, logo_name) if logo_name else None
    if logo_path and not os.path.exists(logo_path):
        logo_path = None

    img = generate_qr_code_png(
        data, logo_path, fill_color, back_color, transparent,
        size_px=size_px, ecc=ecc, fill_style=fill_style, fill_color2=fill_color2
    )
    buf = BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
    return send_file(buf, mimetype="image/png")

# ------------------------------------------------------------------------------
# Short-links (UNIFIED)
# ------------------------------------------------------------------------------

SHORT_DB_PATH = os.path.join("static", "shortlinks.json")
_SHORT_DB_LOCK = Lock()
os.makedirs(os.path.dirname(SHORT_DB_PATH), exist_ok=True)

def _load_short_db() -> dict:
    return _read_json(SHORT_DB_PATH, {})

def _save_short_db(db: dict) -> None:
    _write_json(SHORT_DB_PATH, db)

def _find_code_by_url(db: dict, url: str):
    for c, item in db.items():
        if (item.get("url") if isinstance(item, dict) else item) == url:
            return c
    return None

def _gen_code(n=6) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(n))

def get_or_create_short(url: str) -> str:
    """คืนลิงก์สั้นเต็ม (เช่น http://host/s/Ab12C) สร้างใหม่ถ้ายังไม่มี"""
    with _SHORT_DB_LOCK:
        db = _load_short_db()
        code = _find_code_by_url(db, url)
        if not code:
            code = _gen_code(6)
            while code in db:
                code = _gen_code(6)
            db[code] = {"url": url, "ts": int(time.time())}
            _save_short_db(db)
    return url_for("short_redirect", code=code, _external=True)

@app.get("/s/<code>")
def short_redirect(code: str):
    db = _load_short_db()
    item = db.get(code)
    if not item:
        abort(404)
    long_url = item.get("url") if isinstance(item, dict) else item
    return redirect(long_url, code=302)

# ------------------------------------------------------------------------------
# Admin pages & APIs
# ------------------------------------------------------------------------------

def _admin_nav_urls():
    if session.get("is_admin"):
        return {
            "dash_url": url_for("admin_dashboard"),
            "files_url": url_for("admin_index"),
        }
    return {
        "dash_url": url_for("admin_dashboard", key=ADMIN_KEY),
        "files_url": url_for("admin_index", key=ADMIN_KEY),
    }


def _list_assets():
    """รวบรวมรายการไฟล์ (logo + pdf/mp3/image)"""
    rows = []

    # logo
    for name in sorted(os.listdir(UPLOAD_FOLDER)):
        p = os.path.join(UPLOAD_FOLDER, name)
        if not os.path.isfile(p):
            continue
        st = os.stat(p)
        rows.append({
            "kind": "logo",
            "atype": None,
            "name": name,
            "size": st.st_size,
            "mtime": st.st_mtime,
            "mtime_iso": datetime.fromtimestamp(st.st_mtime).isoformat(sep=" ", timespec="seconds"),
            "url": url_for("static", filename=f"logo/{name}", _external=True),
            "short_url": None,
            "thumb": url_for("static", filename=f"logo/{name}"),
        })

    # assets
    for atype, folder in ASSET_FOLDERS.items():
        for name in sorted(os.listdir(folder)):
            p = os.path.join(folder, name)
            if not os.path.isfile(p):
                continue
            st = os.stat(p)
            long_url = url_for("static", filename=f"files/{atype}/{name}", _external=True)
            rows.append({
                "kind": "asset",
                "atype": atype,
                "name": name,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "mtime_iso": datetime.fromtimestamp(st.st_mtime).isoformat(sep=" ", timespec="seconds"),
                "url": long_url,
                "short_url": None,
                "thumb": url_for("static", filename=f"files/{atype}/{name}") if atype == "image" else None,
            })

    # เติม short_url
    db = _load_short_db()
    url2short = {}
    for code, item in db.items():
        u = item.get("url") if isinstance(item, dict) else item
        url2short[u] = url_for("short_redirect", code=code, _external=True)
    for r in rows:
        r["short_url"] = url2short.get(r["url"])

    # ใหม่สุดอยู่บน
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows

@app.get("/admin")
@admin_required
def admin_index():
    rows = _list_assets()
    total_size = sum(r["size"] for r in rows)
    totals = {
        "all": len(rows),
        "logo": sum(1 for r in rows if r["kind"] == "logo"),
        "pdf": sum(1 for r in rows if r["atype"] == "pdf"),
        "mp3": sum(1 for r in rows if r["atype"] == "mp3"),
        "image": sum(1 for r in rows if r["atype"] == "image"),
        "size": _human_bytes(total_size),
        "size_bytes": total_size,
    }
    return render_template("admin.html", rows=rows, totals=totals, **_admin_nav_urls())

@app.get("/admin/dashboard")
@admin_required
def admin_dashboard():
    """
    หน้า Dashboard — รองรับ ?days=(7|14|30|60) และจำไว้ใน session
    """
    raw = request.args.get("days", session.get("dash_days", 30))
    try:
        days = int(raw)
    except (TypeError, ValueError):
        days = 30
    if days not in ALLOWED_DAYS:
        days = 30

    session["dash_days"] = days  # remember

    return render_template(
        "admin_dashboard.html",
        days=days,
        series=analytics_series(days),
        totals=analytics_totals(days),
        **_admin_nav_urls(),
    )

@app.post("/admin/delete")
@admin_required
def admin_delete():
    data = request.get_json(silent=True) or request.form or {}
    atype = (data.get("atype") or "").lower()
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify(success=False, error="invalid params"), 400

    if atype == "" and name:  # logo
        folder = UPLOAD_FOLDER
    else:
        if atype not in ASSET_FOLDERS:
            return jsonify(success=False, error="invalid type"), 400
        folder = ASSET_FOLDERS[atype]

    # safe path
    fname = os.path.basename(name)
    fpath = safe_join(folder, fname)
    if not fpath or not os.path.isfile(fpath):
        return jsonify(success=False, error="not found"), 404

    try:
        os.remove(fpath)
    except Exception as e:
        return jsonify(success=False, error=f"delete failed: {e}"), 500

    return jsonify(success=True)

@app.post("/admin/shorten")
@admin_api_required
def admin_shorten():
    """
    สร้างลิงก์สั้นจาก long_url ที่ส่งมา (JSON/form: {url})
    - ถ้ามีอยู่แล้ว คืนอันเดิม (already=True)
    """
    data = request.get_json(silent=True) or request.form or {}
    long_url = (data.get("url") or "").strip()
    if not long_url:
        return jsonify(success=False, error="missing url"), 400

    short = get_or_create_short(long_url)

    # ตรวจว่าเพิ่งสร้างหรือมีอยู่แล้ว
    db = _load_short_db()
    code = None
    for c, item in db.items():
        u = item.get("url") if isinstance(item, dict) else item
        if u == long_url:
            code = c
            break
    already = code is not None

    return jsonify(success=True, short_url=short, already=already), (200 if already else 201)

# ------------------------------------------------------------------------------
# Error pages
# ------------------------------------------------------------------------------

@app.errorhandler(403)
def h403(e):  # type: ignore
    return render_template("error.html", code=403, message=str(e)), 403

@app.errorhandler(413)
def too_large(e):  # global hard limit
    return jsonify(success=False, error=f"ไฟล์ใหญ่เกินเพดานรวม {GLOBAL_MAX_UPLOAD_MB} MB"), 413

@app.errorhandler(503)
def h503(e):  # type: ignore
    return render_template("error.html", code=503, message=str(e)), 503

@app.errorhandler(500)
def h500(e):  # type: ignore
    return render_template("error.html", code=500, message="Internal Server Error"), 500

# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True)
