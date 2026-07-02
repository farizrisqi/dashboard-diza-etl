import streamlit as st
import subprocess
import sys
import os

st.set_page_config(page_title="Update Data DIZA", page_icon="🔄", layout="centered")

st.markdown("""
<style>
    .block-container { padding-top: 2rem; max-width: 680px; }
    .info-card {
        background: #f0f4ff;
        border-left: 4px solid #4f7cff;
        border-radius: 8px;
        padding: 1rem 1.2rem;
        margin-bottom: 1.5rem;
        font-size: 0.95rem;
        color: #333;
    }
    .info-card ul { margin: 0.4rem 0 0 1rem; padding: 0; }
    .info-card li { margin-bottom: 0.2rem; }
</style>
""", unsafe_allow_html=True)

st.title("🔄 Update Data Temuan DIZA")
st.markdown(
    "<div class='info-card'>"
    "Tombol di bawah akan mengambil data temuan terbaru dari website "
    "<b>PT. Dizamatra Powerindo</b> dan memperbarui dashboard secara otomatis.<br><br>"
    "<ul>"
    "<li>⏱️ Proses biasanya selesai dalam <b>1–3 menit</b></li>"
    "<li>📋 Jangan tutup halaman ini sampai proses selesai</li>"
    "</ul>"
    "</div>",
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner="Menyiapkan sistem, mohon tunggu...")
def install_playwright():
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True,
    )


install_playwright()


def get_env():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["CI"] = "true"
    for key in ["GOOGLE_CREDS_JSON", "SHEET_ID", "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "GROQ_API_KEY"]:
        if key in st.secrets:
            env[key] = str(st.secrets[key])
    return env


def run_script(label, script_path, env):
    st.markdown(f"**{label}**")
    output_box = st.empty()
    lines = []

    proc = subprocess.Popen(
        [sys.executable, script_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        encoding="utf-8",
        errors="replace",
    )
    for line in proc.stdout:
        lines.append(line.rstrip())
        output_box.code("\n".join(lines[-50:]))
    proc.wait()
    output_box.code("\n".join(lines))
    return proc.returncode


if st.button("🔄 Tarik Data Temuan Sekarang", type="primary", use_container_width=True):
    env = get_env()

    with st.status("Sedang mengambil data, mohon tunggu...", expanded=True) as status:
        rc1 = run_script("📥 Mengambil data dari website...", "etl/etl_diza_download.py", env)
        if rc1 != 0:
            status.update(label="❌ Gagal mengambil data dari website", state="error")
            st.error("Terjadi kesalahan saat mengambil data. Coba lagi beberapa saat.")
            st.stop()

        rc2 = run_script("📤 Memperbarui dashboard...", "etl/etl_diza_supabase.py", env)
        if rc2 != 0:
            status.update(label="❌ Gagal memperbarui dashboard", state="error")
            st.error("Terjadi kesalahan saat memperbarui dashboard. Coba lagi beberapa saat.")
            st.stop()

        status.update(label="✅ Data berhasil diperbarui!", state="complete")
        st.success("Data temuan DIZA sudah diperbarui. Silakan buka dashboard untuk melihat hasil terbaru.")
        st.balloons()
