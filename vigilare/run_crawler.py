import threading
import time
import sys
import os
import sqlite3
import queue

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
from crawler.bot import (
    fetch_worker, 
    parse_worker, 
    db_writer, 
    dispatcher_loop, 
    recover, 
    FETCH_QUEUE, 
    PARSE_QUEUE, 
    WRITE_QUEUE
)

# --- CONFIGURATION ---
FETCH_THREADS = config.FETCH_THREADS
PARSE_THREADS = config.PARSE_THREADS

def monitor_loop():
    start_time = time.time()
    while True:
        uptime = int(time.time() - start_time)
        m, s = divmod(uptime, 60)
        h, m = divmod(m, 60)
        
        q_fetch = FETCH_QUEUE.qsize()
        q_parse = PARSE_QUEUE.qsize()
        q_write = WRITE_QUEUE.qsize()
        
        sys.stdout.write(
            f"\r[RUNTIME {h:02}:{m:02}:{s:02}] "
            f"FetchQ: {q_fetch:<6} | "
            f"ParseQ: {q_parse:<4} | "
            f"WriteQ: {q_write:<4} | "
            f"Active Threads: {threading.active_count():<3}      "
        )
        sys.stdout.flush()
        time.sleep(1)


def main():
    os.system('cls' if os.name == 'nt' else 'clear')
    
    print("==========================================")
    print("   VIGILARE CRAWLER ENGINE                  ")
    print("==========================================")
    print(f" Database: {config.DB_CRAWL}")
    print(f" Fetchers: {FETCH_THREADS}")
    print(f" Parsers:  {PARSE_THREADS}")
    print(" Logs:     data/vigilare.log")
    print("==========================================\n")

    print(" [INIT] Recovering database state...")
    recover()
    
    threads = []
    
    print(" [START] Launching DB Writer...")
    t_db = threading.Thread(target=db_writer, name="DB_Writer", daemon=True)
    t_db.start()
    threads.append(t_db)
    
    print(" [START] Launching Dispatcher...")
    t_disp = threading.Thread(target=dispatcher_loop, name="Dispatcher", daemon=True)
    t_disp.start()
    threads.append(t_disp)
    
    print(f" [START] Spawning {FETCH_THREADS} Fetch Workers...")
    for i in range(FETCH_THREADS):
        t = threading.Thread(target=fetch_worker, name=f"Fetcher-{i}", daemon=True)
        t.start()
        threads.append(t)
        
    print(f" [START] Spawning {PARSE_THREADS} Parse Workers...")
    for i in range(PARSE_THREADS):
        t = threading.Thread(target=parse_worker, name=f"Parser-{i}", daemon=True)
        t.start()
        threads.append(t)

    print("\n [SYSTEM] Engine is running. Press Ctrl+C to STOP gracefully.\n")

    try:
        monitor_loop()
    except KeyboardInterrupt:
        print("\n\n [STOP] Interrupted! Initiating graceful shutdown...")
        print(" [STOP] Draining queues to save progress... (Press Ctrl+C again to FORCE QUIT)")
        
        try:
            while not FETCH_QUEUE.empty():
                try:
                    FETCH_QUEUE.get_nowait()
                    FETCH_QUEUE.task_done()
                except queue.Empty:
                    break
            
            while not PARSE_QUEUE.empty() or not WRITE_QUEUE.empty():
                q_parse = PARSE_QUEUE.qsize()
                q_write = WRITE_QUEUE.qsize()
                
                sys.stdout.write(f"\r [STOP] Saving Data... ParseQ: {q_parse:<5} | WriteQ: {q_write:<5}   ")
                sys.stdout.flush()
                time.sleep(0.5)
            
            print("\n [STOP] All data saved successfully.")

        except KeyboardInterrupt:
            print("\n [STOP] FORCE QUIT DETECTED. Some data in memory may be lost.")

        print(" [STOP] Checkpointing databases (Merging WAL files)...")
        try:
            for db_path in [config.DB_CRAWL, config.DB_STORAGE, config.DB_SEARCH]:
                if os.path.exists(db_path):
                    conn = sqlite3.connect(db_path)
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                    conn.close()
        except Exception as e:
            print(f" [WARN] Cleanup failed: {e}")

        print(" [STOP] Crawler stopped.")


if __name__ == "__main__":
    main()