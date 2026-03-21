import time
import pandas as pd
import os
import sys
import re
from datetime import datetime, timezone, timedelta

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- CONFIGURATION ---
script_dir = os.path.dirname(os.path.abspath(__file__))
CSV_FILE_PATH = os.path.abspath(os.path.join(script_dir, "..", "Data", "step2_listings.csv"))
BASE_URL = "https://www.dubizzle.com.eg/en/"
SEARCH_URL = "https://www.dubizzle.com.eg/en/vehicles/cars-for-sale/q-cars/"
MAX_PAGES = 200   

# --- CONFIGURATION ---
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
CSV_FILE_PATH = os.path.abspath(os.path.join(script_dir, "..", "Data", "step2_listings.csv"))

BASE_URL = "https://www.dubizzle.com.eg"
SEARCH_URL = "https://www.dubizzle.com.eg/en/vehicles/cars-for-sale/q-cars/"
MAX_PAGES = 200   

def get_chrome_options():
    """Sets up Chrome to run invisibly on a server without a screen."""
    options = Options()
    options.binary_location = '/usr/bin/chromium-browser' # Explicitly point to Linux Chrome
    options.add_argument('--headless') 
    options.add_argument('--no-sandbox') 
    options.add_argument('--disable-dev-shm-usage') 
    return options

# Create a Service object pointing to the Linux ChromeDriver
chrome_service = Service('/usr/bin/chromedriver')

# --- 1. INITIAL DRIVER TEST ---
print("Testing Chrome WebDriver...")
try:
    test_driver = webdriver.Chrome(service=chrome_service, options=get_chrome_options())
    test_driver.get("https://www.google.com")
    print(f"Driver Success! Connected to: {test_driver.title}")
    test_driver.quit()
except Exception as e:
    print(f"CRITICAL ERROR: WebDriver failed to initialize.")
    print(f"Error Details: {e}")
    print("Stopping script.")
    sys.exit(1)

# --- 2. LOAD EXISTING DATA ---
active_list = []
if os.path.exists(CSV_FILE_PATH):
    try:
        df = pd.read_csv(CSV_FILE_PATH)
        active_list = list(df.listing_url)
        print(f"Loaded {len(active_list)} existing URLs from CSV.")
    except Exception as e:
        print(f"Warning: Could not read CSV. Starting fresh. Error: {e}")
else:
    print(f"No existing CSV found at {CSV_FILE_PATH}. Starting fresh.")

# --- 3. HELPER FUNCTIONS ---
def compute_listing_date(scraped_at, text):
    if not text: return None
    text = text.lower()
    if "today" in text or "hour" in text: return scraped_at.date()
    if "yesterday" in text: return (scraped_at - timedelta(days=1)).date()
    if "day" in text:
        match = re.search(r"\d+", text)
        if match: return (scraped_at - timedelta(days=int(match.group()))).date()
    if "week" in text:
        match = re.search(r"\d+", text)
        if match: return (scraped_at - timedelta(days=7 * int(match.group()))).date()
    if "month" in text:
        match = re.search(r"\d+", text)
        if match: return (scraped_at - timedelta(days=30 * int(match.group()))).date()
    return None

def extract_specs_dict(driver):
    specs = {}
    rows = driver.find_elements(By.XPATH, "//div[.//span]")
    for row in rows:
        try:
            spans = row.find_elements(By.XPATH, ".//span")
            if len(spans) < 2: continue
            key = spans.text.strip().lower()
            value = spans[-1].text.strip()
            if key and value: specs[key] = value
        except:
            continue
    return specs

# --- 4. SCRAPE LISTING URLS ---
driver = webdriver.Chrome(options=get_chrome_options())
listing_urls = set()

for page in range(1, MAX_PAGES + 1):
    page_url = f"{SEARCH_URL}?page={page}"
    print(f"Scraping page {page}: {page_url}")
    driver.get(page_url)
    
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "article"))
        )
        listings = driver.find_elements(By.CSS_SELECTOR, "article")
        for listing in listings:
            try:
                link_el = listing.find_element(By.XPATH, ".//a[contains(@href,'/ad/')]")
                href = link_el.get_attribute("href")
                if href.startswith("http"):
                    listing_urls.add(href)
                else:
                    listing_urls.add(BASE_URL + href)
            except:
                pass
    except Exception as e:
        print(f"Timeout or error on page {page}. Moving on.")

print("New URLs collected:", len(listing_urls))
listing_urls = list(set(active_list) | set(listing_urls))
print("Total URLs:", len(listing_urls))

# --- 5. SCRAPE INDIVIDUAL CAR DATA ---
step2_data = []
for i, url in enumerate(listing_urls, start=1):
    print(f"Scraping listing {i}/{len(listing_urls)}")
    try:
        driver.get(url)
        time.sleep(2)
        current_url = driver.current_url

        # Detect inactive listing
        if "/ad/" not in current_url:
            listing_id = url.split("/")[-1].replace(".html", "").split("-")[-1]
            step2_data.append({
                "listing_id": listing_id, "listing_url": url, "price": None,
                "mileage": None, "city": None, "listing_date": None,
                "seller_type": None, "scraped_at": datetime.now(timezone.utc), "active": False
            })
            print("Listing inactive")
            continue

        active = True
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(2)

        specs = extract_specs_dict(driver)
        brand = specs.get("brand")
        model = specs.get("model")
        year = specs.get("year")
        fuel = specs.get("fuel type")
        transmission = specs.get("transmission type")
        body = specs.get("body type")
        engine = specs.get("engine capacity (cc)")

        price, mileage, city, seller_type, listing_age = None, None, None, None, None

        try:
            price_text = driver.find_element(By.XPATH, "//span[contains(text(),'EGP')]").text
            price = int(re.sub(r"[^\d]", "", price_text))
        except: pass

        try:
            mileage_text = driver.find_element(By.XPATH, "//span[contains(text(),'km')]").text
            mileage = int(re.sub(r"[^\d]", "", mileage_text))
        except: pass

        try:
            city = driver.find_element(By.XPATH, "//*[@aria-label='Location']").text
        except: pass

        try:
            seller_text = driver.find_element(By.XPATH, "//*[contains(text(),'Listed by')]").text.lower()
            seller_type = "agency" if "agency" in seller_text else "private user"
        except: pass

        try:
            listing_age = driver.find_element(By.XPATH, "//span[contains(text(),'ago') or contains(text(),'Yesterday') or contains(text(),'Today')]").text
        except: pass

        scraped_at = datetime.now(timezone.utc)
        listing_date = compute_listing_date(scraped_at, listing_age)
        listing_id = url.split("/")[-1].replace(".html", "").split("-")[-1]

        step2_data.append({
            "listing_id": listing_id, "listing_url": url, "brand": brand, "model": model,
            "year": year, "fuel_type": fuel, "transmission": transmission, "body_type": body,
            "engine_capacity": engine, "price": price, "mileage": mileage, "city": city,
            "listing_date": listing_date, "seller_type": seller_type, "scraped_at": scraped_at, "active": active
        })
        print("Success")

    except Exception as e:
        print("Failed:", e)

driver.quit()

# --- 6. DATA PROCESSING AND SAVING ---
if not step2_data:
    print("No data was scraped. Exiting.")
    sys.exit(0)

df_step2 = pd.DataFrame(step2_data)
df_step2["year"] = df_step2["year"].astype('Int64')

cols = ["brand", "model", "year", "fuel_type", "transmission", "body_type", "engine_capacity"]
df_step2["vehicle_id"] = df_step2[cols].astype(str).agg("_".join, axis=1)

df_step2 = df_step2.set_index("vehicle_id")
df_step2 = df_step2.drop(columns=cols, errors='ignore')

df_step2["scraped_at"] = df_step2["scraped_at"].dt.strftime("%Y-%m-%d %H:%M:%S")

# Check for inactive ads BEFORE saving
df_step2.loc[df_step2["mileage"].isna() & df_step2["seller_type"].isna(), "active"] = False

# Save to CSV
file_exists = os.path.isfile(CSV_FILE_PATH)
df_step2.to_csv(CSV_FILE_PATH, mode='a', header=not file_exists, index=False)

print(f"Total listings scraped and processed: {len(df_step2)}")
print(f"Data appended to {CSV_FILE_PATH}")