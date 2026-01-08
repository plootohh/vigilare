import sqlite3, os, requests, zipfile, io, config

# --- USER DEFINED SEEDS ---
MANUAL_SEEDS = [
    "https://www.abc.net.au", "https://www.bbc.com", "https://www.bloomberg.com",
    "https://www.cnn.com", "https://www.aljazeera.com", "https://www.reuters.com",
    "https://www.npr.org", "https://github.com", "https://stackoverflow.com",
    "https://slashdot.org", "https://news.ycombinator.com", "https://dev.to",
    "https://www.w3schools.com", "https://developer.mozilla.org", "https://www.wikipedia.org",
    "https://en.wikipedia.org/wiki/Main_Page", "https://curlie.org", "https://www.britannica.com",
    "https://archive.org", "https://www.mit.edu", "https://www.stanford.edu",
    "https://www.harvard.edu", "https://www.youtube.com", "https://www.reddit.com",
    "https://medium.com", "https://wordpress.com/discover", "https://www.amazon.com",
    "https://www.ebay.com", "https://www.craigslist.org", "https://www.popurls.com",
    "https://alltop.com", "https://drudgereport.com"
]


def init_database():
    print("--- Initialising Vigilo Database ---")
    
    if not os.path.exists(config.DATA_DIR):
        os.makedirs(config.DATA_DIR)

    print("[1/3] Creating Crawl DB Schema...")
    conn = sqlite3.connect(config.DB_CRAWL)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA page_size=4096;")
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS frontier (
            url TEXT PRIMARY KEY,
            domain TEXT,
            priority INTEGER DEFAULT 10,
            status INTEGER DEFAULT 0,
            retry_count INTEGER DEFAULT 0,
            reserved_at DATETIME, 
            next_crawl_time DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_frontier_schedule ON frontier(status, next_crawl_time, priority)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_frontier_reserved ON frontier(status, reserved_at)")
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS visited (
            url TEXT PRIMARY KEY,
            title TEXT,
            description TEXT,
            http_status INTEGER,
            language TEXT,
            out_links INTEGER,
            crawled_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            crawl_epoch INTEGER DEFAULT 1,
            last_seen_epoch INTEGER DEFAULT 1, 
            domain_rank INTEGER DEFAULT 10000000 
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS domain_authority (
            domain TEXT PRIMARY KEY, 
            rank INTEGER
        )
    """)
    conn.commit()
    conn.close()

    print("[2/3] Creating Storage DB Schema...")
    conn = sqlite3.connect(config.DB_STORAGE)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL;")
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS html_storage (
            url TEXT PRIMARY KEY,
            raw_html BLOB,
            parsed_text TEXT, 
            title TEXT,
            http_headers TEXT,
            crawled_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

    print("[3/3] Creating Search DB Schema...")
    conn = sqlite3.connect(config.DB_SEARCH)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL;")
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS index_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    c.execute("DROP TABLE IF EXISTS search_index")
    c.execute("""
        CREATE VIRTUAL TABLE search_index USING fts5(
            url UNINDEXED, 
            title, 
            description, 
            content, 
            h1, 
            h2, 
            important_text,
            tokenize='unicode61 remove_diacritics 2' 
        )
    """)
    conn.commit()
    conn.close()
    
    print("--- Schema Setup Complete ---")


def populate_seeds_and_ranks():
    print("\n--- Populating Data (Seeds & Ranks) ---")
    
    conn = sqlite3.connect(config.DB_CRAWL)
    c = conn.cursor()
    c.execute("BEGIN IMMEDIATE")

    print(f" [SEED] Injecting {len(MANUAL_SEEDS)} manual seeds...")
    for url in MANUAL_SEEDS:
        try:
            domain = url.split("/")[2]
            c.execute("INSERT OR IGNORE INTO frontier (url, domain, priority, status) VALUES (?, ?, ?, 0)", (url, domain, 1))
        except: pass

    print(" [DOWNLOAD] Fetching Top 1M Domain List (Tranco)...")
    url = "https://tranco-list.eu/top-1m.csv.zip"
    
    try:
        r = requests.get(url, stream=True)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        csv_filename = z.namelist()[0]
        
        print(f" [PROCESS] extracting {csv_filename}...")
        
        rank_batch = []
        seed_batch = []
        
        with z.open(csv_filename) as f:
            for line in f:
                parts = line.decode('utf-8').strip().split(',')
                if len(parts) == 2:
                    rank = int(parts[0])
                    domain = parts[1]
                    
                    if rank <= 1000000:
                        rank_batch.append((domain, rank))
                    
                    if rank <= 5000:
                        seed_url = f"https://{domain}/"
                        seed_batch.append((seed_url, domain, 100))

        print(f" [DB] Saving {len(rank_batch)} domain ranks...")
        c.executemany("INSERT OR REPLACE INTO domain_authority (domain, rank) VALUES (?, ?)", rank_batch)
        
        print(f" [DB] Injecting {len(seed_batch)} algorithmic seeds...")
        c.executemany("INSERT OR IGNORE INTO frontier (url, domain, priority, status) VALUES (?, ?, ?, 0)", seed_batch)
        
        conn.commit()
        print("--- Population Complete ---")
        
    except Exception as e:
        print(f" [ERROR] Data population failed: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    init_database()
    populate_seeds_and_ranks()