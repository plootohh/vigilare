import sqlite3
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config


def checkpoint_database(name, db_path):
    print(f"\n--- Processing {name} ---")
    wal_path = db_path + "-wal"
    
    if os.path.exists(wal_path):
        size_mb = os.path.getsize(wal_path) / (1024 * 1024)
        print(f"Current WAL Size: {size_mb:.2f} MB")
    else:
        print("No WAL file found (Already clean).")
        return

    print(f"Connecting to {name}...")
    try:
        conn = sqlite3.connect(db_path, timeout=60)
        
        print("Forcing WAL Checkpoint (TRUNCATE)...")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.commit()
        conn.close()
        
        if os.path.exists(wal_path):
            new_size = os.path.getsize(wal_path) / (1024 * 1024)
            print(f"New WAL Size:     {new_size:.2f} MB")
            if new_size < 1.0:
                print("SUCCESS: WAL drained.")
            else:
                print("WARNING: WAL is still large. Another process (Crawler/Indexer) might be holding a lock.")
        else:
            print("SUCCESS: WAL file removed.")
            
    except Exception as e:
        print(f"Error flushing {name}: {e}")


def flush_wal():
    databases = [
        ("Crawl DB", config.DB_CRAWL),
        ("Storage DB", config.DB_STORAGE),
        ("Search DB", config.DB_SEARCH)
    ]
    
    for name, path in databases:
        checkpoint_database(name, path)


if __name__ == "__main__":
    flush_wal()