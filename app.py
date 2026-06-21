import os
import time
import glob
import shutil
import subprocess
import pandas as pd
import streamlit as st

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, WebDriverException

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
# CHROME INSTALLER
# ─────────────────────────────────────────────────────────────────────────────

CHROME_BIN       = "/usr/bin/google-chrome-stable"
CHROMEDRIVER_BIN = "/usr/local/bin/chromedriver"
CHROME_DEB_URL   = "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"


def run(cmd, **kwargs):
    """Run a shell command, return (returncode, stdout, stderr)."""
    r = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


@st.cache_resource(show_spinner=False)
def install_chrome():
    log   = []
    ok    = True

    # ── 1. Chrome ─────────────────────────────────────────────────────────
    if os.path.isfile(CHROME_BIN):
        log.append("✅ Chrome already installed.")
    else:
        log.append("⬇️ Downloading Google Chrome …")
        rc, out, err = run(["wget", "-q", "-O", "/tmp/chrome.deb", CHROME_DEB_URL])
        if rc != 0:
            return False, log + [f"❌ wget failed (rc={rc}): {err}"]

        log.append("📦 Installing Chrome (dpkg) …")
        rc, out, err = run(["dpkg", "-i", "/tmp/chrome.deb"])
        log.append(f"   dpkg rc={rc} | stdout: {out[:300]} | stderr: {err[:300]}")

        # Fix any broken deps
        log.append("🔧 Fixing dependencies …")
        rc2, out2, err2 = run(
            ["apt-get", "install", "-f", "-y"],
            env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        )
        log.append(f"   apt-get -f rc={rc2} | {err2[:300]}")

        if not os.path.isfile(CHROME_BIN):
            # Try alternate binary name
            alt = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
            if alt:
                log.append(f"ℹ️ Chrome found at alternate path: {alt}")
                # patch the constant for this run
                globals()["CHROME_BIN"] = alt
            else:
                # List what google-chrome dpkg actually installed
                rc3, out3, _ = run(["dpkg", "-L", "google-chrome-stable"])
                log.append(f"   dpkg -L output: {out3[:500]}")
                return False, log + ["❌ Chrome binary not found after dpkg + apt-get -f."]

        log.append("✅ Chrome installed.")

    # ── 2. ChromeDriver ───────────────────────────────────────────────────
    if os.path.isfile(CHROMEDRIVER_BIN):
        log.append("✅ ChromeDriver already installed.")
        return True, log

    # Get Chrome version
    rc, ver_out, _ = run([CHROME_BIN, "--version", "--no-sandbox"])
    raw_ver = ver_out.strip().split()[-1]   # e.g. "125.0.6422.76"
    major   = raw_ver.split(".")[0]
    log.append(f"🔎 Chrome version: {raw_ver}  (major={major})")

    # Fetch matching ChromeDriver from Chrome for Testing JSON
    import urllib.request, json
    try:
        url = "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.load(resp)

        driver_url = None
        for entry in reversed(data["versions"]):
            if entry["version"].startswith(major + "."):
                for dl in entry.get("downloads", {}).get("chromedriver", []):
                    if dl["platform"] == "linux64":
                        driver_url = dl["url"]
                        break
            if driver_url:
                break

        if not driver_url:
            return False, log + [f"❌ No ChromeDriver found for Chrome {major}."]

    except Exception as e:
        return False, log + [f"❌ Version lookup failed: {e}"]

    log.append(f"⬇️ Downloading ChromeDriver …")
    rc, _, err = run(["wget", "-q", "-O", "/tmp/chromedriver.zip", driver_url])
    if rc != 0:
        return False, log + [f"❌ wget chromedriver failed: {err}"]

    run(["unzip", "-o", "/tmp/chromedriver.zip", "-d", "/tmp/"])

    found = glob.glob("/tmp/chromedriver-linux64/chromedriver")
    if not found:
        found = glob.glob("/tmp/chromedriver*/chromedriver")
    if not found:
        return False, log + ["❌ Extracted chromedriver binary not found."]

    run(["cp", found[0], CHROMEDRIVER_BIN])
    run(["chmod", "+x", CHROMEDRIVER_BIN])

    if not os.path.isfile(CHROMEDRIVER_BIN):
        return False, log + ["❌ ChromeDriver not found after install."]

    log.append("✅ ChromeDriver installed.")
    return True, log


# ─────────────────────────────────────────────────────────────────────────────
# BROWSER FACTORY
# ─────────────────────────────────────────────────────────────────────────────

CLOUDFLARE_MARKERS = [
    "Performing security verification",
    "Verify you are human",
    "Just a moment",
]


@st.cache_resource(show_spinner=False)
def get_browser():
    options = webdriver.ChromeOptions()
    options.binary_location = CHROME_BIN
    options.page_load_strategy = "eager"
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    service = Service(executable_path=CHROMEDRIVER_BIN)
    browser = webdriver.Chrome(service=service, options=options)
    browser.set_page_load_timeout(40)
    return browser


# ─────────────────────────────────────────────────────────────────────────────
# SCRAPING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def page_has_cloudflare(browser) -> bool:
    try:
        body = browser.find_element(By.TAG_NAME, "body").text
        return any(m in body for m in CLOUDFLARE_MARKERS)
    except Exception:
        return False


def wait_for_report_data(browser, timeout=15) -> str:
    try:
        WebDriverWait(browser, timeout).until(
            lambda b: any(
                m in b.find_element(By.TAG_NAME, "body").text
                for m in ["Shape and Cutting Style", "Carat Weight", "Color Grade"]
            ) or page_has_cloudflare(b)
        )
    except TimeoutException:
        pass
    return browser.find_element(By.TAG_NAME, "body").text


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

    return {"Shape": shape, "Carat": carat, "Color": color,
            "Clarity": clarity, "Growth Type": growth_type}


# ─────────────────────────────────────────────────────────────────────────────
# SESSION STATE
# ─────────────────────────────────────────────────────────────────────────────
for key, default in [("processed", False), ("results", []), ("cf_block", False)]:
    if key not in st.session_state:
        st.session_state[key] = default

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Install Chrome (runs once per container, cached)
# ─────────────────────────────────────────────────────────────────────────────
with st.spinner("Checking Chrome installation …"):
    ok, install_log = install_chrome()

if not ok:
    st.error("Chrome could not be installed. Diagnostic output:")
    for line in install_log:
        st.write(line)
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — File upload & fetch
# ─────────────────────────────────────────────────────────────────────────────
uploaded_file = st.file_uploader("Upload Excel File", type=["xlsx"])

if uploaded_file and not st.session_state.processed:
    df = pd.read_excel(uploaded_file)

    if "LG Number" not in df.columns:
        st.error("Column 'LG Number' not found in the uploaded file.")
        st.stop()

    st.dataframe(df)

    if st.button("▶ Start Fetching", type="primary"):

        try:
            browser = get_browser()
        except (RuntimeError, WebDriverException) as e:
            st.error("Browser failed to start.")
            st.exception(e)
            st.stop()

        results      = []
        total        = len(df)
        progress_bar = st.progress(0)
        status_slot  = st.empty()
        cf_slot      = st.empty()

        for idx, cert in enumerate(df["LG Number"]):
            cert = str(cert).strip()
            url  = f"https://www.igi.org/verify-your-report/?r={cert}"

            try:
                browser.get(url)
                time.sleep(1.5)

                if page_has_cloudflare(browser):
                    st.session_state.cf_block = True
                    cf_slot.error(
                        f"⚠️ Cloudflare challenge at {cert} ({idx+1}/{total}). "
                        "Wait a few minutes then click 'Start Fetching' again."
                    )
                    break

                page_text = wait_for_report_data(browser, timeout=15)
                parsed    = parse_report(page_text)

                if not parsed["Shape"] and not parsed["Carat"]:
                    time.sleep(2)
                    page_text = browser.execute_script("return document.body.innerText;")
                    parsed    = parse_report(page_text)

                results.append({"LG Number": cert, **parsed})

            except Exception as e:
                status_slot.warning(f"Error on {cert}: {e}")
                results.append({"LG Number": cert, "Shape": "", "Carat": "",
                                 "Color": "", "Clarity": "", "Growth Type": ""})

            pct = (idx + 1) / total
            progress_bar.progress(pct, text=f"{int(pct*100)}% | {cert}")

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
