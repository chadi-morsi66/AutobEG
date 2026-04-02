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
from pyvirtualdisplay import Display
import undetected_chromedriver as uc

# --- START THE INVISIBLE MONITOR ---
print("Starting virtual display...")
display = Display(visible=0, size=(1920, 1080))
display.start()

# --- CONFIGURATION ---
script_dir = os.path.dirname(os.path.abspath(__file__))
CSV_FILE_PATH = os.path.abspath(os.path.join(script_dir, "..", "Data", "step2_listings.csv"))
BASE_URL = "https://www.dubizzle.com.eg/en/"
SEARCH_URL = "https://www.dubizzle.com.eg/en/vehicles/cars-for-sale/q-cars/"
MAX_PAGES = 200      

def get_chrome_options():
    options = uc.ChromeOptions()
    # DELETE THIS LINE: options.add_argument('--headless=new') 
    
    options.add_argument('--no-sandbox') 
    options.add_argument('--disable-dev-shm-usage') 
    options.add_argument('--accept-lang=en-US,en')
    # Notice we don't even need the window-size argument here anymore, 
    # because the virtual monitor handles it!
    return options

# Create a Service object pointing to the Linux ChromeDriver
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
        # THE FIX: .dropna() removes the invisible blank rows!
        active_list = df['listing_url'].dropna().tolist()
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
    
    # 1. Grab "Highlighted Details" (Year, Fuel Type, Transmission, Kilometers, etc.)
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

    # 2. Grab "Details" (Brand, Model, Body Type, Engine Capacity, etc.)
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

# --- 4. SCRAPE LISTING URLS ---
driver = uc.Chrome(options=get_chrome_options(), driver_executable_path='/usr/bin/chromedriver')
listing_urls = set()
start_search = time.time()

for page in range(1, MAX_PAGES + 1):
    page_url = f"{SEARCH_URL}?page={page}"
    print(f"Scraping page {page}: {page_url}")
    
    try:
        driver.get(page_url)
    except Exception as e:
        print(f"Session dead on page {page}. Restarting browser...")
        try:
            driver.quit()
        except: pass
        time.sleep(5)
        driver = uc.Chrome(options=get_chrome_options(), driver_executable_path='/usr/bin/chromedriver')
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
                    listing_urls.add(href)
                else:
                    listing_urls.add(BASE_URL + href)
            except:
                pass
                
    except Exception as e:
        print(f"Timeout on page {page} (Possible bot block). Restarting browser...")
        driver.save_screenshot(f"error_page_{page}.png")     
        
        # --- 🚨 EMERGENCY BROWSER REBOOT FOR PHASE 1 🚨 ---
        try:
            driver.quit()
        except: pass
        time.sleep(4)
        driver = uc.Chrome(options=get_chrome_options(), driver_executable_path='/usr/bin/chromedriver')
        
        # We changed 'break' to 'continue' so it keeps going to page 19!
        continue 
        
    wait_time = random.uniform(5, 12)
    time.sleep(wait_time)

    # --- 🚨 PREVENTATIVE BROWSER FLUSH EVERY 15 PAGES 🚨 ---
    if page % 10 == 0:
        print("Phase 1 Anti-Bot Flush: Restarting browser to avoid block...")
        try:
            driver.quit()
        except: pass
        wait_time2 = random.uniform(10, 20)
        time.sleep(wait_time2) # Quick 10-second rest
        driver = uc.Chrome(options=get_chrome_options(), driver_executable_path='/usr/bin/chromedriver')

end_search = time.time()
search_duration = end_search - start_search
print(f"--- Search Phase 1 Finished in {search_duration/60:.2f} minutes ---")

listing_urls = list(set(active_list) | set(listing_urls))
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
        if i == 1:
            print("Saving debug_page.html for the first listing...")
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
        current_url = driver.current_url

        # Detect inactive listing
        if "/ad/" not in current_url:
            listing_id = url.split("/")[-1].replace(".html", "").split("-")[-1]
            step2_data.append({
                "listing_id": listing_id, "listing_url": url, "price": None,
                "mileage": None, "city": None, "listing_date": None,
                "seller_type": None, "payment_options":None, "scraped_at": datetime.now(timezone.utc), "active": False
            })
            print("Listing inactive")
            continue

        active = True
        
        # 1. WAIT FOR THE PAGE TO TRULY LOAD FIRST
        try:
            # Wait up to 20 seconds specifically for the Price to load
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//span[contains(text(),'EGP')]"))
            )
        except Exception as e:
            print(f"Listing {i} failed to load (Possible bot block). Restarting browser...")
            driver.save_screenshot(f"car_error_{i}.png")
            
            # --- EMERGENCY BROWSER REBOOT ---
            try:
                driver.quit()
            except: pass
            time.sleep(5)
            driver = uc.Chrome(options=get_chrome_options(), driver_executable_path='/usr/bin/chromedriver')
            # --------------------------------
            
            continue 

        # --- 🚨 SCROLL TO WAKE UP THE HTML 🚨 ---
        driver.execute_script("window.scrollBy(0, 2000);") # Scrolls down 2000 pixels
        time.sleep(3) # Wait 2 seconds for the "Details" box to pop into existence
        # ---------------------------------------------------------------
        
        # 2. NOW EXTRACT THE SPECS 
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

        # Mileage from Highlighted Details
        km_val = specs.get("kilometers")
        if km_val:
            numbers = re.findall(r"\d+", km_val.replace(",", ""))
            if numbers:
                mileage = int(numbers[0])

        # 3. NOW GRAB THE REST OF THE DATA
        try:
            price_text = driver.find_element(By.XPATH, "//span[contains(text(),'EGP')]").text
            price = int(re.sub(r"[^\d]", "", price_text))
        except: pass

        # Fallback: if mileage wasn't in specs, try XPath
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
            "listing_date": listing_date, "condition": condition, "seller_type": seller_type, "payment_options": payment_options, 
            "scraped_at": scraped_at, "active": active
        })
        print("Success")

    except Exception as e:
        error_msg = str(e)
        print(f"Failed: {error_msg}")
        
        # --- 🚨 DETECT A DEAD BROWSER CRASH 🚨 ---
        if "HTTPConnectionPool" in error_msg or "not reachable" in error_msg or "refused" in error_msg:
            print("🚨 Browser crashed unexpectedly! Forcing emergency reboot...")
            try:
                driver.quit()
            except: pass
            
            # Wipe the server RAM instantly
            os.system("pkill -f chrome")
            os.system("pkill -f chromedriver")
            time.sleep(10)
            
            print("Rebooting browser...")
            driver = uc.Chrome(options=get_chrome_options(), driver_executable_path='/usr/bin/chromedriver')
        # ----------------------------------------
        
    wait = random.uniform(8, 15)
    print(f"Humanizing: Waiting {wait:.1f}s before next car...")
    time.sleep(wait)

# 2. Take a 'Coffee Break' and FLUSH THE BROWSER every 15 cars
    if i % 15 == 0:
        long_wait = random.uniform(30, 60)
        print(f"Anti-Bot Flush: Closing browser and resting for {long_wait:.1f} seconds...")
        
        try:
            driver.quit()
        except: pass
        
        # --- 🚨 THE AUTO-ASSASSIN 🚨 ---
        # Tells the Linux server to wipe all Chrome memory automatically!
        os.system("pkill -f chrome")
        os.system("pkill -f chromedriver")
        # -------------------------------
        
        time.sleep(long_wait)
        
        print("Starting a fresh browser session...")
        try:
            driver = uc.Chrome(options=get_chrome_options(), driver_executable_path='/usr/bin/chromedriver')
        except Exception as e:
            print(f"Critical RAM Error: {e}. Wiping memory again and waiting 60s...")
            os.system("pkill -f chrome")
            time.sleep(60)
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

##Saving example to debug
print("Null counts per column:")
print(df_step2.isnull().sum())
df_step2.to_csv('example_data.csv', index=False)

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
file_exists = os.path.isfile(CSV_FILE_PATH)
df_step2.to_csv(CSV_FILE_PATH, mode='a', header=not file_exists, index=False)

print(f"Total listings scraped and processed: {len(df_step2)}")
print(f"Data appended to {CSV_FILE_PATH}")
print(df_step2.head(10))