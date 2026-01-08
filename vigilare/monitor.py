import sqlite3, time, os, sys
from collections import deque

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config


# --- CONFIGURATION ---
REFRESH_RATE = 2
AVG_WINDOW_SIZE = 30


def get_sizes_mb():
    db_mb = 0.0
    wal_mb = 0.0
    
    paths = [config.DB_CRAWL, config.DB_STORAGE, config.DB_SEARCH]
    
    for p in paths:
        try:
            if os.path.exists(p):
                db_mb += os.path.getsize(p)
            
            wal_path = p + "-wal"
            if os.path.exists(wal_path):
                wal_mb += os.path.getsize(wal_path)
        except OSError:
            pass
            
    return (db_mb / (1024*1024), wal_mb / (1024*1024))


def get_count(db_path, sql):
    try:
        uri_path = db_path.replace("\\", "/")
        conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=1)
        c = conn.cursor()
        c.execute(sql)
        val = c.fetchone()[0]
        conn.close()
        return val
    except:
        return 0


def monitor():
    print("Initialising Monitor...")
    
    speed_history = deque(maxlen=AVG_WINDOW_SIZE)
    last_crawled = 0
    last_time = time.time()
    
    last_crawled = get_count(config.DB_CRAWL, "SELECT COUNT(*) FROM visited")

    while True:
        try:
            crawled_count = get_count(config.DB_CRAWL, "SELECT COUNT(*) FROM visited")
            
            pending_count = get_count(config.DB_CRAWL, "SELECT COUNT(*) FROM frontier WHERE status = 0")
            inflight_count = get_count(config.DB_CRAWL, "SELECT COUNT(*) FROM frontier WHERE status = 1")
            
            retry_count = get_count(config.DB_CRAWL, "SELECT COUNT(*) FROM frontier WHERE retry_count > 0")
            
            indexed_count = get_count(config.DB_SEARCH, "SELECT COUNT(*) FROM search_index")
            
            db_size, wal_size = get_sizes_mb()

            now = time.time()
            time_delta = now - last_time
            count_delta = crawled_count - last_crawled
            
            if time_delta > 0:
                instant_ppm = (count_delta / time_delta) * 60
                speed_history.append(instant_ppm)
            
            last_crawled = crawled_count
            last_time = now

            avg_ppm = sum(speed_history) / len(speed_history) if speed_history else 0
            daily_vol = avg_ppm * 60 * 24

            os.system('cls' if os.name == 'nt' else 'clear')
            
            print(f"================== VIGILO MONITOR =====================")
            print(f"")
            print(f"  PERFORMANCE")
            print(f"  -----------")
            print(f"  Speed:          {int(avg_ppm)} PPM")
            print(f"  Daily Vol:      {int(daily_vol):,} pages/24H")
            print(f"")
            print(f"  STORAGE")
            print(f"  -------")
            print(f"  DB Size:        {db_size:.1f} MB")
            print(f"  WAL Buffer:     {wal_size:.1f} MB  <-- (Writes Pending)")
            print(f"")
            print(f"  PIPELINE STATUS")
            print(f"  ---------------")
            print(f"  1. Pending:     {pending_count:,}  (Waiting in DB)")
            print(f"  2. In-Flight:   {inflight_count:,}  (Active Threads)")
            print(f"  3. Crawled:     {crawled_count:,}  (Downloaded)")
            print(f"  4. Indexed:     {indexed_count:,}  (Searchable)")
            print(f"")
            print(f"  Errors/Retries: {retry_count:,}")
            print(f"")
            print(f"=======================================================")
            print(f" Press Ctrl+C to exit monitor")

            time.sleep(REFRESH_RATE)

        except KeyboardInterrupt:
            print("\nMonitor closed.")
            sys.exit()
        except Exception as e:
            print(f"Monitor glitch: {e}")
            time.sleep(1)

if __name__ == "__main__":
    monitor()