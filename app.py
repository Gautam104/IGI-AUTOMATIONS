import os
import time
import subprocess
import pandas as pd
import streamlit as st

# ─────────────────────────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="IGI Diamond Automation", layout="wide")
st.title("💎 IGI Diamond Automation")
st.caption(
    "Upload an Excel file with a column named **LG Number** "
    "containing 9-digit IGI certificate numbers."
)

# ─────────────────────────────────────────────────────────────────────────────
# PLAYWRIGHT BROWSER INSTALL
# Runs once per container. Installs Chromium into ~/.cache/ms-playwright/
# No root needed — user-level install.
# packages.txt provides the system shared libraries Chromium needs
# (libglib2.0-0, libnss3, etc.) — those ARE installed at build time by
# Streamlit Cloud's apt step which runs as root before the app starts.
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def ensure_playwright_browser():
    # Always re-run install to make sure browser binary + libs are in sync
    result = subprocess.run(
        ["playwright", "install", "chromium"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False, f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    return True, "✅ Playwright Chromium ready."


# ─────────────────────────────────────────────────────────────────────────────
# SCRAPING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

CLOUDFLARE_MARKERS = [
    "Performing security verification",
    "Verify you are human",
    "Just a moment",
]


def has_cloudflare(page) -> bool:
    try:
        return any(m in page.content() for m in CLOUDFLARE_MARKERS)
    except Exception:
        return False


def wait_for_report(page, timeout=15000) -> str:
    try:
        page.wait_for_function(
            """() => {
                const t = document.body.innerText;
                return t.includes('Shape and Cutting Style') ||
                       t.includes('Carat Weight') ||
                       t.includes('Color Grade');
            }""",
            timeout=timeout,
        )
    except Exception:
        pass
    return page.inner_text("body")


def parse_report(page_text: str) -> dict:
    shape = carat = color = clarity = growth_type = ""
    lines = [l.strip() for l in page_text.split("\n")]

    for i, line in enumerate(lines):
        if "Shape and Cutting Style" in line and i + 1 < len(lines):
            shape = lines[i + 1]
        if "Carat Weight" in line and i + 1 < len(lines):
            carat = "".join(c for c in lines[i + 1] if c.isdigit() or c == ".")
        if "Color Grade" in line and i + 1 < len(lines):
            color = lines[i + 1]
        if "Clarity Grade" in line and i + 1 < len(lines):
            clarity = lines[i + 1].replace(" ", "")
        if "CVD" in line.upper():
            growth_type = "CVD"
        elif "HPHT" in line.upper():
            growth_type = "HPHT"

    return {
        "Shape": shape, "Carat": carat, "Color": color,
        "Clarity": clarity, "Growth Type": growth_type,
    }


# ─────────────────────────────────────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────────────────────────────────────
for key, default in [("processed", False), ("results", []), ("cf_block", False)]:
    if key not in st.session_state:
        st.session_state[key] = default

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Ensure browser is ready
# ─────────────────────────────────────────────────────────────────────────────
with st.spinner("Setting up browser (first run ~60 sec) …"):
    pw_ok, pw_msg = ensure_playwright_browser()

if not pw_ok:
    st.error("Browser setup failed. Details:")
    st.code(pw_msg)
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — File upload & fetch
# ─────────────────────────────────────────────────────────────────────────────
uploaded_file = st.file_uploader("Upload Excel File", type=["xlsx"])

if uploaded_file and not st.session_state.processed:
    df = pd.read_excel(uploaded_file)

    if "LG Number" not in df.columns:
        st.error("Column 'LG Number' not found. Check the header name is exactly 'LG Number'.")
        st.stop()

    st.dataframe(df)

    if st.button("▶ Start Fetching", type="primary"):

        from playwright.sync_api import sync_playwright

        results      = []
        total        = len(df)
        progress_bar = st.progress(0)
        status_slot  = st.empty()
        cf_slot      = st.empty()

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1920, "height": 1080},
                )
                page = context.new_page()

                for idx, cert in enumerate(df["LG Number"]):
                    cert = str(cert).strip()
                    url  = f"https://www.igi.org/verify-your-report/?r={cert}"

                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=40000)
                        time.sleep(1.5)

                        if has_cloudflare(page):
                            st.session_state.cf_block = True
                            cf_slot.error(
                                f"⚠️ Cloudflare challenge at {cert} "
                                f"({idx+1}/{total}). Wait a few minutes "
                                "then click 'Start Fetching' again."
                            )
                            break

                        page_text = wait_for_report(page, timeout=15000)
                        parsed    = parse_report(page_text)

                        if not parsed["Shape"] and not parsed["Carat"]:
                            time.sleep(2)
                            page_text = page.inner_text("body")
                            parsed    = parse_report(page_text)

                        results.append({"LG Number": cert, **parsed})

                    except Exception as e:
                        status_slot.warning(f"Error on {cert}: {e}")
                        results.append({
                            "LG Number": cert, "Shape": "", "Carat": "",
                            "Color": "", "Clarity": "", "Growth Type": "",
                        })

                    pct = (idx + 1) / total
                    progress_bar.progress(
                        pct,
                        text=f"{int(pct*100)}% | Processing: {cert}",
                    )

                browser.close()

        except Exception as e:
            st.error(f"Browser session error: {e}")
            st.stop()

        st.session_state.results   = results
        st.session_state.processed = True
        st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Output
# ─────────────────────────────────────────────────────────────────────────────
if st.session_state.processed and st.session_state.results:
    out_df = pd.DataFrame(st.session_state.results)
    st.subheader("✅ Final Output")
    st.dataframe(out_df)

    out_path = "/tmp/diamond_output.xlsx"
    out_df.to_excel(out_path, index=False)
    with open(out_path, "rb") as f:
        st.download_button(
            label="⬇️ Download Excel",
            data=f,
            file_name="diamond_output.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    if st.button("🔄 Process a new file"):
        st.session_state.processed = False
        st.session_state.results   = []
        st.session_state.cf_block  = False
        st.rerun()
