import time
import pandas as pd
import streamlit as st

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# TITLE
st.title("IGI Diamond Automation")

# FILE UPLOAD
uploaded_file = st.file_uploader(
    "Upload Excel File",
    type=["xlsx"]
)

if uploaded_file and "processed" not in st.session_state:

    # READ EXCEL
    df = pd.read_excel(uploaded_file)

    st.write(df)

    # RESULTS
    results = []

    # CHROME OPTIONS
    options = webdriver.ChromeOptions()
    options.page_load_strategy = "eager"

    options.add_argument("--start-maximized")

    options.add_argument("--disable-blink-features=AutomationControlled")

    options.add_experimental_option(
        "excludeSwitches",
        ["enable-automation"]
    )

    options.add_experimental_option(
        "useAutomationExtension",
        False
    )

    # OPEN BROWSER
    browser = webdriver.Chrome(
        service=Service(
            ChromeDriverManager().install()
        ),
        options=options
    )

    time.sleep(10)

    browser.set_page_load_timeout(40)

    # LOOP ALL LG NUMBERS
    total_records = len(df)

    progress_bar = st.progress(0)

    # progress_text = st.empty()

    for index, cert in enumerate(df["LG Number"]):

        cert_original = str(cert).strip()

        try:

            # DIRECT REPORT URL
            url = f"https://www.igi.org/verify-your-report/?r={cert_original}"

            browser.get(url)

            # INITIAL WAIT
            time.sleep(5)

            # WAIT UNTIL CLOUDFLARE FINISHES
            while True:

                page_text_check = browser.find_element(
                    By.TAG_NAME,
                    "body"
                ).text

                if (
                    "Performing security verification" not in page_text_check
                    and
                    "Verify you are human" not in page_text_check
                ):
                    break

                st.warning(
                    f"Complete Cloudflare verification for {cert_original}"
                )

                time.sleep(1)

            # EXTRA WAIT AFTER VERIFY
            time.sleep(1)

            # GET FULL PAGE TEXT
            page_text = browser.find_element(
                By.TAG_NAME,
                "body"
            ).text

            # DEBUG OUTPUT
            # st.text(page_text[:3000])

            # DEFAULT VALUES
            shape = ""
            carat = ""
            color = ""
            clarity = ""
            growth_type = ""

            # SPLIT PAGE LINES
            lines = page_text.split("\n")

            # LOOP THROUGH TEXT
            for i, line in enumerate(lines):

                line = line.strip()

                # SHAPE
                if "Shape and Cutting Style" in line:
                    try:
                        shape = lines[i + 1]
                    except:
                        pass

                # MEASUREMENTS
                if "Measurements" in line:
                    try:
                        measurements = lines[i + 1]
                    except:
                        pass

                # CARAT
                if "Carat Weight" in line:
                    try:
                        carat = lines[i + 1]

                        carat = ''.join(c for c in carat if c.isdigit() or c == '.')
                    except:
                        pass

                # COLOR
                if "Color Grade" in line:
                    try:
                        color = lines[i + 1]
                    except:
                        pass

                # CLARITY
                if "Clarity Grade" in line:
                    try:
                        clarity = lines[i + 1]

                        clarity = clarity.replace(" ", "")
                    except:
                        pass

                # CVD / HPHT
                if "CVD" in line.upper():
                    growth_type = "CVD"
                
                elif "HPHT" in line.upper():
                    growth_type = "HPHT"  
                    
                        # RETRY IF DATA EMPTY
            if shape == "" and carat == "" and color == "":

                time.sleep(3)

                page_text = browser.execute_script(
                    "return document.body.innerText;"
                )

                lines = page_text.split("\n")

                for i, line in enumerate(lines):

                    line = line.strip()

                    # SHAPE
                    if "Shape and Cutting Style" in line:
                        try:
                            shape = lines[i + 1]
                        except:
                            pass

                    # CARAT
                    if "Carat Weight" in line:
                        try:
                            carat = ''.join(
                                c for c in lines[i + 1]
                                if c.isdigit() or c == '.'
                            )
                        except:
                            pass

                    # COLOR
                    if "Color Grade" in line:
                        try:
                            color = lines[i + 1]
                        except:
                            pass              

            # SAVE RESULTS
            results.append({
                "LG Number": cert_original,
                "Shape": shape,
                "Carat": carat,
                "Color": color,
                "Clarity": clarity,
                "Growth Type": growth_type
            })

            progress_percent = int(((index + 1) / total_records) * 100)

            progress_bar.progress((index + 1) / total_records, text=f"{progress_percent}% Completed | Processing : {cert_original}")

            

        except Exception as e:

            st.error(f"ERROR : {cert_original}")

            st.write(e)

            time.sleep(5)

            try:

                browser.get(url)

                time.sleep(5)

                page_text = browser.execute_script("return document.body.innerText")

            except:
                pass    

            # EMPTY VALUES
            results.append({
                "LG Number": cert_original,
                "Shape": "",
                "Carat": "",
                "Color": "",
                "Clarity": "",
                "Growth Type": ""
            })

    # CLOSE BROWSER
    browser.quit()

    # OUTPUT DATAFRAME
    output_df = pd.DataFrame(results)

    st.write("Final Output")

    st.dataframe(output_df)

    # SAVE EXCEL
    output_file = "diamond_output.xlsx"

    output_df.to_excel(
        output_file,
        index=False
    )

    st.session_state.processed = True

    # DOWNLOAD BUTTON
    with open(output_file, "rb") as file:

        st.download_button(
            label="Download Excel",
            data=file,
            file_name="diamond_output.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )



