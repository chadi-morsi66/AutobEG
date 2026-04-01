import time
import pandas as pd
import os
import sys
import re
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
display = Display(visible=0, size=(1920, 1080))
display.start()

# --- CONFIGURATION ---
script_dir = os.path.dirname(os.path.abspath(__file__))
CSV_FILE_PATH = os.path.abspath(os.path.join(script_dir, "..", "Data", "step2_contactcars_listings.csv"))
BASE_URL = "https://www.contactcars.com"
SEARCH_URL = "https://www.contactcars.com/en/cars?&type=car&status=4&sortBy=&sortOrder=false"
MAX_PAGES = 200

def get_chrome_options():
    options = uc.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--accept-lang=en-US,en')
    return options

chrome_service = Service('/usr/bin/chromedriver')

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
    """Parse relative date text like '3 days ago', 'Yesterday', etc."""
    if not text:
        return None
    text = text.lower()
    if "today" in text or "hour" in text:
        return scraped_at.date()
    if "yesterday" in text:
        return (scraped_at - timedelta(days=1)).date()
    if "day" in text:
        match = re.search(r"\d+", text)
        if match:
            return (scraped_at - timedelta(days=int(match.group()))).date()
    if "week" in text:
        match = re.search(r"\d+", text)
        if match:
            return (scraped_at - timedelta(days=7 * int(match.group()))).date()
    if "month" in text:
        match = re.search(r"\d+", text)
        if match:
            return (scraped_at - timedelta(days=30 * int(match.group()))).date()
    return None


def extract_listing_details(driver):
    """
    Extract car details from a ContactCars listing page.
    
    *** IMPORTANT: YOU MUST VERIFY THESE SELECTORS ***
    Open a ContactCars listing in your browser, right-click > Inspect, 
    and verify/update the XPaths and CSS selectors below.
    
    The selectors below are best-guess based on the site structure.
    ContactCars may use different class names or aria-labels.
    """
    details = {}

    # --- STRATEGY 1: Try structured data (JSON-LD) first ---
    # Many car sites embed structured data that has everything
    try:
        scripts = driver.find_elements(By.XPATH, "//script[@type='application/ld+json']")
        for script in scripts:
            import json
            try:
                data = json.loads(script.get_attribute("textContent"))
                if isinstance(data, dict) and data.get("@type") in ["Car", "Vehicle", "Product"]:
                    details["brand"] = data.get("brand", {}).get("name") if isinstance(data.get("brand"), dict) else data.get("brand")
                    details["model"] = data.get("model")
                    details["year"] = data.get("vehicleModelDate") or data.get("productionDate")
                    if data.get("offers"):
                        offers = data["offers"] if isinstance(data["offers"], dict) else data["offers"][0]
                        details["price"] = offers.get("price")
                    if data.get("mileageFromOdometer"):
                        m = data["mileageFromOdometer"]
                        details["mileage"] = m.get("value") if isinstance(m, dict) else m
                    details["fuel_type"] = data.get("fuelType")
                    details["body_type"] = data.get("bodyType")
                    details["transmission"] = data.get("vehicleTransmission")
                    details["engine_capacity"] = data.get("vehicleEngine", {}).get("engineDisplacement") if isinstance(data.get("vehicleEngine"), dict) else None
                    print("  -> Extracted from JSON-LD structured data")
                    return details
            except (json.JSONDecodeError, TypeError):
                continue
    except Exception:
        pass

    # --- STRATEGY 2: Scrape from page elements ---
    # These selectors are GUESSES — you need to verify them via browser DevTools
    
    # Price - look for EGP text
    try:
        price_el = driver.find_element(By.XPATH, "//span[contains(text(),'EGP')] | //div[contains(text(),'EGP')]")
        price_text = price_el.text
        price_nums = re.sub(r"[^\d]", "", price_text)
        if price_nums:
            details["price"] = int(price_nums)
    except Exception:
        pass

    # Try to find a specs/details table or section
    # ContactCars typically shows specs in a structured list
    # Common patterns: key-value pairs in divs, tables, or dl/dt/dd
    
    # Pattern A: Key-value spans (similar to Dubizzle)
    try:
        # Look for all key-value pairs in the page
        spec_sections = driver.find_elements(By.XPATH, 
            "//div[contains(@class,'spec') or contains(@class,'detail') or contains(@class,'info') or contains(@class,'feature')]//div[span[2]]"
        )
        for section in spec_sections:
            spans = section.find_elements(By.TAG_NAME, "span")
            if len(spans) >= 2:
                key = spans[0].get_attribute("textContent").strip().lower()
                value = spans[-1].get_attribute("textContent").strip()
                if key and value:
                    if "brand" in key or "make" in key:
                        details["brand"] = value
                    elif "model" in key:
                        details["model"] = value
                    elif "year" in key:
                        details["year"] = value
                    elif "fuel" in key:
                        details["fuel_type"] = value
                    elif "transmission" in key or "gear" in key:
                        details["transmission"] = value
                    elif "body" in key:
                        details["body_type"] = value
                    elif "engine" in key or "cc" in key or "capacity" in key:
                        details["engine_capacity"] = value
                    elif "kilometer" in key or "mileage" in key or "km" in key:
                        numbers = re.findall(r"\d+", value.replace(",", ""))
                        if numbers:
                            details["mileage"] = int(numbers[0])
                    elif "color" in key and "exterior" not in details:
                        details["color"] = value
                    elif "condition" in key:
                        details["condition"] = value
    except Exception as e:
        print(f"  DEBUG: Spec extraction Pattern A failed: {e}")

    # Pattern B: Table rows
    try:
        rows = driver.find_elements(By.XPATH, "//table//tr[td[2]]")
        for row in rows:
            tds = row.find_elements(By.TAG_NAME, "td")
            if len(tds) >= 2:
                key = tds[0].text.strip().lower()
                value = tds[1].text.strip()
                if "brand" in key or "make" in key:
                    details.setdefault("brand", value)
                elif "model" in key:
                    details.setdefault("model", value)
                elif "year" in key:
                    details.setdefault("year", value)
                elif "fuel" in key:
                    details.setdefault("fuel_type", value)
                elif "transmission" in key:
                    details.setdefault("transmission", value)
                elif "body" in key:
                    details.setdefault("body_type", value)
                elif "engine" in key or "cc" in key:
                    details.setdefault("engine_capacity", value)
                elif "kilometer" in key or "km" in key:
                    numbers = re.findall(r"\d+", value.replace(",", ""))
                    if numbers:
                        details.setdefault("mileage", int(numbers[0]))
    except Exception:
        pass

    # Mileage fallback - look for "km" text anywhere
    if "mileage" not in details:
        try:
            km_el = driver.find_element(By.XPATH, "//*[contains(text(),' km')]")
            km_text = km_el.text
            numbers = re.findall(r"[\d,]+", km_text)
            if numbers:
                details["mileage"] = int(numbers[0].replace(",", ""))
        except Exception:
            pass

    # Location / City
    try:
        # ContactCars shows location like "Cairo، New Cairo & 5th Settlement"
        loc_el = driver.find_element(By.XPATH, 
            "//*[contains(@class,'location') or contains(@class,'address') or contains(@aria-label,'location') or contains(@aria-label,'Location')]"
        )
        details["city"] = loc_el.text.strip()
    except Exception:
        pass

    # Seller type (showroom vs private)
    try:
        seller_el = driver.find_element(By.XPATH, 
            "//*[contains(text(),'Showroom') or contains(text(),'showroom') or contains(text(),'Private') or contains(text(),'private') or contains(text(),'Dealer') or contains(text(),'dealer')]"
        )
        seller_text = seller_el.text.lower()
        if "showroom" in seller_text or "dealer" in seller_text:
            details["seller_type"] = "showroom"
        else:
            details["seller_type"] = "private"
    except Exception:
        pass

    # Condition tag (Fabrika In&Out, Almost New, Imported Specs, etc.)
    try:
        condition_el = driver.find_element(By.XPATH,
            "//*[contains(text(),'Fabrika') or contains(text(),'almost new') or contains(text(),'Almost New') or contains(text(),'Imported')]"
        )
        details["condition"] = condition_el.text.strip()
    except Exception:
        pass

    # Listing date / age
    try:
        date_el = driver.find_element(By.XPATH, 
            "//span[contains(text(),'ago') or contains(text(),'Yesterday') or contains(text(),'Today')] | //*[contains(text(),'Listed')]"
        )
        details["listing_age"] = date_el.text.strip()
    except Exception:
        pass

    # Payment options / Down payment
    try:
        dp_el = driver.find_element(By.XPATH, "//*[contains(text(),'D.P.') or contains(text(),'Down Payment') or contains(text(),'Installment')]")
        details["payment_options"] = dp_el.text.strip()
    except Exception:
        pass

    return details


# --- 4. SCRAPE LISTING URLS ---
driver = uc.Chrome(options=get_chrome_options(), driver_executable_path='/usr/bin/chromedriver')
listing_urls = set()
start_search = time.time()

for page in range(1, MAX_PAGES + 1):
    page_url = f"{SEARCH_URL}&page={page}"
    print(f"Scraping page {page}: {page_url}")
    driver.get(page_url)

    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 
                "a[href*='/en/'], article, div[class*='card'], div[class*='listing']"
            ))
        )
        
        # Find all listing links
        links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/en/']")
        
        page_count = 0
        for link in links:
            href = link.get_attribute("href")
            if not href:
                continue
            # Keep individual listing pages (with numeric IDs or deep paths)
            # Skip navigation/category links
            parts = href.rstrip("/").split("/")
            if len(parts) > 6 or any(char.isdigit() for char in parts[-1]):
                if href.startswith("http"):
                    listing_urls.add(href)
                else:
                    listing_urls.add(BASE_URL + href)
                page_count += 1
        
        print(f"  Found {page_count} listings on page {page}")
        
        if page_count == 0:
            print("  No listings found - possibly last page. Stopping.")
            break

    except Exception as e:
        print(f"Timeout on page {page}. Moving on.")
        print(f"The page title is actually: {driver.title}")
        driver.save_screenshot(f"error_page_{page}.png")
        break

    wait_time = random.uniform(5, 10)
    time.sleep(wait_time)

end_search = time.time()
search_duration = end_search - start_search
print(f"--- Search Phase Finished in {search_duration/60:.2f} minutes ---")

listing_urls = list(set(active_list) | set(listing_urls))
print("Total URLs:", len(listing_urls))

# --- 5. SCRAPE INDIVIDUAL CAR DATA ---
print(f"Starting deep scrape of {len(listing_urls)} listings...")
start_deep = time.time()
step2_data = []

for i, url in enumerate(listing_urls, start=1):
    print(f"Scraping listing {i}/{len(listing_urls)}: {url}")
    try:
        driver.get(url)
        time.sleep(2)

        # Save first page HTML for debugging selectors
        if i == 1:
            print("Saving debug_page.html for the first listing...")
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)

        current_url = driver.current_url

        # Detect inactive listing (redirected away from detail page)
        if "/used-cars/" not in current_url and "/new-cars/" not in current_url:
            listing_id = url.split("/")[-1].replace(".html", "").split("-")[-1]
            step2_data.append({
                "listing_id": listing_id, "listing_url": url, "price": None,
                "mileage": None, "city": None, "listing_date": None,
                "seller_type": None, "payment_options": None,
                "scraped_at": datetime.now(timezone.utc), "active": False
            })
            print("  Listing inactive")
            continue

        active = True

        # Wait for page to load - look for price (EGP)
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(),'EGP')]"))
            )
        except Exception as e:
            print(f"  Listing {i} failed to load. Restarting browser...")
            driver.save_screenshot(f"car_error_{i}.png")
            try:
                driver.quit()
            except:
                pass
            time.sleep(5)
            driver = uc.Chrome(options=get_chrome_options(), driver_executable_path='/usr/bin/chromedriver')
            continue

        # Scroll to trigger lazy-loaded content
        driver.execute_script("window.scrollBy(0, 2000);")
        time.sleep(3)

        # Extract all details
        details = extract_listing_details(driver)

        scraped_at = datetime.now(timezone.utc)
        listing_date = compute_listing_date(scraped_at, details.get("listing_age"))
        listing_id = url.split("/")[-1].replace(".html", "").split("-")[-1]

        step2_data.append({
            "listing_id": listing_id,
            "listing_url": url,
            "brand": details.get("brand"),
            "model": details.get("model"),
            "year": details.get("year"),
            "fuel_type": details.get("fuel_type"),
            "transmission": details.get("transmission"),
            "body_type": details.get("body_type"),
            "engine_capacity": details.get("engine_capacity"),
            "price": details.get("price"),
            "mileage": details.get("mileage"),
            "city": details.get("city"),
            "color": details.get("color"),
            "condition": details.get("condition"),
            "listing_date": listing_date,
            "seller_type": details.get("seller_type"),
            "payment_options": details.get("payment_options"),
            "scraped_at": scraped_at,
            "active": active
        })
        print("  Success")

    except Exception as e:
        print(f"  Failed: {e}")

    wait = random.uniform(8, 15)
    print(f"  Humanizing: Waiting {wait:.1f}s before next car...")
    time.sleep(wait)

    # Anti-bot flush every 15 cars
    if i % 15 == 0:
        long_wait = random.uniform(30, 60)
        print(f"  Anti-Bot Flush: Closing browser and resting for {long_wait:.1f}s...")
        try:
            driver.quit()
        except:
            pass
        time.sleep(long_wait)
        print("  Starting a fresh browser session...")
        driver = uc.Chrome(options=get_chrome_options(), driver_executable_path='/usr/bin/chromedriver')

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

# Saving example to debug
print("Null counts per column:")
print(df_step2.isnull().sum())
df_step2.to_csv('example_contactcars_data.csv', index=False)

df_step2["year"] = df_step2["year"].astype('Int64')

cols = ["brand", "model", "year", "fuel_type", "transmission", "body_type", "engine_capacity"]
df_step2["vehicle_id"] = df_step2[cols].apply(
    lambda row: "_".join([str(val) if pd.notna(val) else "" for val in row]),
    axis=1
)
df_step2 = df_step2.set_index("vehicle_id")
df_step2 = df_step2.drop(columns=cols, errors='ignore')

df_step2["scraped_at"] = df_step2["scraped_at"].dt.strftime("%Y-%m-%d %H:%M:%S")

# Check for inactive ads
df_step2.loc[df_step2["mileage"].isna() & df_step2["seller_type"].isna(), "active"] = False

# Save to CSV
file_exists = os.path.isfile(CSV_FILE_PATH)
df_step2.to_csv(CSV_FILE_PATH, mode='a', header=not file_exists, index=False)

print(f"Total listings scraped and processed: {len(df_step2)}")
print(f"Data appended to {CSV_FILE_PATH}")
print(df_step2.head(10))
