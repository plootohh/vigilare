import sqlite3
import time
import os
import sys
from collections import deque

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config


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


def get_stats_batch():
    stats = {
        'visited': 0,
        'pending': 0,
        'active': 0,
        'retries': 0,
        'indexed': 0
    }
    
    try:
        uri_path = config.DB_CRAWL.replace("\\", "/")
        conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=5)
        c = conn.cursor()
        
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA synchronous=OFF;")
        c.execute("PRAGMA query_only=1;")
        
        c.execute("SELECT COUNT(1) FROM visited")
        stats['visited'] = c.fetchone()[0]
        
        c.execute("SELECT COUNT(1) FROM frontier WHERE status = 0")
        stats['pending'] = c.fetchone()[0]
        
        c.execute("SELECT COUNT(1) FROM frontier WHERE status = 1")
        stats['active'] = c.fetchone()[0]
        
        c.execute("SELECT COUNT(1) FROM frontier WHERE retry_count > 0")
        stats['retries'] = c.fetchone()[0]
        
        conn.close()
    except Exception:
        pass

    try:
        uri_path = config.DB_SEARCH.replace("\\", "/")
        conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True, timeout=1)
        c = conn.cursor()
        c.execute("SELECT COUNT(1) FROM search_index")
        stats['indexed'] = c.fetchone()[0]
        conn.close()
    except Exception:
        pass

    return stats


def monitor():
    print("Initialising Monitor (this may take a moment to cache the DB)...")
    
    speed_history = deque(maxlen=AVG_WINDOW_SIZE)
    
    # Initial Fetch
    initial_stats = get_stats_batch()
    last_crawled = initial_stats['visited']
    last_time = time.time()

    while True:
        try:
            current_stats = get_stats_batch()
            
            crawled_count = current_stats['visited']
            db_size, wal_size = get_sizes_mb()

            now = time.time()
            time_delta = now - last_time
            count_delta = crawled_count - last_crawled
            
            if time_delta > 0:
                instant_ppm = (count_delta / time_delta) * 60
                if instant_ppm >= 0: 
                    speed_history.append(instant_ppm)
            
            last_crawled = crawled_count
            last_time = now

            avg_ppm = sum(speed_history) / len(speed_history) if speed_history else 0
            daily_vol = avg_ppm * 60 * 24

            # Display
            os.system('cls' if os.name == 'nt' else 'clear')
            
            print("================== VIGILARE MONITOR =====================")
            print("")
            print("  PERFORMANCE")
            print("  -----------")
            print(f"  Speed:          {int(avg_ppm)} PPM")
            print(f"  Daily Vol:      {int(daily_vol):,} pages/24H")
            print("")
            print("  STORAGE")
            print("  -------")
            print(f"  DB Size:        {db_size:.1f} MB")
            print(f"  WAL Buffer:     {wal_size:.1f} MB")
            print("")
            print("  PIPELINE STATUS")
            print("  ---------------")
            print(f"  1. Pending:     {current_stats['pending']:,}        (Waiting in DB)")
            
            print(f"  2. Active:      {current_stats['active']:,}         (Being Downloaded)")
            print(f"  3. Crawled:     {current_stats['visited']:,}         (Downloaded)")
            print(f"  4. Indexed:     {current_stats['indexed']:,}         (Searchable)")
            print("")
            print(f"  Errors/Retries: {current_stats['retries']:,}")
            print("")
            print("=======================================================")
            print(" Press Ctrl+C to exit monitor")

            time.sleep(REFRESH_RATE)

        except KeyboardInterrupt:
            print("\nMonitor closed.")
            sys.exit()
        except Exception as e:
            print(f"Monitor error: {e}") 
            time.sleep(1)


if __name__ == "__main__":
    monitor()