import time
import pandas as pd
import os
import sys
import re
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
import random
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import undetected_chromedriver as uc
from pyvirtualdisplay import Display

# --- START THE INVISIBLE MONITOR (only needed for Phase 1) ---
print("Starting virtual display...")
display = Display(visible=0, size=(1280, 720))
display.start()

# --- CONFIGURATION ---
script_dir = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.now().strftime("%Y-%m-%d")

CSV_FILE_PATH = os.path.abspath(os.path.join(script_dir, "..", "Data", "step2_listings.csv"))
URLS_FILE = os.path.abspath(os.path.join(script_dir, "..", "Data", "all_listing_urls.json"))
PROGRESS_FILE = os.path.abspath(os.path.join(script_dir, "..", "Data", "scrape_progress.json"))
PARTIAL_URLS_FILE = os.path.abspath(os.path.join(script_dir, "..", "Data", "partial_listing_urls.json"))
PHASE1_PAGE_FILE = os.path.abspath(os.path.join(script_dir, "..", "Data", "phase1_page.json"))

BASE_URL = "https://www.dubizzle.com.eg/en/"
SEARCH_URL = "https://www.dubizzle.com.eg/en/vehicles/cars-for-sale/q-cars/"
MAX_PAGES = 200
PHASE1_BATCH_PAGES = 50
BATCH_SIZE = 500  # Much larger batches now — requests uses almost no RAM

# --- HTTP SESSION FOR PHASE 2 ---
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.7680.164 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.dubizzle.com.eg/en/vehicles/cars-for-sale/",
    "DNT": "1",
    "Connection": "keep-alive",
})

# --- CHECK IF THIS IS A NEW DAY ---
def is_file_from_today(filepath):
    if not os.path.exists(filepath):
        return False
    file_date = datetime.fromtimestamp(os.path.getmtime(filepath)).strftime("%Y-%m-%d")
    return file_date == TODAY

stale_files = [PROGRESS_FILE, URLS_FILE, PARTIAL_URLS_FILE, PHASE1_PAGE_FILE]
for f in stale_files:
    if os.path.exists(f) and not is_file_from_today(f):
        print(f"Found stale file from a previous day: {f}. Removing.")
        os.remove(f)

# --- CHROME OPTIONS (Phase 1 only) ---
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

def save_progress(batch_start):
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"batch_start": batch_start}, f)
    print(f"Progress saved: next batch starts at index {batch_start}")

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r") as f:
            return json.load(f).get("batch_start", 0)
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
    os.system("pkill -9 -f chrome")
    os.system("pkill -9 -f chromedriver")
    os.system("rm -rf /tmp/.com.google.Chrome.*")
    os.system("rm -rf /tmp/.org.chromium.Chromium.*")
    os.system("rm -rf /tmp/scoped_dir*")
    os.system("rm -rf /tmp/chrome_crashpad*")
    os.system("sync; echo 3 > /proc/sys/vm/drop_caches")
    time.sleep(5)

def start_browser():
    for attempt in range(3):
        try:
            cleanup_chrome()
            driver = uc.Chrome(options=get_chrome_options(), driver_executable_path='/usr/bin/chromedriver')
            return driver
        except Exception as e:
            print(f"Browser start attempt {attempt+1}/3 failed: {e}")
            cleanup_chrome()
            time.sleep(60 * (attempt + 1))
    print("FATAL: Could not start browser after 3 attempts.")
    sys.exit(1)

def restart_script():
    cleanup_chrome()
    time.sleep(30)
    print("Exiting for auto-restart...")
    sys.exit(42)

# --- PHASE 2: EXTRACT DATA FROM HTML USING REQUESTS ---
def extract_listing_data(url):
    """
    Fetch a single listing page with requests and extract all car data.
    Returns a dict of car data, or None if the request failed.
    """
    try:
        response = session.get(url, timeout=30)
        
        # Detect inactive listing (redirect away from /ad/)
        if "/ad/" not in response.url:
            listing_id = url.split("/")[-1].replace(".html", "").split("-")[-1]
            return {
                "listing_id": listing_id, "listing_url": url, "price": None,
                "mileage": None, "city": None, "listing_date": None,
                "seller_type": None, "payment_options": None,
                "scraped_at": datetime.now(timezone.utc), "active": False
            }
        
        if response.status_code != 200:
            print(f"HTTP {response.status_code}")
            return None
        
        html = response.text
        soup = BeautifulSoup(html, "html.parser")
        
        # --- METHOD 1: Try to find JSON-LD structured data ---
        brand, model, year, fuel, transmission = None, None, None, None, None
        body, engine, payment_options, condition = None, None, None, None
        price, mileage, city, seller_type, listing_age = None, None, None, None, None
        
        # Try extracting from embedded JSON in script tags
        json_data = None
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and data.get("@type") == "Product":
                    json_data = data
                    break
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("@type") == "Product":
                            json_data = item
                            break
            except:
                pass
        
        if json_data:
            if "name" in json_data:
                # Name often contains "Brand Model Year"
                pass
            if "offers" in json_data:
                offers = json_data["offers"]
                if isinstance(offers, dict):
                    try:
                        price = int(float(offers.get("price", 0)))
                    except:
                        pass
        
        # --- METHOD 2: Try extracting from __NEXT_DATA__ or similar embedded JSON ---
        for script in soup.find_all("script"):
            if script.string and ("__NEXT_DATA__" in script.string or "window.__DATA__" in script.string or "listing" in script.string.lower()[:100]):
                # Try to find a JSON blob
                json_match = re.search(r'\{.*"listing".*\}', script.string, re.DOTALL)
                if not json_match:
                    json_match = re.search(r'\{.*"ad".*\}', script.string, re.DOTALL)
                if json_match:
                    try:
                        page_data = json.loads(json_match.group())
                        # Navigate to find ad/listing data
                        ad = None
                        if "props" in page_data:
                            # Next.js style
                            props = page_data.get("props", {}).get("pageProps", {})
                            ad = props.get("ad") or props.get("listing") or props.get("data", {}).get("ad")
                        if not ad and "listing" in page_data:
                            ad = page_data["listing"]
                        if not ad and "ad" in page_data:
                            ad = page_data["ad"]
                        
                        if ad and isinstance(ad, dict):
                            # Extract fields from the JSON ad object
                            price = price or _safe_int(ad.get("price"))
                            city = city or ad.get("location", {}).get("city_name") or ad.get("city")
                            
                            # Extract from attributes/details array
                            attrs = ad.get("attributes", []) or ad.get("details", []) or ad.get("specs", [])
                            if isinstance(attrs, list):
                                for attr in attrs:
                                    if isinstance(attr, dict):
                                        key = str(attr.get("label", attr.get("name", attr.get("key", "")))).lower()
                                        value = attr.get("value", attr.get("formatted_value", ""))
                                        _assign_spec(key, value, locals())
                            elif isinstance(attrs, dict):
                                for key, value in attrs.items():
                                    _assign_spec(key.lower(), value, locals())
                    except json.JSONDecodeError:
                        pass
        
        # --- METHOD 3: Fallback — parse the HTML directly ---
        # Price
        if price is None:
            price_el = soup.find("span", string=re.compile(r"EGP"))
            if price_el:
                try:
                    price = int(re.sub(r"[^\d]", "", price_el.get_text()))
                except:
                    pass
        
        # Try aria-label based extraction (matches the Selenium approach)
        # Highlighted Details
        highlighted = soup.find("div", attrs={"aria-label": "Highlighted Details"})
        if highlighted:
            for div in highlighted.find_all("div"):
                spans = div.find_all("span")
                if len(spans) >= 2:
                    key = spans[0].get_text(strip=True).lower()
                    value = spans[-1].get_text(strip=True)
                    if key == "year" and not year: year = value
                    elif key == "fuel type" and not fuel: fuel = value
                    elif key == "transmission type" and not transmission: transmission = value
                    elif key == "kilometers" and not mileage:
                        numbers = re.findall(r"\d+", value.replace(",", ""))
                        if numbers: mileage = int(numbers[0])
                    elif key == "condition" and not condition: condition = value
                    elif key == "payment options" and not payment_options: payment_options = value
        
        # Details section
        details = soup.find("div", attrs={"aria-label": "Details"})
        if details:
            for div in details.find_all("div"):
                spans = div.find_all("span")
                if len(spans) == 2:
                    key = spans[0].get_text(strip=True).lower()
                    value = spans[-1].get_text(strip=True)
                    if key == "brand" and not brand: brand = value
                    elif key == "model" and not model: model = value
                    elif key == "year" and not year: year = value
                    elif key == "body type" and not body: body = value
                    elif key == "fuel type" and not fuel: fuel = value
                    elif key == "transmission type" and not transmission: transmission = value
                    elif key == "engine capacity (cc)" and not engine: engine = value
                    elif key == "condition" and not condition: condition = value
                    elif key == "payment options" and not payment_options: payment_options = value
        
        # Location
        if city is None:
            loc_el = soup.find(attrs={"aria-label": "Location"})
            if loc_el:
                city = loc_el.get_text(strip=True)
        
        # Seller type
        if seller_type is None:
            seller_el = soup.find(string=re.compile(r"Listed by", re.I))
            if seller_el:
                seller_type = "agency" if "agency" in seller_el.lower() else "private user"
        
        # Listing age
        if listing_age is None:
            age_el = soup.find("span", string=re.compile(r"ago|Yesterday|Today", re.I))
            if age_el:
                listing_age = age_el.get_text(strip=True)
        
        # Mileage fallback
        if mileage is None:
            km_el = soup.find("span", string=re.compile(r"\d+.*km", re.I))
            if km_el:
                numbers = re.findall(r"\d+", km_el.get_text().replace(",", ""))
                if numbers:
                    mileage = int(numbers[0])
        
        # Price fallback — search all text
        if price is None:
            price_match = re.search(r'([\d,]+)\s*EGP|EGP\s*([\d,]+)', html)
            if price_match:
                price_str = price_match.group(1) or price_match.group(2)
                try:
                    price = int(price_str.replace(",", ""))
                except:
                    pass
        
        scraped_at = datetime.now(timezone.utc)
        listing_date = compute_listing_date(scraped_at, listing_age)
        listing_id = url.split("/")[-1].replace(".html", "").split("-")[-1]
        
        return {
            "listing_id": listing_id, "listing_url": url, "brand": brand, "model": model,
            "year": year, "fuel_type": fuel, "transmission": transmission, "body_type": body,
            "engine_capacity": engine, "price": price, "mileage": mileage, "city": city,
            "listing_date": listing_date, "condition": condition, "seller_type": seller_type,
            "payment_options": payment_options, "scraped_at": scraped_at, "active": True
        }
    
    except requests.exceptions.Timeout:
        print("Timeout")
        return None
    except requests.exceptions.ConnectionError:
        print("Connection error")
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None

def _safe_int(val):
    """Safely convert a value to int."""
    if val is None:
        return None
    try:
        return int(float(str(val).replace(",", "")))
    except:
        return None

def save_batch_data(step2_data):
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
# PHASE 1: COLLECT LISTING URLS (Chrome — in batches of 50 pages)
# ===================================================================
listing_urls = load_urls()

if listing_urls is None:
    start_page = load_phase1_page()

    if start_page > MAX_PAGES:
        print("=" * 60)
        print("PHASE 1 COMPLETE: Finalizing URL list...")
        print("=" * 60)

        listing_urls_set = load_partial_urls()

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

        listing_urls = list(set(active_list) | listing_urls_set)
        save_urls(listing_urls)
        save_progress(0)
        print(f"Total unique URLs to scrape: {len(listing_urls)}")

        if os.path.exists(PARTIAL_URLS_FILE): os.remove(PARTIAL_URLS_FILE)
        if os.path.exists(PHASE1_PAGE_FILE): os.remove(PHASE1_PAGE_FILE)

    else:
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
                try: driver.save_screenshot(f"error_page_{page}.png")
                except: pass
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

        save_partial_urls(listing_urls_set)
        save_phase1_page(end_page)

        print(f"Restarting script to continue Phase 1 from page {end_page}...")
        restart_script()


# ===================================================================
# PHASE 2: SCRAPE LISTINGS WITH REQUESTS (no Chrome needed!)
# ===================================================================
print("=" * 60)
print("Phase 2 uses HTTP requests — no Chrome needed!")
print("=" * 60)

# Kill any leftover Chrome to free RAM for Phase 2
cleanup_chrome()

batch_start = load_progress()

if batch_start >= len(listing_urls):
    print("=" * 60)
    print("ALL BATCHES COMPLETE! Nothing left to scrape.")
    print("=" * 60)
    if os.path.exists(PROGRESS_FILE): os.remove(PROGRESS_FILE)
    if os.path.exists(URLS_FILE): os.remove(URLS_FILE)
    sys.exit(0)

batch_end = min(batch_start + BATCH_SIZE, len(listing_urls))
batch_urls = listing_urls[batch_start:batch_end]

print(f"PHASE 2: Scraping batch {batch_start}-{batch_end} of {len(listing_urls)} total URLs")
print(f"This batch: {len(batch_urls)} listings")
print("=" * 60)

step2_data = []
start_deep = time.time()
consecutive_failures = 0
MAX_CONSECUTIVE_FAILURES = 20  # If 20 in a row fail, something is wrong

# Save the first response for debugging
debug_saved = False

for i, url in enumerate(batch_urls, start=1):
    print(f"Scraping listing {i}/{len(batch_urls)} (global: {batch_start + i}/{len(listing_urls)})")
    
    result = extract_listing_data(url)
    
    if result is not None:
        step2_data.append(result)
        consecutive_failures = 0
        
        if result.get("active"):
            print(f"Success — Price: {result.get('price')}, Brand: {result.get('brand')}, Model: {result.get('model')}")
        else:
            print("Listing inactive")
        
        # Save first successful HTML for debugging
        if not debug_saved and result.get("active"):
            try:
                resp = session.get(url, timeout=30)
                with open("debug_page.html", "w", encoding="utf-8") as f:
                    f.write(resp.text)
                debug_saved = True
                print("Saved debug_page.html for inspection")
            except:
                pass
    else:
        consecutive_failures += 1
        print(f"Failed (consecutive failures: {consecutive_failures})")
        
        # If too many consecutive failures, we might be blocked
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            print(f"WARNING: {MAX_CONSECUTIVE_FAILURES} consecutive failures! Possible IP block.")
            print("Saving progress and pausing for 5 minutes...")
            save_batch_data(step2_data)
            save_progress(batch_start + i)
            time.sleep(300)  # Wait 5 minutes
            consecutive_failures = 0
            
            # Rotate User-Agent
            agents = [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.7680.164 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
            ]
            session.headers["User-Agent"] = random.choice(agents)
            print(f"Rotated User-Agent. Resuming...")
    
    # Humanizing wait — shorter than Chrome since requests is faster
    wait = random.uniform(3, 7)
    time.sleep(wait)
    
    # Periodic progress save every 100 listings (in case of crash)
    if i % 100 == 0:
        print(f"Checkpoint: saving progress at listing {i}...")
        save_batch_data(step2_data)
        step2_data = []  # Reset to avoid re-saving
        save_progress(batch_start + i)

# --- BATCH COMPLETE ---
end_deep = time.time()
print(f"\n--- Batch {batch_start}-{batch_end} finished in {(end_deep - start_deep)/60:.2f} minutes ---")

save_batch_data(step2_data)

next_batch_start = batch_end
save_progress(next_batch_start)

if next_batch_start >= len(listing_urls):
    print("=" * 60)
    print("ALL BATCHES COMPLETE!")
    print("=" * 60)
    if os.path.exists(PROGRESS_FILE): os.remove(PROGRESS_FILE)
    if os.path.exists(URLS_FILE): os.remove(URLS_FILE)
else:
    remaining = len(listing_urls) - next_batch_start
    print("=" * 60)
    print(f"Batch done. {remaining} URLs remaining.")
    print("=" * 60)
    restart_script()