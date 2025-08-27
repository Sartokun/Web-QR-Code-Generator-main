from flask import Flask, render_template, request, send_file, jsonify, url_for, redirect, abort
from io import BytesIO
import os, math, time, json, secrets
from qrcode import QRCode, constants
from qrcode.image.svg import SvgPathImage  # สำหรับ SVG
from PIL import Image, ImageDraw, ImageColor
import numpy as np
from werkzeug.utils import secure_filename
from pathlib import Path
from threading import Lock
import hmac
from werkzeug.utils import safe_join

app = Flask(__name__)

UPLOAD_FOLDER = "static/logo"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# จำกัดขนาดไฟล์สูงสุด 2MB
GLOBAL_MAX_UPLOAD_MB = int(os.getenv("GLOBAL_MAX_UPLOAD_MB", "64"))
app.config['MAX_CONTENT_LENGTH'] = GLOBAL_MAX_UPLOAD_MB * 1024 * 1024
app.config["PREFERRED_URL_SCHEME"] = "https"
ALLOWED_EXT = {"png", "jpg", "jpeg"}

# ไฟล์เก็บ mapping ลิ้งก์สั้น -> URL ยาว
SHORT_DB_PATH = os.path.join("static", "shortlinks.json")
os.makedirs(os.path.dirname(SHORT_DB_PATH), exist_ok=True)
_SHORT_DB_LOCK = Lock()
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"

# --- เพิ่ม helper สำหรับเลือกระดับ Error Correction ---
ECC_MAP = {
    "L": constants.ERROR_CORRECT_L,  # ~7%
    "M": constants.ERROR_CORRECT_M,  # ~15%
    "Q": constants.ERROR_CORRECT_Q,  # ~25%
    "H": constants.ERROR_CORRECT_H,  # ~30%
}
# ---- Bootstrap required dirs (วางไว้หลังสร้าง app) ----
REQUIRED_DIRS = [
    os.path.join("static", "logo"),
    os.path.join("static", "files"),
    os.path.join("static", "files", "pdf"),
    os.path.join("static", "files", "mp3"),
    os.path.join("static", "files", "image"),
]
for d in REQUIRED_DIRS:
    os.makedirs(d, exist_ok=True)


def parse_ecc(val: str):
    return ECC_MAP.get((val or "H").upper(), constants.ERROR_CORRECT_H)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT

# ====== Analytics: daily metrics (visits/unique/downloads/uploads) ======
import json, hashlib
from threading import Lock
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo  # Py3.9+
    BKK_TZ = ZoneInfo("Asia/Bangkok")
except Exception:
    BKK_TZ = timezone(timedelta(hours=7))  # fallback

ANALYTICS_DB_PATH = os.path.join("static", "analytics.json")
os.makedirs(os.path.dirname(ANALYTICS_DB_PATH), exist_ok=True)
_ANALYTICS_LOCK = Lock()
ANALYTICS_SALT = os.getenv("ANALYTICS_SALT", "change_me_salt")  # แนะนำตั้งใน ENV

def _today_key(dt=None):
    dt = dt or datetime.now(BKK_TZ)
    return dt.strftime("%Y-%m-%d")

def _hash_ip(ip: str) -> str:
    h = hashlib.sha256()
    h.update((ip + "|" + ANALYTICS_SALT).encode("utf-8"))
    return h.hexdigest()[:24]  # สั้นพออ่าน

def _read_analytics():
    if not os.path.exists(ANALYTICS_DB_PATH):
        return {}
    try:
        with open(ANALYTICS_DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _write_analytics(db):
    tmp = ANALYTICS_DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)
    os.replace(tmp, ANALYTICS_DB_PATH)

def track_visit(ip: str):
    key = _today_key()
    with _ANALYTICS_LOCK:
        db = _read_analytics()
        day = db.setdefault(key, {"visits": 0, "unique": [], "downloads": 0, "uploads": 0})
        day["visits"] += 1
        h = _hash_ip(ip or "unknown")
        if h not in day["unique"]:
            day["unique"].append(h)
        _write_analytics(db)

def track_download():
    key = _today_key()
    with _ANALYTICS_LOCK:
        db = _read_analytics()
        day = db.setdefault(key, {"visits": 0, "unique": [], "downloads": 0, "uploads": 0})
        day["downloads"] += 1
        _write_analytics(db)

def track_upload():
    key = _today_key()
    with _ANALYTICS_LOCK:
        db = _read_analytics()
        day = db.setdefault(key, {"visits": 0, "unique": [], "downloads": 0, "uploads": 0})
        day["uploads"] += 1
        _write_analytics(db)

def analytics_series(days=30):
    """คืนข้อมูล 30 วันหลังสุดสำหรับ chart"""
    db = _read_analytics()
    labels, visits, uniques, downloads, uploads = [], [], [], [], []
    today = datetime.now(BKK_TZ).date()
    for i in range(days-1, -1, -1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        row = db.get(d, {"visits": 0, "unique": [], "downloads": 0, "uploads": 0})
        labels.append(d)
        visits.append(row.get("visits", 0))
        uniques.append(len(row.get("unique", [])))
        downloads.append(row.get("downloads", 0))
        uploads.append(row.get("uploads", 0))
    return {
        "labels": labels, "visits": visits, "uniques": uniques,
        "downloads": downloads, "uploads": uploads
    }

def analytics_totals(days=30):
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
        }
    }
# =======================================================================
def _human_bytes(n: int) -> str:
    """แปลง byte เป็นข้อความอ่านง่าย"""
    step = 1024.0
    units = ["B", "KB", "MB", "GB", "TB"]
    s = float(n)
    for u in units:
        if s < step or u == units[-1]:
            if u == "B":
                return f"{int(s)} {u}"
            return f"{s:.2f} {u}"
        s /= step

# ===== เพิ่ม helper ทำไล่สี =====
def _linear_gradient(size, c1, c2):
    import numpy as np
    w, h = size
    x = np.linspace(0.0, 1.0, w, dtype=np.float32)
    y = np.linspace(0.0, 1.0, h, dtype=np.float32)
    t = (x + y[:, None]) * 0.5  # ไล่จากมุมซ้ายบน -> ขวาล่าง
    c1 = np.array(c1, dtype=np.float32)
    c2 = np.array(c2, dtype=np.float32)
    rgb = (c1 + (c2 - c1) * t[..., None]).clip(0, 255).astype(np.uint8)
    a = np.full((h, w, 1), 255, dtype=np.uint8)
    return Image.fromarray(np.concatenate([rgb, a], axis=2), "RGBA")

def _radial_gradient(size, c1, c2):
    import numpy as np
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
    data,
    logo_path=None,
    fill_color="#000",
    back_color="#fff",
    transparent=False,
    size_px=None,
    ecc="H",
    fill_style="solid",       # 'solid' | 'linear' | 'radial'
    fill_color2="#000000"     # ใช้เมื่อเป็นไล่สี
):
    """
    สร้าง QR PNG พร้อมรองรับไล่สี (Linear/Radial) โดยใช้ QR เป็น 'มาสก์'
    """
    qr = QRCode(version=5, error_correction=parse_ecc(ecc), box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)

    # ปรับขนาดให้ใกล้ size_px ที่ขอ
    if size_px:
        modules = qr.modules_count + qr.border * 2
        qr.box_size = max(1, math.ceil(size_px / modules))

    # สร้างภาพมาสก์จาก QR (ดำ-ขาว) แล้ว invert ให้โมดูล = 255
    mask_gray = qr.make_image(fill_color="#000000", back_color="#ffffff").convert("L")
    w, h = mask_gray.size
    mask = mask_gray.point(lambda p: 255 - p)  # ดำ->255, ขาว->0

    # พื้นหลัง
    if transparent:
        base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    else:
        bg = ImageColor.getrgb(back_color)
        base = Image.new("RGBA", (w, h), (*bg, 255))

    # เลเยอร์สีของโมดูล
    c1 = ImageColor.getrgb(fill_color)
    if fill_style == "linear":
        c2 = ImageColor.getrgb(fill_color2 or fill_color)
        color_img = _linear_gradient((w, h), c1, c2)
    elif fill_style == "radial":
        c2 = ImageColor.getrgb(fill_color2 or fill_color)
        color_img = _radial_gradient((w, h), c1, c2)
    else:
        color_img = Image.new("RGBA", (w, h), (*c1, 255))

    # ผสมสีเข้ากับพื้นหลังด้วยมาสก์ QR
    base.paste(color_img, (0, 0), mask)

    # โลโก้ (ถ้ามี)
    if logo_path and os.path.exists(logo_path):
        logo_size = w // 4
        logo = resize_logo_keep_ratio_with_padding(logo_path, logo_size, pad_ratio=0.13)
        # กล่องรองโลโก้
        box_color = (255, 255, 255, 255) if not transparent else (255, 255, 255, 0)
        x = (w - logo_size) // 2
        y = (h - logo_size) // 2
        box_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(box_layer).rectangle([x, y, x + logo_size, y + logo_size], fill=box_color)
        base = Image.alpha_composite(base, box_layer)
        base.paste(logo, (x, y), mask=logo)

    # บังคับขนาดปลายทางตามที่เลือก (รักษาความคม)
    if size_px and (base.width != size_px or base.height != size_px):
        base = base.resize((size_px, size_px), Image.NEAREST)

    return base


def generate_qr_code_svg(data, fill_color="#000", back_color="#fff", transparent=False,ecc="H"):
    """
    สร้าง SVG (ไม่รองรับโลโก้ในเวอร์ชันนี้)
    """
    qr = QRCode(
        version=5,
        error_correction=parse_ecc(ecc),
        box_size=10,
        border=4
    )
    qr.add_data(data)
    qr.make(fit=True)

    bg = None if transparent else back_color
    img = qr.make_image(
        image_factory=SvgPathImage,
        fill_color=fill_color,
        back_color=bg
    )
    # qrcode.svg image มีเมธอด to_string()
    svg_bytes = img.to_string()
    return svg_bytes


def trim_transparent(img):
    bbox = img.getbbox()
    if bbox:
        return img.crop(bbox)
    return img


def resize_logo_keep_ratio_with_padding(logo_path, box_size, pad_ratio=0.1):
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


@app.route("/", methods=["GET", "POST"])
def index():
    track_visit(request.headers.get("X-Forwarded-For", request.remote_addr))
    logos = [f for f in os.listdir(UPLOAD_FOLDER) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
    ecc = (request.form.get("ecc") or "H").upper()
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

        if out_format == "svg":
            # SVG (เวอร์ชันแรกไม่รองรับโลโก้)
            if logo_path:
                return "SVG download does not support logo in this version. Please remove the logo or choose PNG.", 400
            svg_bytes = generate_qr_code_svg(
                data,
                fill_color=fill_color,
                back_color=back_color,
                transparent=transparent,
                ecc=ecc
            )
            buf = BytesIO(svg_bytes)
            buf.seek(0)
            track_download()
            return send_file(buf, mimetype="image/svg+xml", as_attachment=True, download_name="qr_code.svg")

        # PNG
        img = generate_qr_code_png(
            data,
            logo_path,
            fill_color,
            back_color,
            transparent,
            size_px=size_px,
            ecc=ecc,
            fill_style=fill_style,
            fill_color2=fill_color2
        )
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        track_download()
        return send_file(buf, mimetype="image/png", as_attachment=True, download_name="qr_code.png")

    return render_template("index.html", logos=logos)

# ===== อัปโหลดไฟล์ asset (pdf/mp3/image) =====
ASSET_FOLDERS = {
    "pdf":   os.path.join("static", "files", "pdf"),
    "mp3":   os.path.join("static", "files", "mp3"),
    "image": os.path.join("static", "files", "image"),
}
ASSET_EXTS = {
    "pdf":   {"pdf"},
    "mp3":   {"mp3"},
    "image": {"png", "jpg", "jpeg"},
}
ASSET_MAX_MB = {"pdf": 10, "mp3": 15, "image": 5}

for _p in ASSET_FOLDERS.values():
    os.makedirs(_p, exist_ok=True)

def _allowed_asset(atype, filename):
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    return ext in ASSET_EXTS.get(atype, set())

@app.post("/upload_asset/<atype>")
def upload_asset(atype):
    atype = (atype or "").lower()
    if atype not in ASSET_FOLDERS:
        return jsonify(error="unsupported asset type"), 400

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="no file"), 400

    # --- หา ext จาก "ชื่อไฟล์ต้นฉบับ" ก่อน ---
    orig_name = f.filename
    orig_ext = os.path.splitext(orig_name)[1].lower().lstrip(".")

    # ถ้าชื่อไฟล์เป็นอักษร non-ASCII จน secure_filename ตัดทิ้ง ext หาย
    # ให้เดา ext จาก mimetype แทน
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

    # --- ตั้งชื่อไฟล์อย่างปลอดภัย (ใช้ secure_filename เฉพาะ 'ชื่อ' ไม่รวม ext) ---
    base_stem = secure_filename(os.path.splitext(orig_name)[0]) or "file"

    # --- ตรวจขนาดไฟล์ตามเพดานประเภทย่อย ---
    pos = f.stream.tell()
    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(pos)
    max_bytes = ASSET_MAX_MB[atype] * 1024 * 1024
    if size > max_bytes:
        return jsonify(error=f"file too large (>{ASSET_MAX_MB[atype]} MB)"), 400

    final_name = f"{base_stem}_{int(time.time())}.{orig_ext}"
    save_dir = ASSET_FOLDERS[atype]
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, final_name)
    f.save(save_path)

    long_url = url_for("static", filename=f"files/{atype}/{final_name}", _external=True)
    short_url = create_short_link(long_url)
    track_upload()
    return jsonify(success=True, url=long_url, short_url=short_url,
                   filename=final_name, size=size)

@app.route("/upload_logo", methods=["POST"])
def upload_logo():
    file = request.files.get("logo")
    if not file or file.filename == "":
        return "No file selected", 400
    if not allowed_file(file.filename):
        return "Invalid file type. Allowed: .png, .jpg, .jpeg", 400

    fname = secure_filename(file.filename)
    save_path = os.path.join(UPLOAD_FOLDER, fname)
    try:
        img = Image.open(file.stream)
        img.verify()
        fmt = (img.format or "").upper()
        if fmt not in {"PNG", "JPEG"}:
            return "Invalid image format. Only PNG or JPEG are allowed.", 400
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

    # พรีวิวเป็น PNG เสมอ
    try:
        size_px = int(request.form.get("size_px") or "512")
    except Exception:
        size_px = 512

    logo_name = secure_filename(request.form.get("logo") or "")
    logo_path = os.path.join(UPLOAD_FOLDER, logo_name) if logo_name else None
    if logo_path and not os.path.exists(logo_path):
        logo_path = None

    img = generate_qr_code_png(
        data,
        logo_path,
        fill_color,
        back_color,
        transparent,
        size_px=size_px,
        ecc=ecc,
        fill_style=fill_style,
        fill_color2=fill_color2
    )
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route('/delete_logo/<logo_name>', methods=['DELETE'])
def delete_logo(logo_name):
    fname = secure_filename(logo_name)
    path = Path(UPLOAD_FOLDER) / fname
    try:
        if path.exists() and path.resolve().parent == Path(UPLOAD_FOLDER).resolve():
            path.unlink()
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'ไม่พบไฟล์'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ข้อความเมื่อไฟล์เกินกำหนด
@app.errorhandler(413)
def too_large(e):
    return jsonify(
        success=False,
        error=f"ไฟล์ใหญ่เกินเพดานรวม {GLOBAL_MAX_UPLOAD_MB} MB (global limit)"
    ), 413

def _read_short_db():
    if not os.path.exists(SHORT_DB_PATH):
        return {}
    try:
        with open(SHORT_DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _write_short_db(db):
    tmp = SHORT_DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)
    os.replace(tmp, SHORT_DB_PATH)

def _gen_code(n=6):
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))

def create_short_link(long_url):
    """สร้างโค้ดสั้นและบันทึกลงไฟล์, คืน url สั้นแบบ absolute"""
    with _SHORT_DB_LOCK:
        db = _read_short_db()
        code = _gen_code(6)
        while code in db:
            code = _gen_code(6)
        db[code] = {"url": long_url, "ts": int(time.time())}
        _write_short_db(db)
    return url_for("resolve_short", code=code, _external=True)

@app.get("/s/<code>")
def resolve_short(code):
    db = _read_short_db()
    item = db.get(code)
    if not item:
        abort(404)
    return redirect(item["url"], code=302)

# ==== Admin config & helpers ===================================================
import json
import time
from threading import Lock
from datetime import datetime

from flask import (
    Flask, request, render_template, send_file, jsonify, url_for,
    redirect, abort
)
from werkzeug.utils import secure_filename

# ใช้ key ง่าย ๆ ก่อน (เปลี่ยนใน PROD): เข้าผ่าน /admin?key=YOUR_ADMIN_KEY
ADMIN_KEY = os.getenv("ADMIN_KEY", "changeme")
app.config.setdefault("PREFERRED_URL_SCHEME", "https")  # ให้ url_for สร้าง https ถ้ามี reverse proxy

# short-link DB (ถ้ายังไม่มีจากขั้นก่อน ให้คงไว้ได้เลย)
SHORT_DB_PATH = os.path.join("static", "shortlinks.json")
os.makedirs(os.path.dirname(SHORT_DB_PATH), exist_ok=True)
_SHORT_DB_LOCK = Lock()

def _read_short_db():
    if not os.path.exists(SHORT_DB_PATH):
        return {}
    try:
        with open(SHORT_DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _write_short_db(db):
    tmp = SHORT_DB_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False)
    os.replace(tmp, SHORT_DB_PATH)

def _human_bytes(n):
    for unit in ["B","KB","MB","GB","TB"]:
        if n < 1024 or unit == "TB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0

def _require_admin():
    # ถ้าไม่ตั้งค่าไว้ ให้ตอบ 503 ชัดเจน
    if not ADMIN_KEY:
        abort(503, description="ADMIN_KEY is not configured on the server.")
    supplied = request.args.get("key") or request.headers.get("X-Admin-Key")
    ok = supplied and hmac.compare_digest(str(supplied), str(ADMIN_KEY))
    if not ok:
        abort(403, description="Forbidden: invalid admin key.")

def _file_rows():
    """รวบรวมรายการไฟล์ทั้งหมดสำหรับหน้า admin"""
    rows = []

    # โลโก้
    logo_dir = os.path.join("static", "logo")
    os.makedirs(logo_dir, exist_ok=True)
    for name in sorted(os.listdir(logo_dir)):
        path = os.path.join(logo_dir, name)
        if not os.path.isfile(path):
            continue
        st = os.stat(path)
        rows.append({
            "kind": "logo",
            "atype": None,
            "name": name,
            "size": st.st_size,
            "mtime": st.st_mtime,
            "mtime_iso": datetime.fromtimestamp(st.st_mtime).isoformat(sep=" ", timespec="seconds"),
            "url": url_for("static", filename=f"logo/{name}", _external=True),
            "short": None,
            "thumb": url_for("static", filename=f"logo/{name}", _external=False)  # ใช้เป็น thumbnail ได้
        })

    # ไฟล์ asset (pdf/mp3/image)
    for atype, folder in ASSET_FOLDERS.items():
        os.makedirs(folder, exist_ok=True)
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            st = os.stat(path)
            long_url = url_for("static", filename=f"files/{atype}/{name}", _external=True)
            rows.append({
                "kind": "asset",
                "atype": atype,
                "name": name,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "mtime_iso": datetime.fromtimestamp(st.st_mtime).isoformat(sep=" ", timespec="seconds"),
                "url": long_url,
                "short": None,     # จะเติมด้านล่างจาก DB
                "thumb": url_for("static", filename=f"files/{atype}/{name}", _external=False)
                           if atype == "image" else None
            })

    # เติม short links ถ้ามี
    db = _read_short_db()
    url_to_short = {}
    for code, item in db.items():
        url_to_short[item.get("url")] = url_for("resolve_short", code=code, _external=True)
    for r in rows:
        r["short"] = url_to_short.get(r["url"])

    # ใหม่สุดก่อน
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows

# ==== Admin routes =============================================================
def _safe_json_load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _list_assets():
    rows = []
    for atype in ("pdf", "mp3", "image"):
        base = os.path.join("static", "files", atype)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            full = os.path.join(base, name)
            try:
                st = os.stat(full)
            except FileNotFoundError:
                continue
            rows.append({
                "name": name,
                "atype": atype,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "url": url_for("static", filename=f"files/{atype}/{name}", _external=True),
            })
    return rows

@app.get("/admin")
def admin_index():
    _require_admin()
    rows = _list_assets()
    
    total_size = sum(r.get("size", 0) for r in rows)
    totals = {
        "all":   len(rows),
        "pdf":   sum(1 for r in rows if r.get("atype") == "pdf"),
        "mp3":   sum(1 for r in rows if r.get("atype") == "mp3"),
        "image": sum(1 for r in rows if r.get("atype") == "image"),
        "size":  _human_bytes(total_size),
        "size_bytes": total_size,
    }

    urls = _admin_nav_urls()
    return render_template(
        "admin.html",
        rows=rows, files=rows,
        totals=totals,
        admin_key=ADMIN_KEY,
        **urls
    )

@app.post("/admin/delete")
def admin_delete():
    _require_admin()

    # รับได้ทั้ง JSON และ form
    data = request.get_json(silent=True) or request.form or {}

    atype = (data.get("atype") or data.get("type") or data.get("category") or "").lower()
    fname = (data.get("filename") or data.get("name") or data.get("file") or "").strip()

    if not atype or not fname or atype not in ASSET_FOLDERS:
        return jsonify(success=False, error="invalid params"), 400

    # กัน path traversal และตรวจนามสกุลตามชนิด
    fname = os.path.basename(fname)
    ext = os.path.splitext(fname)[1].lower().lstrip(".")
    if ext not in ASSET_EXTS.get(atype, set()):
        return jsonify(success=False, error="invalid extension"), 400

    folder = ASSET_FOLDERS[atype]
    fpath = safe_join(folder, fname)

    if not fpath or not os.path.isfile(fpath):
        return jsonify(success=False, error="not found"), 404

    try:
        os.remove(fpath)
    except Exception as e:
        return jsonify(success=False, error=f"delete failed: {e}"), 500

    return jsonify(success=True)

@app.post("/admin/shorten")
def admin_shorten():
    _require_admin()
    long_url = request.form.get("url")
    if not long_url:
        return jsonify(error="missing url"), 400

    # สร้าง short code ใหม่ (ใช้ฟังก์ชัน create_short_link ที่คุณมีอยู่)
    short_url = create_short_link(long_url)
    track_upload()
    return jsonify(success=True, short_url=short_url)

@app.get("/admin/dashboard")
def admin_dashboard():
    _require_admin()
    days = int(request.args.get("days", "30"))
    series = analytics_series(days=days)
    totals = analytics_totals(days=days)

    urls = _admin_nav_urls()
    return render_template(
        "admin_dashboard.html",
        series=series, totals=totals, days=days,
        admin_key=ADMIN_KEY,
        **urls
    )
    
# ===== helper: admin nav urls =====
def _admin_nav_urls():
    return {
        "dash_url": url_for("admin_dashboard", key=ADMIN_KEY),
        "files_url": url_for("admin_index", key=ADMIN_KEY)
    }
    
# ==== Error Handlers ===========================================================
@app.errorhandler(403)
def h403(e):  # type: ignore
    return render_template("error.html", code=403, message=str(e)), 403

@app.errorhandler(503)
def h503(e):  # type: ignore
    return render_template("error.html", code=503, message=str(e)), 503

@app.errorhandler(500)
def h500(e):  # type: ignore
    # ไม่โชว์รายละเอียดภายในในโปรดักชัน
    return render_template("error.html", code=500, message="Internal Server Error"), 500

if __name__ == "__main__":
    app.run(debug=True)
