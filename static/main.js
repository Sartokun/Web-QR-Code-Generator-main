// Coloris Picker Config
Coloris({
    el: '[data-coloris]',
    theme: 'large',
    themeMode: 'dark',
    format: 'hex',
    swatches: ['#222', '#fff', '#e53935', '#fb8c00', '#fdd835', '#43a047', '#1e88e5', '#8e24aa'],
    alpha: false,
});

// Logo upload
function uploadLogo() {
    let fileInput = document.getElementById("logoUpload");
    if (fileInput.files.length === 0) return alert("เลือกไฟล์ก่อน!");
    let formData = new FormData();
    formData.append("logo", fileInput.files[0]);
    fetch("/upload_logo", { method: "POST", body: formData })
        .then(resp => resp.text())
        .then(() => { alert("อัปโหลดโลโก้สำเร็จ!"); location.reload(); });
}

// Preview QR update
function updatePreview() {
    let form = document.getElementById("qrForm");
    let formData = new FormData(form);
    let logoInput = form.querySelector("input[name='logo']");
    formData.set('logo', logoInput.value);

    let transparentChecked = form.querySelector("#transparent").checked;
    formData.set('transparent', transparentChecked ? "1" : "");

    fetch("/preview_qr", { method: "POST", body: formData })
        .then(resp => resp.ok ? resp.blob() : null)
        .then(blob => {
            if (blob) {
                let url = URL.createObjectURL(blob);
                document.getElementById("qrPreview").src = url;

                // ----- แก้ background preview -----
                let qrBG = document.getElementById("qrPreviewBG");
                if (transparentChecked) {
                    qrBG.classList.add("qr-preview-bg");
                } else {
                    qrBG.classList.remove("qr-preview-bg");
                }
            }
        });
}
document.getElementById("qrForm").addEventListener("input", updatePreview);
document.getElementById("qrForm").addEventListener("change", updatePreview);
window.onload = updatePreview;

// เมื่อเลือกไฟล์อัปโหลด ให้โชว์ชื่อไฟล์
function showUploadFileName() {
    let fileInput = document.getElementById("logoUpload");
    let fileNameBox = document.getElementById("uploadFileName");
    if (fileInput.files.length) {
        fileNameBox.textContent = "ไฟล์ที่เลือก: " + fileInput.files[0].name;
    } else {
        fileNameBox.textContent = "";
    }
}

document.addEventListener("DOMContentLoaded", function() {
    document.getElementById('openLogoPicker').onclick = function () {
        document.getElementById('logoGalleryModal').style.display = 'flex';
    };
    document.getElementById('closeLogoPicker').onclick = function () {
        document.getElementById('logoGalleryModal').style.display = 'none';
    };
    document.querySelector('.logo-gallery-overlay').onclick = function () {
        document.getElementById('logoGalleryModal').style.display = 'none';
    };
    document.getElementById("logoGalleryList").onclick = function(e) {
        let thumb = e.target.closest(".logo-thumb");
        if (!thumb) return;
        document.querySelectorAll("#logoGalleryList .logo-thumb").forEach(e => e.classList.remove("active"));
        thumb.classList.add("active");
        let logoName = thumb.dataset.logo || "";
        document.getElementById("logoInput").value = logoName;
        let preview = document.getElementById("logoMainPreview");
        if (logoName && thumb.querySelector("img")) {
            let imgUrl = thumb.querySelector("img").src;
            preview.innerHTML = `<img src="${imgUrl}" alt="logo big">`;
        } else {
            preview.innerHTML = '<span style="color:#888;">(ไม่มีโลโก้)</span>';
        }
        document.getElementById('logoGalleryModal').style.display = 'none';
        updatePreview();
    };
});
