from flask import Flask, render_template, jsonify
import sqlite3
import time
import psutil
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import config

app = Flask(__name__, template_folder="app/templates")


def get_file_size(path):
    try:
        if os.path.exists(path):
            return os.path.getsize(path) / (1024 * 1024) # MB
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
    stats = {'visited': 0, 'queue': {}, 'retries': 0, 'indexed': 0}
    
    try:
        uri = config.DB_CRAWL.replace("\\", "/")
        conn = sqlite3.connect(f"file:{uri}?mode=ro", uri=True, timeout=1)
        c = conn.cursor()
        
        stats['visited'] = c.execute("SELECT count(*) FROM visited").fetchone()[0]
        
        c.execute("SELECT status, count(*) FROM frontier GROUP BY status")
        rows = c.fetchall()
        mapping = {0: 'pending', 1: 'active', 2: 'completed', 3: 'error'}
        stats['queue'] = {mapping.get(r[0], 'unknown'): r[1] for r in rows}
        
        stats['retries'] = c.execute("SELECT count(*) FROM frontier WHERE retry_count > 0").fetchone()[0]
        conn.close()
    except Exception as e:
        stats['error_crawl'] = str(e)

    try:
        uri_search = config.DB_SEARCH.replace("\\", "/")
        conn = sqlite3.connect(f"file:{uri_search}?mode=ro", uri=True, timeout=1)
        stats['indexed'] = conn.execute("SELECT count(*) FROM search_index").fetchone()[0]
        conn.close()
    except Exception as e:
        stats['indexed'] = 0
        stats['error_index'] = str(e)

    return stats


def get_system_stats():
    return {
        'cpu': psutil.cpu_percent(interval=None),
        'ram': psutil.virtual_memory().percent,
        'disk': psutil.disk_usage(config.DATA_DIR).percent
    }


@app.route("/")
def index():
    return render_template("dashboard.html")


@app.route("/api/stats")
def api_stats():
    return jsonify({
        'counts': get_db_counts(),
        'storage': get_storage_stats(),
        'system': get_system_stats(),
        'timestamp': time.time()
    })


if __name__ == "__main__":
    print("==========================================")
    print("   VIGILARE DASHBOARD")
    print("   http://127.0.0.1:5001")
    print("==========================================")
    app.run(port=5001, debug=True, use_reloader=False)