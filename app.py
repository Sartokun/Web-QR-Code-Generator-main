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

app = Flask(__name__)

UPLOAD_FOLDER = "static/logo"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# จำกัดขนาดไฟล์สูงสุด 2MB
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024
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
def parse_ecc(val: str):
    return ECC_MAP.get((val or "H").upper(), constants.ERROR_CORRECT_H)


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXT


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

    # ตรวจนามสกุล
    base = secure_filename(f.filename)
    stem, ext = os.path.splitext(base)
    ext_l = ext.lower().lstrip(".")
    if ext_l not in ASSET_EXTS.get(atype, set()):
        return jsonify(error="invalid extension"), 400

    # ตรวจขนาดไฟล์
    f.stream.seek(0, os.SEEK_END)
    size = f.stream.tell()
    f.stream.seek(0)
    max_bytes = ASSET_MAX_MB[atype] * 1024 * 1024
    if size > max_bytes:
        return jsonify(error=f"file too large (>{ASSET_MAX_MB[atype]} MB)"), 400

    # ตั้งชื่อไฟล์แบบปลอดภัย + กันเคสชื่อว่าง/เป็นแค่เครื่องหมาย
    safe_stem = (stem or "").strip("-_.")
    if not safe_stem:
        safe_stem = "file"
    final_name = f"{safe_stem}_{int(time.time())}.{ext_l}"

    # เซฟ
    save_dir = ASSET_FOLDERS[atype]
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, final_name)
    f.save(save_path)

    # ส่งกลับเป็น "ลิงก์แบบ absolute" ใช้งานได้ทันทีใน QR
    long_url = url_for("static", filename=f"files/{atype}/{final_name}", _external=True)
    short_url = create_short_link(long_url)
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
    return "File too large. Max 2MB.", 413

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


if __name__ == "__main__":
    app.run(debug=True)
