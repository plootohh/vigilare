import sqlite3
import os
import config

def fix_wal_bloat():
    print(f"--- Draining WAL File ---")
    db_path = config.DB_PATH
    wal_path = db_path + "-wal"
    
    if os.path.exists(wal_path):
        size_mb = os.path.getsize(wal_path) / (1024 * 1024)
        print(f"Current WAL Size: {size_mb:.2f} MB")
    
    print("Connecting to database...")
    conn = sqlite3.connect(db_path, timeout=600)
    
    print("Forcing WAL Checkpoint (TRUNCATE)... This may take a minute.")
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.commit()
        print("Checkpoint complete.")
    except Exception as e:
        print(f"Error: {e}")
    
    conn.close()
    
    if os.path.exists(wal_path):
        new_size = os.path.getsize(wal_path) / (1024 * 1024)
        print(f"New WAL Size: {new_size:.2f} MB")
        if new_size < 1.0:
            print("SUCCESS: WAL file drained.")
        else:
            print("WARNING: WAL is still large. Something might be holding a lock.")

if __name__ == "__main__":
    fix_wal_bloat()