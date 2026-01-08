import sqlite3, time, logging, requests, sys, os, threading, queue, random, json, zlib, ssl, urllib3
from urllib.parse import urljoin, urlparse
from urllib import robotparser
from requests.adapters import HTTPAdapter, Retry
from selectolax.parser import HTMLParser
from datetime import datetime, timedelta
from collections import defaultdict, deque

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from crawler.utils import canonicalise, compress_html, RotationalBloomFilter


# --- LOGGING ---
root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)
for handler in root_logger.handlers[:]:
    root_logger.removeHandler(handler)

file_formatter = logging.Formatter("%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s", datefmt='%H:%M:%S')
file_handler = logging.FileHandler(config.LOG_PATH, encoding='utf-8', mode='w') 
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(file_formatter)
root_logger.addHandler(file_handler)

stream_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt='%H:%M:%S')
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setLevel(logging.INFO) 
stream_handler.setFormatter(stream_formatter)
root_logger.addHandler(stream_handler)

logging.getLogger("urllib3").setLevel(logging.ERROR)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- QUEUES ---
FETCH_QUEUE = queue.Queue(maxsize=5000)
PARSE_QUEUE = queue.Queue()
WRITE_QUEUE = queue.Queue()

# --- BLOOM FILTER ---
BLOOM = RotationalBloomFilter(100_000_000, 7, data_dir=config.DATA_DIR)
BLOOM_LOCK = threading.Lock()


# --- DOMAIN GOVERNANCE ---
class DomainManager:
    def __init__(self):
        self.locks = defaultdict(threading.Lock)
        self.last_access = defaultdict(float)
        self.failures = defaultdict(int)
        self.page_counts = defaultdict(int)

    def can_crawl(self, domain):
        if self.page_counts[domain] >= config.MAX_PAGES_PER_DOMAIN:
            logging.debug(f"[Gov] SKIP {domain}: Hit Max Cap ({config.MAX_PAGES_PER_DOMAIN})")
            return False
        
        if self.failures[domain] > 10:
            if time.time() - self.last_access[domain] < 300: 
                logging.debug(f"[Gov] SKIP {domain}: Penalty Box (Failures: {self.failures[domain]})")
                return False
        
        if time.time() - self.last_access[domain] < config.CRAWL_DELAY:
            logging.debug(f"[Gov] SKIP {domain}: Politeness Wait")
            return False
            
        return True

    def mark_access(self, domain): self.last_access[domain] = time.time()
    def mark_success(self, domain): self.page_counts[domain] += 1
    def mark_failure(self, domain):
        self.failures[domain] += 1
        self.last_access[domain] = time.time()

DOMAIN_MGR = DomainManager()


# --- LEGACY SSL ADAPTER ---
class LegacySSLAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.options &= ~ssl.OP_NO_SSLv3
        ctx.options |= 0x4
        try:
            ctx.set_ciphers('DEFAULT:@SECLEVEL=1')
        except:
            pass
        
        self.poolmanager = urllib3.poolmanager.PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=ctx,
            **pool_kwargs
        )


# --- NETWORK ---
SESSION = requests.Session()
retries = Retry(total=0, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
adapter = LegacySSLAdapter(max_retries=retries, pool_connections=config.FETCH_THREADS, pool_maxsize=config.FETCH_THREADS)
SESSION.mount("http://", adapter)
SESSION.mount("https://", adapter)


# --- ROBOTS CACHE ---
class RobotParser(robotparser.RobotFileParser):
    def __init__(self, url=''):
        super().__init__(url)


ROBOTS_CACHE = {}
ROBOTS_LOCK = threading.Lock()
ROBOTS_TTL = 86400


def check_robots_allow(domain, url):    
    now = time.time()
    rp = None
    with ROBOTS_LOCK:
        if domain in ROBOTS_CACHE:
            parser, ts = ROBOTS_CACHE[domain]
            if now - ts < ROBOTS_TTL:
                rp = parser
    
    if not rp:
        logging.debug(f"[Robots] Fetching for {domain}")
        rp = RobotParser()
        rp.set_url(f"http://{domain}/robots.txt")
        try:
            rp.read()
        except Exception as e:
            logging.debug(f"[Robots] Failed {domain}: {e}")
        
        with ROBOTS_LOCK:
            ROBOTS_CACHE[domain] = (rp, now)
    
    try:
        allowed = rp.can_fetch(config.USER_AGENT, url)
        if not allowed:
            logging.debug(f"[Robots] DENIED {url}")
        return allowed
    except:
        return True


# --- WORKER: FETCHER ---
def fetch_worker():
    while True:
        try:
            url, retry_count = FETCH_QUEUE.get()
            domain = urlparse(url).netloc
            
            if not DOMAIN_MGR.can_crawl(domain):
                if DOMAIN_MGR.page_counts[domain] >= config.MAX_PAGES_PER_DOMAIN:
                    WRITE_QUEUE.put(('status_update', (2, url)))
                else:
                    FETCH_QUEUE.put((url, retry_count))
                    time.sleep(0.1)
                FETCH_QUEUE.task_done()
                continue

            if not check_robots_allow(domain, url):
                WRITE_QUEUE.put(('status_update', (3, url)))
                FETCH_QUEUE.task_done()
                continue
            
            with DOMAIN_MGR.locks[domain]:
                DOMAIN_MGR.mark_access(domain)
                start_t = time.time()
                result = download_page(url)
                dur = time.time() - start_t

            if result['error']:
                logging.debug(f"[Fetch] FAIL {url} ({result['error']}) {dur:.2f}s")
                DOMAIN_MGR.mark_failure(domain)
                if retry_count < 2:
                    WRITE_QUEUE.put(('retry', (url, retry_count + 1)))
                else:
                    WRITE_QUEUE.put(('status_update', (3, url)))
            else:
                logging.debug(f"[Fetch] OK {url} {dur:.2f}s")
                DOMAIN_MGR.mark_success(domain)
                PARSE_QUEUE.put((url, result, retry_count))
            
            FETCH_QUEUE.task_done()
        except Exception as e:
            logging.error(f"Fetch Error: {e}", exc_info=True)
            time.sleep(0.1)


# --- WORKER: PROCESSOR ---
def parse_worker():
    while True:
        try:
            url, result, retry_count = PARSE_QUEUE.get()
            start_t = time.time()
            raw_bytes = result['content']
            
            try:
                html_str = raw_bytes.decode('utf-8')
            except UnicodeDecodeError:
                html_str = raw_bytes.decode('latin-1', errors='ignore')
            
            tree = HTMLParser(html_str)
            
            for tag in tree.css('script, style, nav, footer, header, noscript, iframe, svg'):
                tag.decompose()

            title_node = tree.css_first('title')
            title = title_node.text(strip=True) if title_node else ""
            
            desc = ""
            meta = tree.css_first('meta[name="description"]')
            if meta: desc = meta.attributes.get('content', '')
            
            content = ""
            if tree.body:
                content = tree.body.text(separator=' ', strip=True)
                content = " ".join(content.split())
                if len(content) > config.MAX_TEXT_CHARS:
                    content = content[:config.MAX_TEXT_CHARS]
            
            links = []
            if FETCH_QUEUE.qsize() < 5000:
                for node in tree.css('a[href]'):
                    href = node.attributes.get('href')
                    try:
                        joined_url = urljoin(url, href)
                        clean = canonicalise(joined_url)
                        if clean: links.append(clean)
                    except ValueError:
                        continue

            data_package = {
                'url': url,
                'title': title,
                'description': desc,
                'content': content,
                'raw_html': compress_html(raw_bytes), 
                'headers': json.dumps(dict(result['headers'])),
                'status': result['status'],
                'out_links': len(links),
                'links_found': links
            }
            
            logging.debug(f"[Parse] {url} -> {len(links)} links ({time.time()-start_t:.3f}s)")
            
            WRITE_QUEUE.put(('save_page', data_package))
            PARSE_QUEUE.task_done()
        except Exception as e:
            logging.error(f"Parse Error: {e}", exc_info=True)
            PARSE_QUEUE.task_done()


# --- WORKER: DB WRITER ---
def db_writer():
    logging.info(" [DB] Writer started.")
    conn_crawl = sqlite3.connect(config.DB_CRAWL, timeout=60)
    conn_crawl.execute("PRAGMA journal_mode=WAL")
    conn_crawl.execute("PRAGMA synchronous=OFF")
    conn_storage = sqlite3.connect(config.DB_STORAGE, timeout=60)
    conn_storage.execute("PRAGMA journal_mode=WAL")
    conn_storage.execute("PRAGMA synchronous=OFF") 
    
    if hasattr(BLOOM, 'load'):
        BLOOM.load()
    last_bloom_save = time.time()

    while True:
        try:
            batch_visited = []
            batch_storage = []
            batch_frontier = []
            batch_status = []
            batch_reserve = [] 
            batch_retries = []

            while not WRITE_QUEUE.empty() and len(batch_visited) < 2000:
                msg_type, payload = WRITE_QUEUE.get()
                
                if msg_type == 'save_page':
                    p = payload
                    batch_visited.append((
                        p['url'], p['title'], p['description'], p['status'], 
                        None, p['out_links'], 
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        config.CRAWL_EPOCH, config.CRAWL_EPOCH
                    ))
                    batch_storage.append((
                        p['url'], p['raw_html'], p['content'], p['title'], p['headers'], 
                        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    ))
                    batch_status.append((2, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), p['url']))
                    
                    for link in p['links_found']:
                        with BLOOM_LOCK:
                            if not BLOOM.lookup(link):
                                BLOOM.add(link)
                                batch_frontier.append((link, urlparse(link).netloc))

                elif msg_type == 'status_update':
                    status, url = payload
                    batch_status.append((status, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), url))
                
                elif msg_type == 'retry':
                    url, retry = payload 
                    batch_retries.append((retry, url))
                    
                elif msg_type == 'reserve':
                    urls = payload
                    batch_reserve.extend([(u,) for u in urls])

                WRITE_QUEUE.task_done()

            if any([batch_visited, batch_status, batch_frontier, batch_reserve, batch_retries]):
                commit_start = time.time()
                try:
                    conn_crawl.execute("BEGIN IMMEDIATE")
                    if batch_visited:
                        conn_crawl.executemany("""
                            INSERT OR REPLACE INTO visited 
                            (url, title, description, http_status, language, out_links, crawled_at, crawl_epoch, last_seen_epoch)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, batch_visited)
                    
                    if batch_status:
                        conn_crawl.executemany("UPDATE frontier SET status=?, next_crawl_time=? WHERE url=?", batch_status)
                    
                    if batch_frontier:
                        conn_crawl.executemany("INSERT OR IGNORE INTO frontier (url, domain) VALUES (?, ?)", batch_frontier)
                    
                    if batch_reserve:
                        conn_crawl.executemany("UPDATE frontier SET status=1, reserved_at=CURRENT_TIMESTAMP WHERE url=?", batch_reserve)

                    if batch_retries:
                        conn_crawl.executemany("UPDATE frontier SET status=0, priority=50, retry_count=? WHERE url=?", batch_retries)
                        
                    conn_crawl.commit()
                except Exception as e:
                    logging.error(f"Crawl DB Write Error: {e}", exc_info=True)
                    try: conn_crawl.rollback()
                    except: pass
                
                try:
                    if batch_storage:
                        conn_storage.execute("BEGIN IMMEDIATE")
                        conn_storage.executemany("""
                            INSERT OR REPLACE INTO html_storage (url, raw_html, parsed_text, title, http_headers, crawled_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, batch_storage)
                        conn_storage.commit()
                except Exception as e:
                    logging.error(f"Storage DB Write Error: {e}", exc_info=True)
                
                logging.debug(f"[DB] Commit: {len(batch_visited)} visited, {len(batch_frontier)} new links ({time.time()-commit_start:.3f}s)")

            if time.time() - last_bloom_save > 300:
                if hasattr(BLOOM, 'save'): BLOOM.save()
                last_bloom_save = time.time()
            else:
                time.sleep(0.05)

        except Exception as e:
            logging.error(f"DB Thread Error: {e}", exc_info=True)
            time.sleep(1)


# --- DISPATCHER ---
def dispatcher_loop():
    logging.info(" [SYS] Dispatcher started.")
    conn = sqlite3.connect(config.DB_CRAWL, timeout=60)
    dispatched_cache = deque(maxlen=20000) 
    
    while True:
        if FETCH_QUEUE.qsize() < 2500:
            try:
                logging.debug("[Dispatch] Querying DB for jobs...")
                start_t = time.time()
                
                cursor = conn.execute(f"""
                    SELECT url, retry_count FROM frontier 
                    WHERE status = 0 
                    OR (status = 1 AND reserved_at < datetime('now', '-15 minutes'))
                    ORDER BY priority ASC 
                    LIMIT {config.BATCH_SIZE}
                """)
                rows = cursor.fetchall()
                
                valid_rows = [r for r in rows if r[0] not in dispatched_cache]
                
                if valid_rows:
                    random.shuffle(valid_rows)
                    
                    urls = [r[0] for r in valid_rows]
                    WRITE_QUEUE.put(('reserve', urls))
                    dispatched_cache.extend(urls)
                    
                    for r in valid_rows:
                        FETCH_QUEUE.put(r)
                    
                    logging.info(f" [SYS] Dispatched {len(valid_rows)} URLs ({time.time()-start_t:.3f}s).")
                else:
                    logging.debug("[Dispatch] Frontier empty. Sleeping.")
                    time.sleep(2)
            except Exception as e:
                logging.error(f"Dispatch Error: {e}", exc_info=True)
                time.sleep(5)
        else:
            time.sleep(0.5)


def download_page(url):
    res = {'content': None, 'headers': {}, 'status': 0, 'error': None}
    try:
        r = SESSION.get(url, headers={'User-Agent': config.USER_AGENT}, timeout=(3, 10))
        res['status'] = r.status_code
        res['headers'] = r.headers
        
        if r.status_code != 200:
            res['error'] = f"HTTP_{r.status_code}"
            return res
        
        if "text/html" not in r.headers.get("Content-Type", "").lower():
            res['error'] = "NOT_HTML"
            return res
        
        if len(r.content) > config.MAX_BYTES:
            res['error'] = "TOO_LARGE"
            return res
        
        res['content'] = r.content
        return res
    except Exception as e:
        res['error'] = f"NET_ERROR: {str(e)[:50]}"
    return res


def recover():
    try:
        conn = sqlite3.connect(config.DB_CRAWL)
        conn.execute("UPDATE frontier SET status=0 WHERE status=1")
        conn.commit()
        conn.close()
    except: pass