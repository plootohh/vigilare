import sqlite3
import time
import os
import sys
import config
import re
from datetime import datetime
from collections import Counter
from langdetect import detect
import networkx

# --- CONFIGURATION ---
BATCH_SIZE = 2500
MIN_BATCH_SIZE = 1000
MAX_WAIT_TIME = 120
STATE_FILE = "indexer_state.txt"
RECYCLE_CONN_EVERY = 100
PAGERANK_INTERVAL = 600

# --- VOCABULARY SETTINGS ---
VOCAB_REGEX = re.compile(r'\b[a-z]{3,15}\b') 


def get_storage_conn():
    conn = sqlite3.connect(config.DB_STORAGE, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA mmap_size=30000000000;") 
    return conn


def get_search_conn():
    conn = sqlite3.connect(config.DB_SEARCH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    
    try:
        cursor = conn.execute("SELECT sql FROM sqlite_master WHERE name='search_vocab'")
        row = cursor.fetchone()
        if row and "USING fts5vocab" in row[0]:
            print(" [WARN] Detected read-only vocab table. Dropping it...")
            conn.execute("DROP TABLE search_vocab")
    except Exception:
        pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_vocab (
            term TEXT PRIMARY KEY,
            doc_freq INTEGER
        )
    """)
    return conn


def get_crawl_conn():
    conn = sqlite3.connect(config.DB_CRAWL, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def get_last_indexed_id():
    if not os.path.exists(STATE_FILE):
        return 0
    try:
        with open(STATE_FILE, "r") as f:
            content = f.read().strip()
            return int(content) if content else 0
    except Exception:
        return 0


def update_last_indexed_id(rowid):
    try:
        with open(STATE_FILE, "w") as f:
            f.write(str(rowid))
    except Exception:
        pass


def update_vocabulary(conn, text_batch):
    try:
        batch_counts = Counter()
        for text in text_batch:
            if not text:
                continue
            words = VOCAB_REGEX.findall(text.lower())
            batch_counts.update(words)
        
        if not batch_counts:
            return

        cursor = conn.cursor()
        cursor.executemany("""
            INSERT INTO search_vocab (term, doc_freq) VALUES (?, ?)
            ON CONFLICT(term) DO UPDATE SET doc_freq = doc_freq + excluded.doc_freq
        """, batch_counts.items())
    except Exception as e:
        print(f" [WARN] Vocab learning failed: {e}")


def run_pagerank_job():
    print("\n [RANK] Starting PageRank calculation...")
    
    MAX_RETRIES = 3
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            start_t = time.time()
            
            uri_path = config.DB_CRAWL.replace("\\", "/")
            conn = sqlite3.connect(f"file:{uri_path}", uri=True, timeout=90)
            cursor = conn.cursor()
            
            cursor.execute("SELECT source_url, target_url FROM link_graph")
            edges = cursor.fetchall()
            conn.close()
            
            if not edges:
                print(" [RANK] No links found in graph yet. Skipping.")
                return

            G = networkx.DiGraph()
            G.add_edges_from(edges)
            
            scores = networkx.pagerank(G, alpha=0.85, max_iter=100)
            
            batch_updates = [(score * 100000, url) for url, score in scores.items()]
            
            conn_write = sqlite3.connect(config.DB_CRAWL, timeout=90)
            conn_write.execute("PRAGMA journal_mode=WAL")
            conn_write.execute("PRAGMA synchronous=OFF")
            
            conn_write.executemany("UPDATE visited SET page_rank = ? WHERE url = ?", batch_updates)
            conn_write.commit()
            conn_write.close()
            
            print(f" [RANK] Updated {len(batch_updates)} pages in {time.time() - start_t:.2f}s.")
            return
            
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                print(f" [RANK] Locked (Attempt {attempt}/{MAX_RETRIES}). Waiting 10s...")
                time.sleep(10)
            else:
                print(f" [RANK] SQLite Error: {e}")
                return
        except Exception as e:
            print(f" [RANK] General Error: {e}")
            return

    print(f" [RANK] Skipped. Database was too busy after {MAX_RETRIES} attempts.")


def run_indexer():
    print("--- Vigilare Indexer ---")
    
    conn_storage = get_storage_conn()
    conn_search = get_search_conn()
    conn_crawl = get_crawl_conn()
    
    last_id = get_last_indexed_id()
    print(f" [INFO] Resuming from Storage Row ID: {last_id}")
    
    batch_counter = 0
    last_pagerank_time = time.time()
    last_process_time = time.time()

    while True:
        try:
            if batch_counter >= RECYCLE_CONN_EVERY:
                conn_storage.close()
                conn_search.close()
                conn_crawl.close()
                conn_storage = get_storage_conn()
                conn_search = get_search_conn()
                conn_crawl = get_crawl_conn()
                batch_counter = 0
            
            if time.time() - last_pagerank_time > PAGERANK_INTERVAL:
                run_pagerank_job()
                last_pagerank_time = time.time()

            try:
                c_check = conn_storage.cursor()
                c_check.execute("SELECT MAX(rowid) FROM html_storage")
                max_row = c_check.fetchone()[0]
                current_max_id = max_row if max_row else 0
                
                pending_count = current_max_id - last_id
                
                if pending_count < MIN_BATCH_SIZE and (time.time() - last_process_time < MAX_WAIT_TIME):
                    sys.stdout.write(f"\r[{datetime.now().strftime('%H:%M:%S')}] Buffering... {pending_count}/{MIN_BATCH_SIZE} pending   ")
                    sys.stdout.flush()
                    time.sleep(2)
                    continue
            except Exception:
                pass

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
            vocab_learning_buffer = []
            max_id_in_batch = last_id
            
            print(f"\n [JOB] Processing {len(rows)} pages (Starting ID: {rows[0][0]})...")

            for r in rows:
                row_id, url, text, title = r
                
                if row_id > max_id_in_batch:
                    max_id_in_batch = row_id
                
                final_title = title if title else (url)
                if not final_title and text:
                    lines = text.split('\n')
                    for line in lines[:3]:
                        if line.strip():
                            final_title = line.strip()[:80]
                            break

                learning_text = (final_title or "") + " " + (text[:500] if text else "")
                vocab_learning_buffer.append(learning_text)

                lang = "unknown"
                if text and len(text) > 200:
                    try:
                        lang = detect(text[:1000])
                    except Exception:
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
                
                update_vocabulary(conn_search, vocab_learning_buffer)
                
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
            last_process_time = time.time()
            
            elapsed = time.time() - start_time
            rate = int(len(rows) / elapsed) if elapsed > 0 else 0
            print(f"    -> Indexed & Learned in {elapsed:.2f}s ({rate} pages/sec)")

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