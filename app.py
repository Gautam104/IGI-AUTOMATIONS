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
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException


# ==========================================
# TEMPORARY DEBUG CODE
# ==========================================

st.write("Chromium:", shutil.which("chromium"))
st.write("Chromedriver:", shutil.which("chromedriver"))

st.write("Chromedriver glob:")
st.write(glob.glob("/usr/lib/chromium*/**/chromedriver", recursive=True))
st.write(glob.glob("/usr/bin/*chromedriver*"))

st.write("Chromium glob:")
st.write(glob.glob("/usr/lib/chromium*/*"))
# ----------------------------------------------------------------------
# PAGE CONFIG
# ----------------------------------------------------------------------
st.set_page_config(page_title="IGI Diamond Automation", layout="wide")
st.title("IGI Diamond Automation")

st.caption(
    "Upload an Excel file with a column named **LG Number** containing "
    "9-digit IGI certificate numbers."
)

# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------

CLOUDFLARE_MARKERS = ["Performing security verification", "Verify you are human", "Just a moment"]


def _first_existing(paths):
    for path in paths:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def find_chromium_binary():
    """
    Locate a usable Chromium/Chrome binary installed via packages.txt.
    Checks fixed paths first, then PATH, then a filesystem glob as a
    last resort (covers versioned/alternate install locations).
    """
    candidates = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ]
    found = _first_existing(candidates)
    if found:
        return found

    for name in ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable"]:
        found = shutil.which(name)
        if found:
            return found

    for pattern in ["/usr/lib/chromium*/chromium*", "/usr/lib/chromium-browser/chromium*"]:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]

    return None


def find_chromedriver_binary():
    """
    Locate the chromedriver installed via packages.txt (chromium-driver).
    Deliberately does NOT fall back to letting Selenium/Selenium Manager
    auto-download a driver -- on Streamlit Cloud that downloads a binary
    with a mismatched architecture/glibc that fails with exit code 127.
    """
    candidates = [
        "/usr/bin/chromedriver",
        "/usr/lib/chromium-browser/chromedriver",
        "/usr/lib/chromium/chromedriver",
    ]
    found = _first_existing(candidates)
    if found:
        return found

    found = shutil.which("chromedriver")
    if found:
        return found

    matches = glob.glob("/usr/lib/chromium*/chromedriver")
    if matches:
        return matches[0]

    return None


def diagnose_missing_browser():
    """
    Build a clear diagnostic message when Chromium/chromedriver aren't
    found, so the fix is obvious instead of a raw stack trace.
    """
    lines = ["Chromium/chromedriver were not found on this server.", ""]
    try:
        dpkg_out = subprocess.run(
            ["dpkg", "-l", "chromium", "chromium-driver"],
            capture_output=True, text=True, timeout=5,
        )
        lines.append("dpkg status:")
        lines.append(dpkg_out.stdout or dpkg_out.stderr)
    except Exception as e:
        lines.append(f"Could not run dpkg check: {e}")
    return "\n".join(lines)


@st.cache_resource(show_spinner=False)
def get_browser():
    """
    Build a single headless Chrome/Chromium session, reused across the
    whole batch run. Cached as a resource so Streamlit doesn't relaunch
    a browser on every rerun.
    """
    chrome_binary = find_chromium_binary()
    driver_binary = find_chromedriver_binary()

    # IMPORTANT: do not silently fall back to letting Selenium/Selenium
    # Manager auto-download a driver. On Streamlit Cloud that downloads a
    # binary into ~/.cache/selenium/... that does not match the
    # container's architecture and fails with "unexpectedly exited,
    # status code 127". If packages.txt installed things correctly,
    # chrome_binary/driver_binary should both be found above -- if not,
    # fail loudly here with a diagnosis instead of limping into a broken
    # auto-downloaded driver.
    if not chrome_binary or not driver_binary:
        raise RuntimeError(
            "chrome_binary="
            + str(chrome_binary)
            + ", driver_binary="
            + str(driver_binary)
            + "\n\n"
            + diagnose_missing_browser()
        )

    options = webdriver.ChromeOptions()
    options.page_load_strategy = "eager"

    # Headless is mandatory on a server with no display
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    options.binary_location = chrome_binary
    service = Service(executable_path=driver_binary)

    browser = webdriver.Chrome(service=service, options=options)
    browser.set_page_load_timeout(40)
    return browser


def page_has_cloudflare(browser) -> bool:
    try:
        body_text = browser.find_element(By.TAG_NAME, "body").text
    except Exception:
        return False
    return any(marker in body_text for marker in CLOUDFLARE_MARKERS)


def wait_for_report_data(browser, timeout=15):
    """
    Wait until either the report data has rendered or we hit timeout,
    instead of always sleeping the full fixed duration. Returns the
    page text as soon as a known field label shows up.
    """
    try:
        WebDriverWait(browser, timeout).until(
            lambda b: any(
                marker in b.find_element(By.TAG_NAME, "body").text
                for marker in ["Shape and Cutting Style", "Carat Weight", "Color Grade"]
            )
            or page_has_cloudflare(b)
        )
    except TimeoutException:
        pass
    return browser.find_element(By.TAG_NAME, "body").text


def parse_report(page_text: str) -> dict:
    shape, carat, color, clarity, growth_type = "", "", "", "", ""
    lines = [line.strip() for line in page_text.split("\n")]

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
        "Shape": shape,
        "Carat": carat,
        "Color": color,
        "Clarity": clarity,
        "Growth Type": growth_type,
    }


# ----------------------------------------------------------------------
# SESSION STATE
# ----------------------------------------------------------------------
if "processed" not in st.session_state:
    st.session_state.processed = False
if "results" not in st.session_state:
    st.session_state.results = []
if "cloudflare_block" not in st.session_state:
    st.session_state.cloudflare_block = False

# ----------------------------------------------------------------------
# FILE UPLOAD
# ----------------------------------------------------------------------
uploaded_file = st.file_uploader("Upload Excel File", type=["xlsx"])

if uploaded_file and not st.session_state.processed:
    df = pd.read_excel(uploaded_file)

    if "LG Number" not in df.columns:
        st.error("The uploaded file must contain a column named 'LG Number'.")
        st.stop()

    st.write(df)

    start_clicked = st.button("Start Fetching", type="primary")

    if start_clicked:
        try:
            browser = get_browser()
        except RuntimeError as e:
            st.error(
                "Chromium or chromedriver could not be found on this server. "
                "This means 'packages.txt' either wasn't picked up or didn't "
                "install correctly. In the Streamlit Cloud dashboard: open "
                "your app's menu -> 'Reboot app'. If that doesn't help, open "
                "'Manage app' -> check the build logs for an 'apt-get' error "
                "during the packages.txt install step."
            )
            st.code(str(e))
            st.stop()
        except WebDriverException as e:
            st.error(
                "Chromium/chromedriver were found but the browser still failed "
                "to start. This is usually a container resource issue "
                "(out of memory) rather than a missing-package issue."
            )
            st.exception(e)
            st.stop()

        results = []
        total_records = len(df)
        progress_bar = st.progress(0)
        status_box = st.empty()
        cloudflare_notice = st.empty()

        for index, cert in enumerate(df["LG Number"]):
            cert_original = str(cert).strip()
            url = f"https://www.igi.org/verify-your-report/?r={cert_original}"

            try:
                browser.get(url)

                # Give Cloudflare a brief moment, then check once rather
                # than sleeping a fixed amount regardless of need.
                time.sleep(1.5)

                if page_has_cloudflare(browser):
                    # A real Cloudflare "verify you are human" challenge
                    # cannot be solved by code on a headless cloud server
                    # (no display, no human to click it). We stop the
                    # batch here rather than spin forever, and surface a
                    # clear message so you know a manual restart/check is
                    # needed -- this matches the trade-off you confirmed
                    # (stay cloud-hosted, accept occasional manual restarts).
                    st.session_state.cloudflare_block = True
                    cloudflare_notice.error(
                        f"Cloudflare verification triggered at certificate "
                        f"{cert_original} ({index + 1}/{total_records}). "
                        "This can't be solved automatically on a headless cloud "
                        "server. Please wait a few minutes and click 'Start "
                        "Fetching' again -- already-fetched rows below are kept."
                    )
                    break

                page_text = wait_for_report_data(browser, timeout=15)
                parsed = parse_report(page_text)

                # One retry if everything came back empty (page was slow)
                if not parsed["Shape"] and not parsed["Carat"] and not parsed["Color"]:
                    time.sleep(2)
                    page_text = browser.execute_script("return document.body.innerText;")
                    parsed = parse_report(page_text)

                results.append({"LG Number": cert_original, **parsed})

            except Exception as e:
                status_box.warning(f"Error on {cert_original}: {e}")
                results.append(
                    {
                        "LG Number": cert_original,
                        "Shape": "",
                        "Carat": "",
                        "Color": "",
                        "Clarity": "",
                        "Growth Type": "",
                    }
                )

            progress_percent = (index + 1) / total_records
            progress_bar.progress(
                progress_percent,
                text=f"{int(progress_percent * 100)}% completed | Processing: {cert_original}",
            )

        st.session_state.results = results
        st.session_state.processed = True
        st.rerun()

# ----------------------------------------------------------------------
# OUTPUT
# ----------------------------------------------------------------------
if st.session_state.processed and st.session_state.results:
    output_df = pd.DataFrame(st.session_state.results)

    st.subheader("Final Output")
    st.dataframe(output_df)

    output_file = "/tmp/diamond_output.xlsx"
    output_df.to_excel(output_file, index=False)

    with open(output_file, "rb") as file:
        st.download_button(
            label="Download Excel",
            data=file,
            file_name="diamond_output.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    if st.button("Process a new file"):
        st.session_state.processed = False
        st.session_state.results = []
        st.session_state.cloudflare_block = False
        st.rerun()
