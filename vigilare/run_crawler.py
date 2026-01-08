import threading, time, sys, os, sqlite3, logging

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
    try:
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
                f"Active Threads: {threading.active_count()}"
            )
            sys.stdout.flush()
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def main():
    os.system('cls' if os.name == 'nt' else 'clear')
    
    print(f"==========================================")
    print(f"   VIGILO CRAWLER ENGINE                  ")
    print(f"==========================================")
    print(f" Database: {config.DB_CRAWL}")
    print(f" Fetchers: {FETCH_THREADS}")
    print(f" Parsers:  {PARSE_THREADS}")
    print(f"==========================================\n")

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

    print("\n [SYSTEM] Engine is running. Press Ctrl+C to stop.\n")

    try:
        monitor_loop()
    except KeyboardInterrupt:
        print("\n\n [STOP] Shutdown signal received!")
        print(" [STOP] Waiting for queues to drain (5s)...")
        time.sleep(2) 
        print(" [STOP] Shutdown complete.")
        sys.exit(0)


if __name__ == "__main__":
    main()