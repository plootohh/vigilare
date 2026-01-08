import sqlite3, math, mmh3, pickle, os, zlib
from urllib.parse import urlparse, parse_qsl, urlencode


class RotationalBloomFilter:
    def __init__(self, capacity=100_000_000, hash_count=7, data_dir="data"):
        self.capacity = capacity
        self.hash_count = hash_count
        self.hot_path = os.path.join(data_dir, "bloom_hot.bin")
        self.cold_path = os.path.join(data_dir, "bloom_cold.bin")
        
        self.hot = self._create_empty()
        self.cold = self._create_empty()
        
        self.insert_count = 0
        self.rotate_threshold = int(capacity * 0.5)
        
        if not os.path.exists(data_dir):
            try: os.makedirs(data_dir)
            except: pass

    def _create_empty(self):
        return bytearray(math.ceil(self.capacity / 8))

    def _add_to_array(self, arr, string):
        for seed in range(self.hash_count):
            result = mmh3.hash(string, seed) % self.capacity
            byte_index = result // 8
            bit_index = result % 8
            arr[byte_index] |= (1 << bit_index)

    def _check_array(self, arr, string):
        for seed in range(self.hash_count):
            result = mmh3.hash(string, seed) % self.capacity
            byte_index = result // 8
            bit_index = result % 8
            if not (arr[byte_index] & (1 << bit_index)):
                return False
        return True

    def add(self, string):
        self._add_to_array(self.hot, string)
        self.insert_count += 1
        
        if self.insert_count >= self.rotate_threshold:
            self.rotate()

    def lookup(self, string):
        if self._check_array(self.hot, string): return True
        if self._check_array(self.cold, string): return True
        return False

    def rotate(self):
        print(" [SYSTEM] Rotating Bloom Filters (Hot -> Cold)...")
        self.cold = self.hot
        self.hot = self._create_empty()
        self.insert_count = 0
        self.save()

    def save(self):
        try:
            with open(self.hot_path, 'wb') as f: pickle.dump(self.hot, f)
            with open(self.cold_path, 'wb') as f: pickle.dump(self.cold, f)
            return True
        except Exception as e:
            print(f"Bloom Save Error: {e}")
            return False

    def load(self):
        try:
            if os.path.exists(self.hot_path):
                with open(self.hot_path, 'rb') as f: self.hot = pickle.load(f)
            if os.path.exists(self.cold_path):
                with open(self.cold_path, 'rb') as f: self.cold = pickle.load(f)
            return True
        except:
            return False


BloomFilter = RotationalBloomFilter 


def get_high_perf_connection(db_path):
    conn = sqlite3.connect(db_path, timeout=60, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;") 
    conn.execute("PRAGMA synchronous=NORMAL;") 
    conn.execute("PRAGMA cache_size=-64000;")
    conn.execute("PRAGMA temp_store=MEMORY;") 
    conn.execute("PRAGMA mmap_size=30000000000;")
    return conn


def compress_html(data):
    if not data: return None
    if isinstance(data, str): data = data.encode('utf-8')
    try: return zlib.compress(data)
    except: return None


def decompress_html(blob_data):
    if not blob_data: return ""
    try: return zlib.decompress(blob_data).decode('utf-8', errors='replace')
    except: return ""


def canonicalise(url):
    try:
        url = str(url).strip()
        if not url: return None
        if '#' in url: url = url.split('#')[0]
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"): return None
        
        netloc = parsed.hostname
        if not netloc: return None
        netloc = netloc.lower()
        if parsed.port:
            if (parsed.scheme == "http" and parsed.port != 80) or \
                (parsed.scheme == "https" and parsed.port != 443):
                netloc += f":{parsed.port}"
        
        path = parsed.path.replace("//", "/")
        if not path: path = "/"
        
        ignore_exts = {
            '.png','.jpg','.jpeg','.gif','.css','.js','.ico','.svg',
            '.pdf','.zip','.exe','.mp4','.mp3','.wav','.avi','.mov',
            '.xml','.json','.txt','.bmp','.tif','.tiff','.woff','.woff2',
            '.ttf','.eot','.dmg','.iso','.bin','.dat','.apk','.rar'
        }
        if any(path.lower().endswith(ext) for ext in ignore_exts): return None

        clean_query = ""
        if parsed.query:
            qs = parse_qsl(parsed.query)
            filtered_qs = []
            for k, v in qs:
                k_low = k.lower()
                if k_low.startswith("utm_") or k_low in {'fbclid', 'gclid', 'ref', 'source', 'yclid', '_ga'}:
                    continue
                filtered_qs.append((k, v))
            if filtered_qs:
                filtered_qs.sort() 
                clean_query = urlencode(filtered_qs)

        clean_url = f"{parsed.scheme}://{netloc}{path}"
        if clean_query: clean_url += f"?{clean_query}"
        return clean_url
    except: return None