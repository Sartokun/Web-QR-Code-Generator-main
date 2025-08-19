/* ===== Coloris Picker Config ===== */
Coloris({
  el: "[data-coloris]",
  theme: "large",
  themeMode: "dark",
  format: "hex",
  swatches: [
    "#222",
    "#fff",
    "#e53935",
    "#fb8c00",
    "#fdd835",
    "#43a047",
    "#1e88e5",
    "#8e24aa",
  ],
  alpha: false,
});

/* ===== Utils ===== */
const PREVIEW_DEBOUNCE_MS = 150; // ปรับได้ 150–300ms
const ONE_MB = 1024 * 1024;

function bindAssetDnD(type) {
  const drop = document.getElementById(`assetDrop_${type}`);
  const cfg = ASSET_CFG[type];
  if (!drop || !cfg) return;

  const input = document.getElementById(cfg.inputId);

  const enter = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    drop.classList.add("drag-over");
  };
  const leave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    drop.classList.remove("drag-over");
  };

  drop.addEventListener("dragenter", enter);
  drop.addEventListener("dragover", enter);
  drop.addEventListener("dragleave", leave);
  drop.addEventListener("dragend", leave);

  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    e.stopPropagation();
    drop.classList.remove("drag-over");

    const files = e.dataTransfer?.files || [];
    if (!files.length) return;
    const f = files[0];

    // ใส่ไฟล์ลง <input type=file> เพื่อให้ใช้ลอจิกเดิม
    const dt = new DataTransfer();
    dt.items.add(f);
    if (input) input.files = dt.files;

    // แสดงรายละเอียดและเปิดปุ่มอัปโหลด (ไม่อัปโหลดอัตโนมัติ)
    handleAssetFileChange(type);
  });
}

// ===== Assets (pdf/mp3/image) =====
const ASSET_CFG = {
  pdf: {
    inputId: "assetFileInput_pdf",
    pillId: "assetFileName_pdf",
    btnId: "uploadAssetBtn_pdf",
    urlInputId: "dt_pdf",
    maxMB: 10,
    accept: ["application/pdf", ".pdf"],
  },
  mp3: {
    inputId: "assetFileInput_mp3",
    pillId: "assetFileName_mp3",
    btnId: "uploadAssetBtn_mp3",
    urlInputId: "dt_mp3",
    maxMB: 15,
    accept: ["audio/mpeg", ".mp3"],
  },
  image: {
    inputId: "assetFileInput_image",
    pillId: "assetFileName_image",
    btnId: "uploadAssetBtn_image",
    urlInputId: "dt_image",
    maxMB: 5,
    accept: ["image/png", "image/jpeg", ".png", ".jpg", ".jpeg"],
  },
};

function enableAssetBtn(type, enabled) {
  const btn = document.getElementById(ASSET_CFG[type].btnId);
  if (btn) btn.disabled = !enabled;
}
function setAssetPill(type, text, status) {
  // status: '', 'ok', 'warn'
  const pill = document.getElementById(ASSET_CFG[type].pillId);
  if (!pill) return;
  pill.textContent = text;
  pill.title = text;
  pill.classList.remove("ok", "warn");
  if (status) pill.classList.add(status);
}
function humanMB(bytes) {
  return (bytes / 1024 / 1024).toFixed(2) + " MB";
}

function handleAssetFileChange(type) {
  const cfg = ASSET_CFG[type];
  const input = document.getElementById(cfg.inputId);
  if (!input || !input.files.length) {
    setAssetPill(type, "ยังไม่ได้เลือกไฟล์");
    enableAssetBtn(type, false);
    return;
  }
  const f = input.files[0];

  // ตรวจชนิด/ขนาดหยาบๆ
  const okType = cfg.accept.some(
    (t) => f.type === t || f.name.toLowerCase().endsWith(t)
  );
  if (!okType) {
    setAssetPill(type, `ชนิดไฟล์ไม่รองรับ: ${f.name}`, "warn");
    enableAssetBtn(type, false);
    return;
  }
  const tooBig = f.size > cfg.maxMB * 1024 * 1024;
  setAssetPill(
    type,
    `ไฟล์: ${f.name} — ${formatBytes(f.size)} (จำกัด ~${cfg.maxMB} MB)`,
    tooBig ? "warn" : "ok"
  );
  enableAssetBtn(type, !tooBig);
}

function uploadAsset(type) {
  const cfg = ASSET_CFG[type];
  const input = document.getElementById(cfg.inputId);
  if (!input || !input.files.length) {
    alert("เลือกไฟล์ก่อน");
    return;
  }
  const f = input.files[0];

  const fd = new FormData();
  fd.append("file", f);

  const btn = document.getElementById(cfg.btnId);
  if (btn) {
    btn.disabled = true;
    btn.textContent = "กำลังอัปโหลด...";
  }

  fetch(`/upload_asset/${encodeURIComponent(type)}`, {
    method: "POST",
    body: fd,
  })
    .then(async (resp) => {
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data?.error || "อัปโหลดไม่สำเร็จ");
      // ตั้ง URL ให้ input ของประเภทนั้น ๆ
      const urlInput = document.getElementById(cfg.urlInputId);
      const finalUrl = data.short_url || data.url;
      if (urlInput) {
        urlInput.value = finalUrl || "";
      }
      // อัปเดตตัวอย่าง/พรีวิว
      updateDataPreviewOnly();
      debouncedUpdatePreview();
      setAssetPill(
        type,
        `อัปโหลดแล้ว: ${data.filename} — ${formatBytes(data.size || 0)}`,
        "ok"
      );
    })
    .catch((err) => {
      alert("อัปโหลดล้มเหลว: " + err.message);
      enableAssetBtn(type, true);
    })
    .finally(() => {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "อัปโหลด";
      }
    });
}

function updateUploadButtonState(hasFile) {
  const btn = document.getElementById("uploadLogoBtn");
  if (!btn) return;
  btn.disabled = !hasFile;
}

function escWiFi(str) {
  return (str || "").replace(/([\\;,:"])/g, "\\$1"); // escape ตามสเปก Wi-Fi
}
function composeDataFromBuilder() {
  const type = document.getElementById("data_type")?.value || "url";
  switch (type) {
    case "url":
      return document.getElementById("dt_url").value.trim();
    case "text":
      return document.getElementById("dt_text").value;
    case "wifi": {
      const ssid = escWiFi(document.getElementById("wifi_ssid").value);
      const auth = document.getElementById("wifi_auth").value || "WPA";
      const pass = escWiFi(document.getElementById("wifi_pass").value);
      const hidden = document.getElementById("wifi_hidden").checked
        ? "true"
        : "false";
      return `WIFI:T:${auth};S:${ssid};${
        auth !== "nopass" ? `P:${pass};` : ""
      }H:${hidden};;`;
    }
    case "email": {
      const to = (document.getElementById("email_to").value || "").trim();
      const subj = encodeURIComponent(
        document.getElementById("email_subject").value || ""
      );
      const body = encodeURIComponent(
        (document.getElementById("email_body").value || "").replace(
          /\n/g,
          "%0A"
        )
      );
      let url = `mailto:${to}`;
      const q = [];
      if (subj) q.push(`subject=${subj}`);
      if (body) q.push(`body=${body}`);
      if (q.length) url += `?${q.join("&")}`;
      return url;
    }
    case "sms": {
      const to = (document.getElementById("sms_to").value || "").trim();
      const body = document.getElementById("sms_body").value || "";
      return `SMSTO:${to}:${body}`;
    }
    case "pdf":
      return document.getElementById("dt_pdf").value.trim();
    case "mp3":
      return document.getElementById("dt_mp3").value.trim();
    case "image":
      return document.getElementById("dt_image").value.trim();
    default:
      return "";
  }
}
function toggleTypeGroup() {
  const type = document.getElementById("data_type").value;
  document.querySelectorAll("#data_type_fields .dt-group").forEach((el) => {
    el.style.display = el.dataset.type === type ? "" : "none";
  });
}
function updateDataPreviewOnly() {
  const dataStr = composeDataFromBuilder();
  document.getElementById("data").value = dataStr;
  document.getElementById("dataPreview").textContent = dataStr || "(ว่าง)";
}

function toggleColor2() {
  const style = document.getElementById("fill_style")?.value || "solid";
  const el = document.getElementById("color2Wrap");
  if (!el) return;
  el.style.display = style === "linear" || style === "radial" ? "" : "none";
}

function debounce(fn, delay = 200) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), delay);
  };
}
function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  const k = 1024,
    sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}
function getRecommendedLogoEdge() {
  const sel = document.getElementById("size_px");
  const size = sel ? parseInt(sel.value, 10) || 1024 : 1024;
  // โลโก้แนะนำ ~25% ของด้านกว้าง QR
  return Math.round(size * 0.25);
}

/* ===== Global (for preview & drop) ===== */
let previewAbortController = null;

/* ===== Upload (shared) ===== */
function uploadLogoWithFile(file) {
  const okTypes = ["image/png", "image/jpeg"];
  const okExt = [".png", ".jpg", ".jpeg"];
  const nameLower = file.name.toLowerCase();
  const hasOkExt = okExt.some((ext) => nameLower.endsWith(ext));
  if (!okTypes.includes(file.type) || !hasOkExt) {
    alert("รับเฉพาะไฟล์ .png หรือ .jpg/.jpeg เท่านั้น");
    return;
  }

  const formData = new FormData();
  formData.append("logo", file);

  fetch("/upload_logo", { method: "POST", body: formData })
    .then(async (resp) => {
      const text = await resp.text();
      if (!resp.ok) throw new Error(text || "อัปโหลดไม่สำเร็จ");
      alert("อัปโหลดโลโก้สำเร็จ!");
      location.reload();
    })
    .catch((err) => {
      if (err?.name === "AbortError") return;
      alert("อัปโหลดล้มเหลว: " + err.message);
    });
}

function uploadLogo() {
  const fileInput = document.getElementById("logoUpload");
  if (fileInput.files.length === 0) return alert("เลือกไฟล์ก่อน!");
  uploadLogoWithFile(fileInput.files[0]);
}

/* ===== Analyze file (size & dimensions) ===== */
function analyzeAndPreviewLocalLogo(file) {
  const metaBox = document.getElementById("uploadFileName");
  const dz = document.getElementById("logoMainPreview");

  // อัปเดต file ให้กับ <input type=file> เพื่อให้กดปุ่ม "อัปโหลดโลโก้" ได้เลย
  const dt = new DataTransfer();
  dt.items.add(file);
  document.getElementById("logoUpload").files = dt.files;

  // อ่านภาพเพื่อเอามิติ
  const reader = new FileReader();
  reader.onload = () => {
    const img = new Image();
    img.onload = () => {
      // พรีวิวภาพในกล่อง
      dz.innerHTML = "";
      const thumb = document.createElement("img");
      thumb.src = reader.result;
      thumb.alt = "logo preview";
      dz.appendChild(thumb);

      // คำนวณคำแนะนำ
      const longest = Math.max(img.width, img.height);
      const rec = getRecommendedLogoEdge();

      // เกณฑ์เตือน: ด้านยาว > 1.6× ของแนะนำ หรือ ขนาดไฟล์ > 1 MB
      const tooLarge = longest > Math.round(rec * 1.6) || file.size > ONE_MB;
      const text =
        `ไฟล์: ${file.name} — ${img.width}×${img.height}px, ${formatBytes(
          file.size
        )} ` + `| แนะนำ: ด้านยาว ≤ ${rec}px (≈25% ของขนาด QR)`;

      metaBox.textContent = text;
      metaBox.title = text;
      metaBox.classList.remove("ok", "warn");
      metaBox.classList.add(tooLarge ? "warn" : "ok");

      if (tooLarge) {
        // ไม่บังคับลด แต่เตือนให้ผู้ใช้ทราบ
        console.warn("โลโก้ใหญ่เกินจำเป็น: อาจไม่คุ้มต่อขนาดไฟล์");
      }
    };
    img.src = reader.result;
  };
  reader.readAsDataURL(file);
}

/* ===== Preview (debounce + abort) ===== */
function updatePreview() {
  updateDataPreviewOnly();

  const form = document.getElementById("qrForm");
  const formData = new FormData(form);

  // โลโก้/โปร่งใส/ขนาด
  const logoInput = form.querySelector("input[name='logo']");
  formData.set("logo", logoInput.value || "");
  const transparentChecked = form.querySelector("#transparent").checked;
  formData.set("transparent", transparentChecked ? "1" : "");
  const sizeSel = document.getElementById("size_px");
  formData.set("size_px", sizeSel ? sizeSel.value : "512");

  // ECC
  const eccSel = document.getElementById("ecc");
  if (eccSel) formData.set("ecc", eccSel.value || "H");

  // ----- เฉพาะสไตล์สี -----
  formData.set(
    "fill_style",
    document.getElementById("fill_style")?.value || "solid"
  );
  formData.set(
    "fill_color2",
    document.getElementById("fill_color2")?.value || "#000000"
  );

  // พรีวิวเป็น PNG เสมอ
  formData.set("out_format", "png");

  if (previewAbortController) previewAbortController.abort();
  previewAbortController = new AbortController();

  fetch("/preview_qr", {
    method: "POST",
    body: formData,
    signal: previewAbortController.signal,
  })
    .then((resp) => (resp.ok ? resp.blob() : null))
    .then((blob) => {
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      document.getElementById("qrPreview").src = url;

      const qrBG = document.getElementById("qrPreviewBG");
      if (transparentChecked) qrBG.classList.add("qr-preview-bg");
      else qrBG.classList.remove("qr-preview-bg");
    })
    .catch((err) => {
      if (err?.name !== "AbortError") console.error(err);
    });
}
const debouncedUpdatePreview = debounce(updatePreview, PREVIEW_DEBOUNCE_MS);

/* ===== Misc UI ===== */
function showUploadFileName() {
  const fileInput = document.getElementById("logoUpload");
  const metaBox = document.getElementById("uploadFileName");
  if (!fileInput.files.length) {
    metaBox.textContent = "ยังไม่ได้เลือกไฟล์";
    updateUploadButtonState(false);
    return;
  }
  analyzeAndPreviewLocalLogo(fileInput.files[0]);
  updateUploadButtonState(true);
}

document.addEventListener("DOMContentLoaded", function () {
  // ===== bind asset file change =====
  ["pdf", "mp3", "image"].forEach((type) => {
    const cfg = ASSET_CFG[type];
    const input = document.getElementById(cfg.inputId);
    if (input) {
      input.addEventListener("change", () => handleAssetFileChange(type));
    }
    bindAssetDnD(type);
  });

  // สลับฟิลด์ตามประเภท + อัปเดตพรีวิว
  const typeSel = document.getElementById("data_type");
  typeSel.addEventListener("change", () => {
    toggleTypeGroup();
    updateDataPreviewOnly();
    debouncedUpdatePreview();
  });
  toggleTypeGroup();
  updateDataPreviewOnly();

  // ผูกอินพุตทั้งหมดใน #data_type_fields ให้เด้งพรีวิว
  document
    .querySelectorAll(
      "#data_type_fields input, #data_type_fields textarea, #data_type_fields select"
    )
    .forEach((el) => {
      el.addEventListener("input", debouncedUpdatePreview);
      el.addEventListener("change", debouncedUpdatePreview);
    });

  // ตอน submit ให้ประกอบข้อมูลอีกครั้ง เผื่อผู้ใช้เปลี่ยนก่อนกด
  document.getElementById("qrForm").addEventListener("submit", () => {
    updateDataPreviewOnly();
  });

  // Modal โลโก้
  document.getElementById("openLogoPicker").onclick = function () {
    document.getElementById("logoGalleryModal").style.display = "flex";
  };
  document.getElementById("closeLogoPicker").onclick = function () {
    document.getElementById("logoGalleryModal").style.display = "none";
  };
  document.querySelector(".logo-gallery-overlay").onclick = function () {
    document.getElementById("logoGalleryModal").style.display = "none";
  };

  // เปิด/ปิด สีที่สอง และอัปเดตพรีวิว
  document.getElementById("fill_style")?.addEventListener("change", () => {
    toggleColor2();
    debouncedUpdatePreview();
  });
  toggleColor2();

  // เลือกโลโก้จากแกลเลอรี
  document.getElementById("logoGalleryList").onclick = function (e) {
    const thumb = e.target.closest(".logo-thumb");
    if (!thumb) return;
    document
      .querySelectorAll("#logoGalleryList .logo-thumb")
      .forEach((el) => el.classList.remove("active"));
    thumb.classList.add("active");
    const logoName = thumb.dataset.logo || "";
    document.getElementById("logoInput").value = logoName;

    const preview = document.getElementById("logoMainPreview");
    if (logoName && thumb.querySelector("img")) {
      preview.innerHTML = `<img src="${
        thumb.querySelector("img").src
      }" alt="logo big">`;
    } else {
      preview.innerHTML = "ลาก & ปล่อยไฟล์โลโก้ที่นี่ (รองรับ .png / .jpg)";
    }
    document.getElementById("logoGalleryModal").style.display = "none";
    debouncedUpdatePreview();
  };

  // ฟอร์ม: debounce
  const form = document.getElementById("qrForm");
  form.addEventListener("input", debouncedUpdatePreview);
  form.addEventListener("change", debouncedUpdatePreview);

  // เมื่อเปลี่ยนขนาด QR ให้ประเมินคำแนะนำโลโก้ใหม่ ถ้ามีไฟล์เลือกอยู่
  const sizeSel = document.getElementById("size_px");
  if (sizeSel) {
    sizeSel.addEventListener("change", () => {
      const fi = document.getElementById("logoUpload");
      if (fi.files.length) analyzeAndPreviewLocalLogo(fi.files[0]);
    });
  }

  // Dropzone: drag & drop ลงกล่องพรีวิวโลโก้
  const dz = document.getElementById("logoMainPreview");
  ["dragenter", "dragover"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dz.classList.add("drag-over");
    })
  );
  ["dragleave", "dragend"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      dz.classList.remove("drag-over");
    })
  );
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    e.stopPropagation();
    dz.classList.remove("drag-over");
    const files = e.dataTransfer?.files || [];
    if (!files.length) return;
    const file = files[0];
    analyzeAndPreviewLocalLogo(file);
    updateUploadButtonState(true);

    // เสนอให้อัปโหลดทันที
    // const ok = confirm(`อัปโหลดโลโก้ "${file.name}" เลยไหม?`);
    // if (ok) uploadLogoWithFile(file);
  });

  // โหลดครั้งแรก: แสดงพรีวิว
  updatePreview();
});

/* ลบโลโก้ */
function confirmDeleteLogo(logoName) {
  if (!logoName) return;
  const ok = confirm(`คุณแน่ใจหรือไม่ว่าต้องการลบโลโก้ "${logoName}" ?`);
  if (!ok) return;
  fetch(`/delete_logo/${encodeURIComponent(logoName)}`, {
    method: "DELETE",
  }).then((res) => {
    if (res.ok) {
      alert("ลบโลโก้เรียบร้อยแล้ว");
      location.reload();
    } else {
      alert("ไม่สามารถลบโลโก้ได้");
    }
  });
}
