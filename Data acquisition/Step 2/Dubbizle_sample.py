import time
import pandas as pd
import os
import sys
import re
from datetime import datetime, timezone, timedelta
import time
import random
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc

# --- CONFIGURATION ---
script_dir = os.path.dirname(os.path.abspath(__file__))
CSV_FILE_PATH = os.path.abspath(os.path.join(script_dir, "..", "Data", "step2_listings.csv"))
output_table = os.path.abspath(os.path.join(script_dir, "..", "Data", "step2_listings_sample.csv"))
BASE_URL = "https://www.dubizzle.com.eg/en/"
SEARCH_URL = "https://www.dubizzle.com.eg/en/vehicles/cars-for-sale/q-cars/"
MAX_PAGES = 2      

def get_chrome_options():
    # 1. Use the new 'uc' options instead of standard Selenium options
    options = uc.ChromeOptions()
    options.binary_location = '/usr/bin/chromium-browser' 
    
    # 2. Server Essentials (Notice we use '--headless=new' now)
    options.add_argument('--headless=new') 
    options.add_argument('--no-sandbox') 
    options.add_argument('--disable-dev-shm-usage') 
    options.add_argument('--disable-gpu')
    
    # 3. Force Desktop & English (To fix the Nulls)
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--accept-lang=en-US,en')
    
    # We completely deleted the User-Agent and Automation flags! 
    # undetected_chromedriver handles all of that automatically in the background.
    
    return options

# DELETE the old `chrome_service = Service(...)` line entirely. 
# undetected_chromedriver handles the executable path directly.

# --- 1. INITIAL DRIVER TEST ---
print("Testing Chrome WebDriver...")
try:
    test_driver = uc.Chrome(options=get_chrome_options(), driver_executable_path='/usr/bin/chromedriver')
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
driver = uc.Chrome(options=get_chrome_options(), driver_executable_path='/usr/bin/chromedriver')
listing_urls = set()
start_search = time.time()
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
        print(f"Timeout on page {page}. Moving on.")
        print(f"The page title is actually: {driver.title}") # This will likely say 'Just a moment...'
        driver.save_screenshot(f"error_page_{page}.png")     # Takes a picture of the block!
        break # Stops the loop so it doesn't do this 200 times
    wait_time = random.uniform(10, 20)
    time.sleep(wait_time)
end_search = time.time()
search_duration = end_search - start_search
print(f"--- Search Phase 1 Finished in {search_duration/60:.2f} minutes ---")

print("Total URLs:", len(listing_urls))

# --- 5. SCRAPE INDIVIDUAL CAR DATA ---
print(f"Starting deep scrape of {len(listing_urls)} listings...")
start_deep = time.time()
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
            # Wait up to 20 seconds specifically for the Price or the Specs to load
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//span[contains(text(),'EGP')]"))
            )
        except Exception as e:
            print(f"Listing {i} failed to load data in time (Possible bot block).")
            driver.save_screenshot(f"car_error_{i}.png")

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
end_deep = time.time()
deep_duration = end_deep - start_deep
print(f"\n--- Deep Scrape Finished in {deep_duration/60:.2f} minutes ---")
print(f"--- Total scraping runtime: {(end_deep - start_search)/60:.2f} minutes ---")
# --- 6. DATA PROCESSING AND SAVING ---
if not step2_data:
    print("No data was scraped. Exiting.")
    sys.exit(0)

df_step2 = pd.DataFrame(step2_data)
print(df_step2.isnull().sum())
print(df_step2)

df_step2["year"] = df_step2["year"].astype('Int64')

cols = ["brand", "model", "year", "fuel_type", "transmission", "body_type", "engine_capacity"]
df_step2["vehicle_id"] = df_step2[cols].apply(
    lambda row: "_".join([str(val) if pd.notna(val) else "" for val in row]), 
    axis=1
)
df_step2 = df_step2.set_index("vehicle_id")
df_step2 = df_step2.drop(columns=cols, errors='ignore')

df_step2["scraped_at"] = df_step2["scraped_at"].dt.strftime("%Y-%m-%d %H:%M:%S")

# Check for inactive ads BEFORE saving
df_step2.loc[df_step2["mileage"].isna() & df_step2["seller_type"].isna(), "active"] = False

# Save to CSV
file_exists = os.path.isfile(output_table)
df_step2.to_csv(output_table)

print(f"Total listings scraped and processed: {len(df_step2)}")
print(f"Data appended to {output_table}")
