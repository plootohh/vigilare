from flask import Flask, render_template, jsonify
import sqlite3
import time
import psutil
import os
import sys

# --- PATH SETUP ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)

try:
    import config
except ImportError:
    print("\n[CRITICAL] Could not import config.py!")
    sys.exit(1)

app = Flask(__name__, template_folder="app/templates", static_folder="app/static")


# --- STARTUP DIAGNOSTICS ---
print("\n--- DIAGNOSTICS ---")
print(f"1. Crawl DB Path:   {config.DB_CRAWL}")
if os.path.exists(config.DB_CRAWL):
    size = os.path.getsize(config.DB_CRAWL) / (1024*1024)
    print(f"   -> Status:       FOUND ({size:.2f} MB)")
else:
    print("   -> Status:       [WAITING] File not created yet")
print("-------------------\n")


def get_db_connection(db_path):
    """
    Establish a high-performance read-only SQLite connection.
    """
    if not os.path.exists(db_path):
        return None
    try:
        clean_path = os.path.abspath(db_path).replace('\\', '/')
        if not clean_path.startswith('/'):
            uri = f"file:///{clean_path}?mode=ro"
        else:
            uri = f"file:{clean_path}?mode=ro"
            
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        return conn
    except Exception as e:
        print(f" [CONN ERROR] {e}")
        return None


def get_file_size(path):
    try:
        if os.path.exists(path):
            return os.path.getsize(path) / (1024 * 1024)
    except Exception:
        pass
    return 0.0


def get_storage_stats():
    crawl = {'db': get_file_size(config.DB_CRAWL), 'wal': get_file_size(config.DB_CRAWL + "-wal")}
    storage = {'db': get_file_size(config.DB_STORAGE), 'wal': get_file_size(config.DB_STORAGE + "-wal")}
    search = {'db': get_file_size(config.DB_SEARCH), 'wal': get_file_size(config.DB_SEARCH + "-wal")}
    
    total_mb = (
        crawl['db'] + crawl['wal'] + 
        storage['db'] + storage['wal'] + 
        search['db'] + search['wal']
    )

    return {
        'crawl': crawl,
        'storage': storage,
        'search': search,
        'total_mb': total_mb
    }


def get_db_counts():
    stats = {
        'visited': 0, 
        'queue': {'active': 0, 'pending': 0, 'completed': 0, 'error': 0}, 
        'retries': 0, 
        'indexed': 0
    }
    
    # --- CRAWL DB STATS ---
    conn = get_db_connection(config.DB_CRAWL)
    if conn:
        try:
            c = conn.cursor()
            
            # 1. Total Visited
            c.execute("SELECT count(*) FROM visited")
            stats['visited'] = c.fetchone()[0]
            
            # 2. Queue Status Breakdown
            c.execute("SELECT status, count(*) FROM frontier GROUP BY status")
            rows = c.fetchall()
            
            # Mapping: 0=Pending, 1=Active, 2=Completed, 3=Error
            mapping = {0: 'pending', 1: 'active', 2: 'completed', 3: 'error'}
            
            for r in rows:
                status_name = mapping.get(r[0], 'unknown')
                stats['queue'][status_name] = r[1]
            
            # 3. Retries
            c.execute("SELECT count(*) FROM frontier WHERE retry_count > 0")
            stats['retries'] = c.fetchone()[0]
            
        except Exception as e:
            print(f" [READ ERROR] Crawl DB: {e}")
        finally:
            conn.close()

    # --- SEARCH DB STATS ---
    if os.path.exists(config.DB_SEARCH):
        conn = get_db_connection(config.DB_SEARCH)
        if conn:
            try:
                c = conn.cursor()
                c.execute("SELECT count(*) FROM search_index")
                row = c.fetchone()
                if row:
                    stats['indexed'] = row[0]
            except Exception:
                pass
            finally:
                conn.close()
    
    return stats


def get_system_stats():
    try:
        return {
            'cpu': psutil.cpu_percent(interval=0.1),
            'ram': psutil.virtual_memory().percent,
            'disk': psutil.disk_usage(config.DATA_DIR).percent
        }
    except Exception:
        return {'cpu': 0, 'ram': 0, 'disk': 0}


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/stats")
def api_stats():
    response = jsonify({
        'counts': get_db_counts(),
        'storage': get_storage_stats(),
        'system': get_system_stats(),
        'timestamp': time.time()
    })
    
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    return response


if __name__ == "__main__":
    print("==========================================")
    print("   VIGILARE DASHBOARD")
    print("   http://127.0.0.1:5001")
    print("==========================================")
    
    app.run(port=5001, debug=True, use_reloader=False)