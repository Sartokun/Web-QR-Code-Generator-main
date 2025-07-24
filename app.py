from flask import Flask, render_template, request, send_file
from io import BytesIO
import os
from qrcode import QRCode, constants
from PIL import Image, ImageDraw, ImageColor
import numpy as np

app = Flask(__name__)
UPLOAD_FOLDER = "static/logo"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def generate_qr_code(data, logo_path=None, fill_color="#000", back_color="#fff", transparent=False):
    qr = QRCode(
        version=5,
        error_correction=constants.ERROR_CORRECT_H,
        box_size=10,
        border=4
    )

    qr.add_data(data)
    qr.make(fit=True)
    # รับ HEX/RGB ได้หมด
    qr_img = qr.make_image(fill_color=fill_color, back_color=back_color).convert("RGBA")
    qr_width, qr_height = qr_img.size
    logo = None
    if logo_path and os.path.exists(logo_path):
        logo_size = qr_width // 4
        logo = resize_logo_keep_ratio_with_padding(logo_path, logo_size, pad_ratio=0.13)
        logo_pos = ((qr_width - logo_size) // 2, (qr_height - logo_size) // 2)
        logo_box = (logo_pos[0], logo_pos[1], logo_pos[0] + logo_size, logo_pos[1] + logo_size)
        box_color = (255, 255, 255, 255) if not transparent else (255,255,255,0)
        box_layer = Image.new("RGBA", qr_img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(box_layer)
        draw.rectangle(logo_box, fill=box_color)
        qr_img = Image.alpha_composite(qr_img, box_layer)
        qr_img.paste(logo, logo_pos, mask=logo)
    if transparent:
        qr_array = np.array(qr_img)
        r, g, b, a = qr_array[:, :, 0], qr_array[:, :, 1], qr_array[:, :, 2], qr_array[:, :, 3]
        target_color = ImageColor.getrgb(back_color)
        mask = (r == target_color[0]) & (g == target_color[1]) & (b == target_color[2])
        qr_array[:, :, 3] = np.where(mask, 0, a)
        qr_img = Image.fromarray(qr_array)
    if logo is not None:
        logo_pos = ((qr_width - logo.size[0]) // 2, (qr_height - logo.size[1]) // 2)
        qr_img.paste(logo, logo_pos, mask=logo)
    return qr_img

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
    if request.method == "POST":
        data = request.form.get("data", "")
        fill_color = request.form.get("fill_color", "#000")
        back_color = request.form.get("back_color", "#fff")
        transparent = bool(request.form.get("transparent"))
        logo_name = request.form.get("logo")
        logo_path = os.path.join(UPLOAD_FOLDER, logo_name) if logo_name else None
        img = generate_qr_code(data, logo_path, fill_color, back_color, transparent)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return send_file(buf, mimetype="image/png", as_attachment=True, download_name="qr_code.png")
    return render_template("index.html", logos=logos)

@app.route("/upload_logo", methods=["POST"])
def upload_logo():
    file = request.files["logo"]
    if file:
        path = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(path)
    return "OK"

@app.route("/preview_qr", methods=["POST"])
def preview_qr():
    data = request.form.get("data", "")
    fill_color = request.form.get("fill_color", "#000")
    back_color = request.form.get("back_color", "#fff")
    transparent = bool(request.form.get("transparent"))
    logo_name = request.form.get("logo")
    logo_path = os.path.join(UPLOAD_FOLDER, logo_name) if logo_name else None
    img = generate_qr_code(data, logo_path, fill_color, back_color, transparent)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")

if __name__ == "__main__":
    app.run(debug=True)
