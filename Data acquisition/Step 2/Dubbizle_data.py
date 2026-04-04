import time
import pandas as pd
import os
import sys
import re
import json
from datetime import datetime, timezone, timedelta
import random
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc
from pyvirtualdisplay import Display

# --- START THE INVISIBLE MONITOR ---
print("Starting virtual display...")
display = Display(visible=0, size=(1280, 720))
display.start()

# --- CONFIGURATION ---
script_dir = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.now().strftime("%Y-%m-%d")

CSV_FILE_PATH = os.path.abspath(os.path.join(script_dir, "..", "Data", "step2_listings.csv"))
URLS_FILE = os.path.abspath(os.path.join(script_dir, "..", "Data", "all_listing_urls.json"))
PROGRESS_FILE = os.path.abspath(os.path.join(script_dir, "..", "Data", "scrape_progress.json"))
# Phase 1 uses a separate partial URLs file to accumulate URLs across restarts
PARTIAL_URLS_FILE = os.path.abspath(os.path.join(script_dir, "..", "Data", "partial_listing_urls.json"))
PHASE1_PAGE_FILE = os.path.abspath(os.path.join(script_dir, "..", "Data", "phase1_page.json"))

BASE_URL = "https://www.dubizzle.com.eg/en/"
SEARCH_URL = "https://www.dubizzle.com.eg/en/vehicles/cars-for-sale/q-cars/"
MAX_PAGES = 200
PHASE1_BATCH_PAGES = 50   # Restart script every 50 search pages in Phase 1
BATCH_SIZE = 150           # Phase 2: scrape 100 listings per batch

# --- CHECK IF THIS IS A NEW DAY ---
def is_file_from_today(filepath):
    """Check if a file was last modified today."""
    if not os.path.exists(filepath):
        return False
    file_date = datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m-%d")
    return file_date == TODAY

# If progress/url files exist but are from a previous day, delete them to start fresh
stale_files = [PROGRESS_FILE, URLS_FILE, PARTIAL_URLS_FILE, PHASE1_PAGE_FILE]
for f in stale_files:
    if os.path.exists(f) and not is_file_from_today(f):
        print(f"Found stale file from a previous day: {f}. Removing.")
        os.remove(f)

def get_chrome_options():
    options = uc.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--accept-lang=en-US,en')
    options.add_argument('--disable-gpu')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-background-networking')
    options.add_argument('--disable-default-apps')
    options.add_argument('--js-flags=--max-old-space-size=256')
    return options

# --- HELPER FUNCTIONS ---
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
    try:
        highlighted_box = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH, "//div[@aria-label='Highlighted Details']"))
        )
        items = highlighted_box.find_elements(By.XPATH, ".//div[span[2]]")
        for item in items:
            spans = item.find_elements(By.TAG_NAME, "span")
            key = spans[0].get_attribute("textContent").strip().lower()
            value = spans[-1].get_attribute("textContent").strip()
            if key and value:
                specs[key] = value
    except Exception as e:
        print(f"DEBUG: Highlighted Details not found. Error: {e}")

    try:
        details_box = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH, "//div[@aria-label='Details']"))
        )
        rows = details_box.find_elements(By.XPATH, ".//div[span[2] and not(span[3])]")
        for row in rows:
            spans = row.find_elements(By.TAG_NAME, "span")
            key = spans[0].get_attribute("textContent").strip().lower()
            value = spans[-1].get_attribute("textContent").strip()
            if key and value and key not in specs:
                specs[key] = value
    except Exception as e:
        print(f"DEBUG: Details box not found. Error: {e}")
    return specs

def save_progress(batch_start):
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"batch_start": batch_start}, f)
    print(f"Progress saved: next batch starts at index {batch_start}")

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            data = json.load(f)
            return data.get("batch_start", 0)
    return 0

def save_urls(urls):
    with open(URLS_FILE, "w") as f:
        json.dump(urls, f)
    print(f"Saved {len(urls)} URLs to {URLS_FILE}")

def load_urls():
    if os.path.exists(URLS_FILE):
        with open(URLS_FILE, "r") as f:
            return json.load(f)
    return None

def save_partial_urls(urls_set):
    """Save partially collected URLs during Phase 1 (accumulates across restarts)."""
    existing = set()
    if os.path.exists(PARTIAL_URLS_FILE):
        with open(PARTIAL_URLS_FILE, "r") as f:
            existing = set(json.load(f))
    combined = list(existing | urls_set)
    with open(PARTIAL_URLS_FILE, "w") as f:
        json.dump(combined, f)
    print(f"Saved {len(combined)} partial URLs ({len(urls_set)} new this batch)")

def load_partial_urls():
    if os.path.exists(PARTIAL_URLS_FILE):
        with open(PARTIAL_URLS_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_phase1_page(page):
    with open(PHASE1_PAGE_FILE, "w") as f:
        json.dump({"next_page": page}, f)

def load_phase1_page():
    if os.path.exists(PHASE1_PAGE_FILE):
        with open(PHASE1_PAGE_FILE, "r") as f:
            return json.load(f).get("next_page", 1)
    return 1

def cleanup_chrome():
    """Force kill all chrome/chromedriver processes and free memory."""
    os.system("pkill -9 -f chrome")
    os.system("pkill -9 -f chromedriver")
    
    # Nuke all corrupted Chrome lock files and leftover temp directories
    os.system("rm -rf /tmp/.com.google.Chrome.*")
    os.system("rm -rf /tmp/.org.chromium.Chromium.*")
    os.system("rm -rf /tmp/scoped_dir*")
    os.system("rm -rf /tmp/chrome_crashpad*")
    time.sleep(5)

def start_browser():
    """Start a fresh browser with retry logic."""
    for attempt in range(3):
        try:
            cleanup_chrome()  # Always clean BEFORE trying to start
            driver = uc.Chrome(options=get_chrome_options(), driver_executable_path='/usr/bin/chromedriver')
            return driver
        except Exception as e:
            print(f"Browser start attempt {attempt+1}/3 failed: {e}")
            cleanup_chrome()
            time.sleep(60 * (attempt + 1))  # 60s, 120s, 180s
    print("FATAL: Could not start browser after 3 attempts.")
    sys.exit(1)

def restart_script():
    """Fully restart this script to free all memory."""
    cleanup_chrome()
    time.sleep(30)
    print("Exiting for auto-restart...")
    sys.exit(42)  # Special exit code = "restart me"

def save_batch_data(step2_data):
    """Process and append a batch of scraped data to the CSV, deduplicating by listing_id."""
    if not step2_data:
        print("No data in this batch to save.")
        return

    df_step2 = pd.DataFrame(step2_data)

    print("Null counts per column:")
    print(df_step2.isnull().sum())

    df_step2["scrape_date"] = TODAY
    df_step2["year"] = df_step2["year"].astype('Int64')

    cols = ["brand", "model", "year", "fuel_type", "transmission", "body_type", "engine_capacity"]
    df_step2["vehicle_id"] = df_step2[cols].apply(
        lambda row: "_".join([str(val) if pd.notna(val) else "" for val in row]),
        axis=1
    )
    df_step2 = df_step2.set_index("vehicle_id")
    df_step2 = df_step2.drop(columns=cols, errors='ignore')

    df_step2["scraped_at"] = df_step2["scraped_at"].dt.strftime("%Y-%m-%d %H:%M:%S")

    df_step2.loc[df_step2["mileage"].isna() & df_step2["seller_type"].isna(), "active"] = False

    if os.path.isfile(CSV_FILE_PATH):
        try:
            df_existing = pd.read_csv(CSV_FILE_PATH)
            new_ids = set(df_step2["listing_id"].astype(str).tolist())
            df_existing = df_existing[~df_existing["listing_id"].astype(str).isin(new_ids)]
            df_combined = pd.concat([df_existing, df_step2.reset_index()], ignore_index=True)
            df_combined.to_csv(CSV_FILE_PATH, index=False)
            print(f"Updated CSV: {len(df_combined)} total rows ({len(df_step2)} new/updated)")
        except Exception as e:
            print(f"Warning: Could not dedup. Appending instead. Error: {e}")
            df_step2.to_csv(CSV_FILE_PATH, mode='a', header=False, index=True)
    else:
        df_step2.to_csv(CSV_FILE_PATH, index=True)
        print(f"Created CSV with {len(df_step2)} listings")


# ===================================================================
# PHASE 1: COLLECT LISTING URLS (in batches of 50 pages, with restarts)
# ===================================================================
listing_urls = load_urls()

if listing_urls is None:
    # Check where Phase 1 left off
    start_page = load_phase1_page()

    if start_page > MAX_PAGES:
        # Phase 1 page scraping is done — finalize the URL list
        print("=" * 60)
        print("PHASE 1 COMPLETE: Finalizing URL list...")
        print("=" * 60)

        listing_urls_set = load_partial_urls()

        # Load existing active URLs from CSV
        active_list = []
        if os.path.exists(CSV_FILE_PATH):
            try:
                df = pd.read_csv(CSV_FILE_PATH)
                if 'active' in df.columns:
                    latest_status = df.drop_duplicates(subset='listing_url', keep='last')
                    latest_status = latest_status[latest_status['active'] == True]
                    active_list = latest_status['listing_url'].dropna().tolist()
                else:
                    active_list = df['listing_url'].dropna().tolist()
                print(f"Loaded {len(active_list)} ACTIVE URLs from CSV to re-check today.")
            except Exception as e:
                print(f"Warning: Could not read CSV. Error: {e}")

        # Merge
        listing_urls = list(set(active_list) | listing_urls_set)
        save_urls(listing_urls)
        save_progress(0)
        print(f"Total unique URLs to scrape: {len(listing_urls)}")

        # Clean up Phase 1 temp files
        if os.path.exists(PARTIAL_URLS_FILE):
            os.remove(PARTIAL_URLS_FILE)
        if os.path.exists(PHASE1_PAGE_FILE):
            os.remove(PHASE1_PAGE_FILE)

    else:
        # Scrape the next batch of search pages
        end_page = min(start_page + PHASE1_BATCH_PAGES, MAX_PAGES + 1)
        print("=" * 60)
        print(f"PHASE 1: Scraping search pages {start_page} to {end_page - 1} of {MAX_PAGES}")
        print("=" * 60)

        driver = start_browser()
        listing_urls_set = set()
        start_search = time.time()

        for page in range(start_page, end_page):
            page_url = f"{SEARCH_URL}?page={page}"
            print(f"Scraping page {page}: {page_url}")

            try:
                driver.get(page_url)
            except Exception as e:
                print(f"Session dead on page {page}. Restarting browser...")
                try: driver.quit()
                except: pass
                cleanup_chrome()
                time.sleep(5)
                driver = start_browser()
                continue

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
                            listing_urls_set.add(href)
                        else:
                            listing_urls_set.add(BASE_URL + href)
                    except:
                        pass

            except Exception as e:
                print(f"Timeout on page {page}. Restarting browser...")
                try:
                    driver.save_screenshot(f"error_page_{page}.png")
                except:
                    pass
                try: driver.quit()
                except: pass
                cleanup_chrome()
                time.sleep(4)
                driver = start_browser()
                continue

            wait_time = random.uniform(5, 12)
            time.sleep(wait_time)

            if page % 20 == 0:
                print("Phase 1 Anti-Bot Flush: Restarting browser...")
                try: driver.quit()
                except: pass
                cleanup_chrome()
                time.sleep(random.uniform(10, 20))
                driver = start_browser()

        try: driver.quit()
        except: pass
        cleanup_chrome()

        end_search = time.time()
        print(f"--- Phase 1 batch done in {(end_search - start_search)/60:.2f} minutes ---")
        print(f"Found {len(listing_urls_set)} URLs in pages {start_page}-{end_page - 1}")

        # Save partial URLs and update page progress
        save_partial_urls(listing_urls_set)
        save_phase1_page(end_page)

        # Restart script to free memory and continue Phase 1
        print(f"Restarting script to continue Phase 1 from page {end_page}...")
        restart_script()


# ===================================================================
# PHASE 2: SCRAPE IN BATCHES OF {BATCH_SIZE}
# ===================================================================
batch_start = load_progress()

if batch_start >= len(listing_urls):
    print("=" * 60)
    print("ALL BATCHES COMPLETE! Nothing left to scrape.")
    print("=" * 60)
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
    if os.path.exists(URLS_FILE):
        os.remove(URLS_FILE)
    sys.exit(0)

batch_end = min(batch_start + BATCH_SIZE, len(listing_urls))
batch_urls = listing_urls[batch_start:batch_end]

print("=" * 60)
print(f"PHASE 2: Scraping batch {batch_start}-{batch_end} of {len(listing_urls)} total URLs")
print(f"This batch: {len(batch_urls)} listings")
print("=" * 60)

driver = start_browser()
step2_data = []
start_deep = time.time()

for i, url in enumerate(batch_urls, start=1):
    print(f"Scraping listing {i}/{len(batch_urls)} (global: {batch_start + i}/{len(listing_urls)})")
    try:
        driver.get(url)
        time.sleep(2)

        if i == 1:
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)

        current_url = driver.current_url

        # Detect inactive listing
        if "/ad/" not in current_url:
            listing_id = url.split("/")[-1].replace(".html", "").split("-")[-1]
            step2_data.append({
                "listing_id": listing_id, "listing_url": url, "price": None,
                "mileage": None, "city": None, "listing_date": None,
                "seller_type": None, "payment_options": None,
                "scraped_at": datetime.now(timezone.utc), "active": False
            })
            print("Listing inactive")
            continue

        active = True

        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//span[contains(text(),'EGP')]"))
            )
        except Exception as e:
            print(f"Listing {i} failed to load. Restarting browser...")
            try:
                driver.save_screenshot(f"car_error_{i}.png")
            except:
                pass
            try: driver.quit()
            except: pass
            cleanup_chrome()
            time.sleep(5)
            driver = start_browser()
            continue

        driver.execute_script("window.scrollBy(0, 2000);")
        time.sleep(3)

        specs = extract_specs_dict(driver)

        brand = specs.get("brand")
        model = specs.get("model")
        year = specs.get("year")
        fuel = specs.get("fuel type")
        transmission = specs.get("transmission type")
        body = specs.get("body type")
        engine = specs.get("engine capacity (cc)")
        payment_options = specs.get("payment options")
        condition = specs.get("condition")

        price, mileage, city, seller_type, listing_age = None, None, None, None, None

        km_val = specs.get("kilometers")
        if km_val:
            numbers = re.findall(r"\d+", km_val.replace(",", ""))
            if numbers:
                mileage = int(numbers[0])

        try:
            price_text = driver.find_element(By.XPATH, "//span[contains(text(),'EGP')]").text
            price = int(re.sub(r"[^\d]", "", price_text))
        except: pass

        if mileage is None:
            try:
                mileage_text = driver.find_element(By.XPATH, "//span[contains(text(),'km')]").text
                numbers = re.findall(r"\d+", mileage_text.replace(",", ""))
                if numbers:
                    mileage = int(numbers[0])
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
            "listing_date": listing_date, "condition": condition, "seller_type": seller_type,
            "payment_options": payment_options, "scraped_at": scraped_at, "active": active
        })
        print("Success")

    except Exception as e:
        error_msg = str(e)
        print(f"Failed: {error_msg}")

        if "HTTPConnectionPool" in error_msg or "not reachable" in error_msg or "refused" in error_msg or "tab crashed" in error_msg:
            print("Browser crashed! Forcing emergency reboot...")
            try: driver.quit()
            except: pass
            cleanup_chrome()

            for attempt in range(3):
                wait = 60 * (attempt + 1)
                print(f"Retry {attempt+1}/3: waiting {wait}s...")
                time.sleep(wait)
                try:
                    driver = start_browser()
                    break
                except:
                    cleanup_chrome()
            else:
                print("All retries failed. Saving data and exiting.")
                save_batch_data(step2_data)
                save_progress(batch_start + i)
                sys.exit(1)

    wait = random.uniform(8, 12)
    print(f"Humanizing: Waiting {wait:.1f}s before next car...")
    time.sleep(wait)

    # Anti-bot flush every 10 cars
    if i % 20 == 0:
        long_wait = random.uniform(20, 40)
        print(f"Anti-Bot Flush: Closing browser and resting for {long_wait:.1f} seconds...")
        try: driver.quit()
        except: pass
        cleanup_chrome()
        time.sleep(long_wait)
        print("Starting a fresh browser session...")
        driver = start_browser()

# --- BATCH COMPLETE ---
try: driver.quit()
except: pass
cleanup_chrome()

end_deep = time.time()
print(f"\n--- Batch {batch_start}-{batch_end} finished in {(end_deep - start_deep)/60:.2f} minutes ---")

# Save this batch's data
save_batch_data(step2_data)

# Update progress for next batch
next_batch_start = batch_end
save_progress(next_batch_start)

if next_batch_start >= len(listing_urls):
    print("=" * 60)
    print("ALL BATCHES COMPLETE!")
    print("=" * 60)
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
    if os.path.exists(URLS_FILE):
        os.remove(URLS_FILE)
else:
    remaining = len(listing_urls) - next_batch_start
    print("=" * 60)
    print(f"Batch done. {remaining} URLs remaining.")
    print("=" * 60)
    restart_script()