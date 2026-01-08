import sqlite3, time, config, re, math, tldextract
from flask import render_template, request, jsonify
from markupsafe import Markup
from app import app
from urllib.parse import urlparse
from datetime import datetime

extract = tldextract.TLDExtract(cache_dir=None)

# -------------------------
# Config
# -------------------------
PER_PAGE = 20
CANDIDATE_POOL_SIZE = 500
MAX_QUERY_TERMS = 7
MAX_QUERY_LENGTH = 150

# --- Rate Limiter ---
RATE_LIMIT = {}
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 30


def check_rate_limit(ip):
    now = time.time()
    if len(RATE_LIMIT) > 10000:
        RATE_LIMIT.clear()
        
    if ip not in RATE_LIMIT:
        RATE_LIMIT[ip] = (now, 1)
        return True
    
    start, count = RATE_LIMIT[ip]
    if now - start > RATE_LIMIT_WINDOW:
        # Reset window
        RATE_LIMIT[ip] = (now, 1)
        return True
    
    if count >= RATE_LIMIT_MAX:
        return False
    
    RATE_LIMIT[ip] = (start, count + 1)
    return True

STOPWORDS = {
    "the","a","an","of","to","and","in","on","for","with","at","by","from",
    "how","what","why","when","where","is","are","be","this","that","it","its"
}

SYNONYMS = {
    "install": ["setup", "configure"],
    "setup": ["install", "configure"],
    "error": ["issue", "problem"],
    "bug": ["issue", "defect"],
    "security": ["infosec", "cybersecurity"],
    "auth": ["authentication", "login"],
    "login": ["authentication", "auth"],
    "network": ["net", "networking"],
    "linux": ["gnu", "unix"],
    "windows": ["win"],
}


# -------------------------
# DB helper
# -------------------------
def get_db_connection():
    conn = sqlite3.connect(config.DB_SEARCH, timeout=10)
    conn.execute(f"ATTACH DATABASE '{config.DB_CRAWL}' AS crawl_db")
    conn.row_factory = sqlite3.Row
    return conn


# -------------------------
# Query utilities
# -------------------------
def normalize_tokens(raw):
    raw = raw.lower()
    raw = re.sub(r"[^a-z0-9\s]", " ", raw)
    tokens = raw.split()
    tokens = [t for t in tokens if t not in STOPWORDS and len(t) > 1]
    tokens = list(dict.fromkeys(tokens))
    return tokens[:MAX_QUERY_TERMS]


def normalize_for_brand(raw):
    return re.sub(r"[^a-z0-9]", "", raw.lower())


def extract_site_directives(raw):
    raw_low = raw.lower()
    m = re.search(r"site:\s*([a-z0-9.\-]+)", raw_low)
    if m:
        return m.group(1)
    tokens = re.findall(r"[a-z0-9.]+", raw_low)
    for t in tokens:
        if "." in t and len(t) > 4:
            return t
    return None


def expand_terms(base_terms):
    expanded = list(base_terms)
    for t in base_terms:
        for s in SYNONYMS.get(t, []):
            expanded.append(s)
    return list(dict.fromkeys(expanded))


def build_fts_query(base_terms, mode="AND"):
    if not base_terms:
        return ""
    
    groups = []
    for t in base_terms:
        variants = [f'"{t}"', f'"{t}"*']
        
        for s in SYNONYMS.get(t, []):
            variants.append(f'"{s}"')
            
        groups.append("(" + " OR ".join(variants) + ")")
    
    join_operator = " AND " if mode == "AND" else " OR "
    return join_operator.join(groups)


def term_weights(original_terms, expanded_terms):
    weights = {}
    original_set = set(original_terms)
    for t in expanded_terms:
        base = 1.0 + min(1.5, len(t) / 6.0)
        if t not in original_set:
            base *= 0.5
        weights[t] = base
    return weights


# -------------------------
# Text analysis & proximity
# -------------------------
def tokenize(text):
    return re.findall(r"[a-z0-9]+", text.lower()) if text else []


def multi_term_proximity(text, terms):
    tokens = tokenize(text)
    if len(tokens) < 2 or len(terms) < 2:
        return 0.0
    
    positions = []
    for i, tok in enumerate(tokens):
        if any(t in tok for t in terms):
            positions.append(i)
            
    if len(positions) < 2:
        return 0.0
        
    span = max(positions) - min(positions)
    return max(0.0, 30.0 / (1.0 + span))


def saturation(val, cap):
    return min(val / cap, 1.0)


# -------------------------
# Scoring components
# -------------------------
def authority_score(rank):
    if not rank: return 0.0
    # Clamped at 60.0 to prevent domain dominance
    raw_score = 160.0 / (1.0 + math.log10(float(rank) + 10))
    return min(raw_score, 60.0)


def freshness_score(crawled_at):
    if not crawled_at: return 0.0
    try:
        dt = datetime.strptime(crawled_at, "%Y-%m-%d %H:%M:%S")
        age = (datetime.now() - dt).days
        return 25.0 * math.exp(-age / 200.0)
    except: return 0.0


def tld_bias(url):
    try:
        tld = extract(url).suffix or ""
        if tld in {"gov", "edu", "org"}: return 15.0
        if tld in {"io", "dev", "net"}: return 8.0
    except: pass
    return 0.0


def url_quality(url):
    try:
        p = urlparse(url)
        score = 0.0
        depth = p.path.count("/")
        score -= max(0, depth - 3) * 4.0
        if "?" in url: score -= 12.0
        tokens = tokenize(p.path)
        score += min(10.0, len(tokens) * 2.0)
        if p.path in ("", "/"): score += 12.0
        return score
    except: return 0.0


def field_score(row, terms, weights):
    title = (row.get("title") or "").lower()
    desc = (row.get("description") or "").lower()
    url = row.get("url", "").lower()
    
    score = 0.0
    phrase = " ".join(terms)
    
    if phrase and phrase in title: score += 90.0
    elif phrase and phrase in desc: score += 50.0
        
    title_hits = sum(weights.get(t, 0.0) for t in terms if t in title)
    desc_hits = sum(weights.get(t, 0.0) for t in terms if t in desc)
    url_hits = sum(weights.get(t, 0.0) for t in terms if t in url)
    
    score += saturation(title_hits, 4.0) * 70.0
    score += saturation(desc_hits, 6.0) * 35.0
    score += saturation(url_hits, 4.0) * 30.0
    
    score += multi_term_proximity(title, terms) * 1.6
    score += multi_term_proximity(desc, terms)
    
    return score


def intent_boost(intent, url, nav_slug):
    if intent == "navigational" and nav_slug:
        try:
            if nav_slug in urlparse(url).netloc: return 180.0
        except: pass
    return 0.0


def language_score(row_lang, user_lang):
    if not row_lang: return 0.0
    try:
        rl = row_lang.lower().split("-")[0]
        ul = user_lang.lower().split("-")[0]
        if rl == ul: return 40.0
        if rl and ul and rl[0] == ul[0]: return 8.0
        return -10.0
    except: return 0.0


# -------------------------
# Domain/brand helpers
# -------------------------
def domain_from_url(url):
    try:
        e = extract(url)
        return e.domain or ""
    except: return ""


def matches_brand_phrase(raw_normalized_no_space, row_domain_base):
    if not row_domain_base: return False
    return raw_normalized_no_space == row_domain_base


# -------------------------
# Final score aggregation
# -------------------------
def calculate_score(conn, row, terms, weights, intent, nav_slug, domain_counts,
                    site_directive=None, raw_brand_normalized="",
                    user_lang="en"):
    
    score = 100.0
    score += authority_score(row.get("domain_rank"))
    score += freshness_score(row.get("crawled_at"))
    score += tld_bias(row.get("url"))
    score += url_quality(row.get("url"))
    score += language_score(row.get("language"), user_lang)
    score += field_score(row, terms, weights)
    score += intent_boost(intent, row.get("url"), nav_slug)
    
    domain = urlparse(row.get("url")).netloc
    score -= domain_counts.get(domain, 0) * 15.0

    try:
        row_domain_base = domain_from_url(row.get("url"))
        parsed = urlparse(row.get("url"))
        is_root = parsed.path in ("", "/")
        
        if site_directive:
            sd = site_directive.lower().rstrip("/")
            if sd and (sd in domain or sd == row_domain_base):
                if is_root: score += 240.0
                else: score += 80.0
                    
        if raw_brand_normalized:
            if matches_brand_phrase(raw_brand_normalized, row_domain_base):
                if is_root: score += 220.0
                else: score += 40.0
    except: pass

    return score


# -------------------------
# Routes
# -------------------------
@app.route("/")
def home():
    return render_template("index.html")


@app.route("/search")
def search():
    if not check_rate_limit(request.remote_addr):
        return "Rate limit exceeded. Try again later.", 429

    raw_query = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)

    if len(raw_query) > MAX_QUERY_LENGTH:
        raw_query = raw_query[:MAX_QUERY_LENGTH]

    if not raw_query:
        return render_template("index.html")

    start_time = time.time()

    accept = request.headers.get("Accept-Language", "en")
    user_lang = accept.split(",")[0].split(";")[0].strip() or "en"

    site_directive = extract_site_directives(raw_query)
    base_terms = normalize_tokens(raw_query)
    
    if not base_terms:
        base_terms = raw_query.lower().split()
        
    expanded_terms = expand_terms(base_terms)
    weights = term_weights(base_terms, expanded_terms)
    
    intent = "navigational" if len(base_terms) <= 2 else "informational"
    raw_brand_normalized = normalize_for_brand(raw_query)

    conn = get_db_connection()
    c = conn.cursor()

    results = []
    total_estimated = 0
    fallback_triggered = False
    
    try:
        sql_base = """
            SELECT
                search_index.url,
                search_index.title,
                search_index.description,
                snippet(search_index, 3, '<b>', '</b>', '...', 64) AS snippet,
                crawl_db.visited.crawled_at,
                crawl_db.visited.language,
                crawl_db.visited.domain_rank
            FROM search_index
            JOIN crawl_db.visited ON search_index.url = crawl_db.visited.url
            WHERE search_index MATCH ?
            LIMIT ?
        """

        fts_query = build_fts_query(base_terms, mode="AND")
        c.execute(sql_base, (fts_query, CANDIDATE_POOL_SIZE))
        rows = c.fetchall()

        if len(rows) < 5 and len(base_terms) > 1:
            print(" [DEBUG] Low results, triggering OR fallback.")
            fallback_triggered = True
            loose_query = build_fts_query(base_terms, mode="OR")
            c.execute(sql_base, (loose_query, CANDIDATE_POOL_SIZE))
            rows = c.fetchall()

        seen_norm = set()
        pre_scored = []

        for r in rows:
            row_dict = dict(r)
            norm = re.sub(r"^https?://(www\.)?", "", row_dict["url"].strip("/")).rstrip("/")
            
            if norm in seen_norm: continue
            seen_norm.add(norm)

            score = calculate_score(
                conn, row_dict, expanded_terms, weights, intent, nav_slug=None, 
                domain_counts={}, # Key change
                site_directive=site_directive, 
                raw_brand_normalized=raw_brand_normalized,
                user_lang=user_lang
            )
            
            if fallback_triggered: score *= 0.8
            pre_scored.append((score, row_dict))

        pre_scored.sort(key=lambda x: x[0], reverse=True)

        final_scored = []
        domain_counts = {}
        
        for score, row_dict in pre_scored:
            domain = urlparse(row_dict["url"]).netloc
            count = domain_counts.get(domain, 0)
            
            penalty = count * 15.0
            final_score = score - penalty
            
            domain_counts[domain] = count + 1
            final_scored.append((final_score, row_dict))
            
        final_scored.sort(key=lambda x: x[0], reverse=True)

        total_estimated = len(final_scored)
        start_idx = (page - 1) * PER_PAGE
        end_idx = start_idx + PER_PAGE

        for score, r in final_scored[start_idx:end_idx]:
            clean_snip = r["snippet"] or ""
            if not clean_snip and r.get("description"):
                clean_snip = r["description"][:200] + "..."
            
            title = r["title"] or r["url"]
            domain = urlparse(r["url"]).netloc
            rank = r.get("domain_rank") or 10000000

            results.append({
                "title": Markup(title),
                "url": r["url"],
                "domain": domain,
                "snippet": Markup(clean_snip),
                "lang": r.get("language"),
                "rank": rank,
                "verified": (rank < 10000)
            })

    except Exception as e:
        print(f"Search error: {e}")
    finally:
        conn.close()

    elapsed = round(time.time() - start_time, 4)
    total_pages = (total_estimated // PER_PAGE) + (1 if total_estimated % PER_PAGE else 0)

    return render_template(
        "index.html",
        query=raw_query,
        results=results,
        count=total_estimated,
        time=elapsed,
        page=page,
        total_pages=total_pages
    )


@app.route("/suggest")
def suggest():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT title FROM crawl_db.visited WHERE title LIKE ? LIMIT 5", (f"%{q}%",))
        rows = c.fetchall()
        return jsonify([r[0] for r in rows if r[0]])
    except:
        return jsonify([])
    finally:
        if conn:
            conn.close()