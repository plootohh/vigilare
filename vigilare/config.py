import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

# --- Database Paths ---
DB_CRAWL = os.path.join(DATA_DIR, "vigilare_crawl.db")
DB_STORAGE = os.path.join(DATA_DIR, "vigilare_storage.db")
DB_SEARCH = os.path.join(DATA_DIR, "vigilare_search.db")

LOG_PATH = os.path.join(DATA_DIR, "vigilare.log")

# --- Crawler Identity ---
USER_AGENT = "Mozilla/5.0 (compatible; Vigilare/0.1.5; +mailto:mailme31@proton.me)"

# --- Tuning ---
FETCH_THREADS = 50
PARSE_THREADS = 10
BATCH_SIZE = 5000

# --- Governance & Limits ---
MAX_BYTES = 6_000_000
MAX_TEXT_CHARS = 1_000_000
MAX_PAGES_PER_DOMAIN = 10000
CRAWL_DELAY = 0.5
CRAWL_EPOCH = 1

# --- Assets ---
ICONS_DIR = os.path.join(DATA_DIR, "icons")
if not os.path.exists(ICONS_DIR):
    os.makedirs(ICONS_DIR)