import sqlite3, time, os, sys, config, zlib
from datetime import datetime
from langdetect import detect

# --- CONFIGURATION ---
BATCH_SIZE = 2500
STATE_FILE = "indexer_state.txt"
RECYCLE_CONN_EVERY = 100


def get_storage_conn():
    conn = sqlite3.connect(config.DB_STORAGE, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA mmap_size=30000000000;") 
    return conn


def get_search_conn():
    conn = sqlite3.connect(config.DB_SEARCH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def get_crawl_conn():
    conn = sqlite3.connect(config.DB_CRAWL, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def get_last_indexed_id():
    if not os.path.exists(STATE_FILE): return 0
    try:
        with open(STATE_FILE, "r") as f:
            content = f.read().strip()
            return int(content) if content else 0
    except: return 0


def update_last_indexed_id(rowid):
    try:
        with open(STATE_FILE, "w") as f:
            f.write(str(rowid))
    except: pass


def run_indexer():
    print("--- Vigilo Indexer ---")
    
    conn_storage = get_storage_conn()
    conn_search = get_search_conn()
    conn_crawl = get_crawl_conn()
    
    last_id = get_last_indexed_id()
    print(f" [INFO] Resuming from Storage Row ID: {last_id}")
    
    batch_counter = 0

    while True:
        try:
            if batch_counter >= RECYCLE_CONN_EVERY:
                conn_storage.close(); conn_search.close(); conn_crawl.close()
                conn_storage = get_storage_conn(); conn_search = get_search_conn(); conn_crawl = get_crawl_conn()
                batch_counter = 0

            c_store = conn_storage.cursor()
            c_store.execute("""
                SELECT rowid, url, parsed_text, title 
                FROM html_storage 
                WHERE rowid > ? 
                AND parsed_text IS NOT NULL
                ORDER BY rowid ASC 
                LIMIT ?
            """, (last_id, BATCH_SIZE))
            
            rows = c_store.fetchall()

            if not rows:
                sys.stdout.write(f"\r[{datetime.now().strftime('%H:%M:%S')}] Waiting for new pages...")
                sys.stdout.flush()
                time.sleep(2)
                continue

            start_time = time.time()
            to_insert = []
            lang_updates = []
            max_id_in_batch = last_id
            
            print(f"\n [JOB] Processing {len(rows)} pages (Starting ID: {rows[0][0]})...")

            for r in rows:
                row_id, url, text, title = r
                
                if row_id > max_id_in_batch: max_id_in_batch = row_id
                
                final_title = title if title else (text[:80].split('\n')[0] if text else url)

                lang = "unknown"
                if text and len(text) > 200:
                    try:
                        lang = detect(text[:1000])
                    except:
                        pass

                to_insert.append((
                    url, final_title, "", text, "", "", "" 
                ))
                
                if lang != "unknown":
                    lang_updates.append((lang, url))

            if to_insert:
                c_search = conn_search.cursor()
                c_search.execute("BEGIN IMMEDIATE")
                c_search.executemany("""
                    INSERT INTO search_index (url, title, description, content, h1, h2, important_text) 
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, to_insert)
                conn_search.commit()

            if lang_updates:
                try:
                    c_crawl = conn_crawl.cursor()
                    c_crawl.execute("BEGIN IMMEDIATE")
                    c_crawl.executemany("UPDATE visited SET language=? WHERE url=?", lang_updates)
                    conn_crawl.commit()
                except Exception as e:
                    print(f" [WARN] Lang update failed (non-critical): {e}")

            update_last_indexed_id(max_id_in_batch)
            last_id = max_id_in_batch
            batch_counter += 1
            
            elapsed = time.time() - start_time
            rate = int(len(rows) / elapsed) if elapsed > 0 else 0
            print(f"    -> Indexed in {elapsed:.2f}s ({rate} pages/sec)")

        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                time.sleep(1)
            else:
                print(f" [ERROR] SQLite: {e}")
                time.sleep(5)
        except KeyboardInterrupt:
            print("\n [STOP] Indexer stopping...")
            break
        except Exception as e:
            print(f" [CRITICAL] {e}")
            time.sleep(5)

    conn_storage.close()
    conn_search.close()
    conn_crawl.close()


if __name__ == "__main__":
    run_indexer()