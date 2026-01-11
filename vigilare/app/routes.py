import sqlite3
import time
import config
import re
import math
import tldextract
import os
import requests
import difflib
from flask import render_template, request, jsonify
from markupsafe import Markup
from app import app
from urllib.parse import urlparse
from datetime import datetime
from flask import send_from_directory

extract = tldextract.TLDExtract(cache_dir=None, suffix_list_urls=[])

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
def get_spelling_suggestion(conn, raw_query):
    # 1. Tokenize
    terms = normalise_tokens(raw_query)
    if not terms:
        return None
    
    corrections = {}
    found_typo = False
    
    c = conn.cursor()
    
    for term in terms:
        c.execute("SELECT count(*) FROM search_vocab WHERE term = ?", (term,))
        if c.fetchone()[0] > 0:
            corrections[term] = term
            continue
            
        found_typo = True
        
        c.execute("SELECT term FROM search_vocab WHERE term LIKE ? LIMIT 50", (f"{term[0]}%",))
        candidates = [r[0] for r in c.fetchall()]
        
        matches = difflib.get_close_matches(term, candidates, n=1, cutoff=0.75)
        
        if matches:
            corrections[term] = matches[0]
        else:
            corrections[term] = term
            
    if found_typo:
        new_query = raw_query
        for wrong, right in corrections.items():
            if wrong != right:
                new_query = re.sub(r'\b' + re.escape(wrong) + r'\b', right, new_query)
        
        if new_query != raw_query:
            return new_query
            
    return None


def normalise_tokens(raw):
    raw = raw.lower()
    raw = re.sub(r"[^a-z0-9\s]", " ", raw)
    tokens = raw.split()
    tokens = [t for t in tokens if t not in STOPWORDS and len(t) > 1]
    tokens = list(dict.fromkeys(tokens))
    return tokens[:MAX_QUERY_TERMS]


def normalise_for_brand(raw):
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


# --- Contextual Snippet Generator ---
def generate_contextual_snippet(content, query_terms):
    if not content or not query_terms:
        return ""
    
    text = " ".join(content.split())
    
    best_window = ""
    max_score = 0
    lower_text = text.lower()
    
    positions = []
    for term in query_terms:
        start = 0
        while True:
            idx = lower_text.find(term, start)
            if idx == -1: 
                break
            positions.append(idx)
            start = idx + 1
    
    if not positions:
        return text[:250] + "..."

    positions.sort()
    
    for pos in positions:
        start = max(0, pos - 60)
        end = min(len(text), pos + 240)
        window = text[start:end]
        
        score = 0
        window_lower = window.lower()
        for term in query_terms:
            score += window_lower.count(term)
        
        if score > max_score:
            max_score = score
            best_window = window

    if best_window:
        for term in query_terms:
            pattern = re.compile(re.escape(term), re.IGNORECASE)
            best_window = pattern.sub(f"<b>{term}</b>", best_window)
            
        return "..." + best_window + "..."
        
    return text[:250] + "..."


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
def tokenise(text):
    return re.findall(r"[a-z0-9]+", text.lower()) if text else []


def multi_term_proximity(text, terms):
    tokens = tokenise(text)
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
    if not rank:
        return 0.0
    raw_score = 160.0 / (1.0 + math.log10(float(rank) + 10))
    return min(raw_score, 60.0)


def pagerank_score(pr_val):
    if not pr_val or pr_val <= 0:
        return 0.0
    return math.log(pr_val * 10 + 1) * 15.0


def freshness_score(crawled_at):
    if not crawled_at:
        return 0.0
    try:
        dt = datetime.strptime(crawled_at, "%Y-%m-%d %H:%M:%S")
        age = (datetime.now() - dt).days
        return 25.0 * math.exp(-age / 200.0)
    except Exception:
        return 0.0


def tld_bias(suffix):
    if not suffix:
        return 0.0
    try:
        if suffix in {"gov", "edu", "org"}:
            return 15.0
        if suffix in {"io", "dev", "net"}:
            return 8.0
    except Exception:
        pass
    return 0.0


def url_quality(parsed_obj, raw_url):
    try:
        score = 0.0
        depth = parsed_obj.path.count("/")
        score -= max(0, depth - 3) * 4.0
        
        if "?" in raw_url:
            score -= 12.0
            
        tokens = tokenise(parsed_obj.path)
        score += min(10.0, len(tokens) * 2.0)
        
        if parsed_obj.path in ("", "/"):
            score += 12.0
            
        return score
    except Exception:
        return 0.0


def field_score(row, terms, weights):
    title = (row.get("title") or "").lower()
    desc = (row.get("description") or "").lower()
    url = row.get("url", "").lower()
    
    score = 0.0
    phrase = " ".join(terms)
    
    if phrase and phrase in title:
        score += 90.0
    elif phrase and phrase in desc:
        score += 50.0
        
    title_hits = sum(weights.get(t, 0.0) for t in terms if t in title)
    desc_hits = sum(weights.get(t, 0.0) for t in terms if t in desc)
    url_hits = sum(weights.get(t, 0.0) for t in terms if t in url)
    
    score += saturation(title_hits, 4.0) * 70.0
    score += saturation(desc_hits, 6.0) * 35.0
    score += saturation(url_hits, 4.0) * 30.0
    
    score += multi_term_proximity(title, terms) * 1.6
    score += multi_term_proximity(desc, terms)
    
    return score


def intent_boost(intent, netloc, nav_slug):
    if intent == "navigational" and nav_slug:
        try:
            if nav_slug in netloc:
                return 180.0
        except Exception:
            pass
    return 0.0


def language_score(row_lang, user_lang):
    if not row_lang:
        return 0.0
    try:
        rl = row_lang.lower().split("-")[0]
        ul = user_lang.lower().split("-")[0]
        if rl == ul:
            return 40.0
        if rl and ul and rl[0] == ul[0]:
            return 8.0
        return -10.0
    except Exception:
        return 0.0


# -------------------------
# Domain/brand helpers
# -------------------------
def matches_brand_phrase(raw_normalised_no_space, row_domain_base):
    if not row_domain_base:
        return False
    return raw_normalised_no_space == row_domain_base


# -------------------------
# Final score aggregation
# -------------------------
def calculate_score(conn, row, terms, weights, intent, nav_slug, domain_counts,
                    site_directive=None, raw_brand_normalised="",
                    user_lang="en"):
    
    row_url = row.get("url")
    try:
        parsed = urlparse(row_url)
        extracted = extract(row_url)
        
        domain = parsed.netloc
        row_domain_base = extracted.domain
        suffix = extracted.suffix
    except Exception:
        return 0.0

    score = 100.0
    
    try:
        raw_bm25 = float(row.get("bm25") or 0)
        score += max(0, (20.0 - raw_bm25) * 2.0)
    except Exception:
        pass
    
    score += authority_score(row.get("domain_rank"))
    score += pagerank_score(row.get("page_rank"))
    score += freshness_score(row.get("crawled_at"))
    
    score += tld_bias(suffix)
    score += url_quality(parsed, row_url)
    
    score += language_score(row.get("language"), user_lang)
    score += field_score(row, terms, weights)
    score += intent_boost(intent, domain, nav_slug)
    
    domain = urlparse(row.get("url")).netloc
    score -= domain_counts.get(domain, 0) * 15.0

    try:
        is_root = parsed.path in ("", "/")
        
        if site_directive:
            sd = site_directive.lower().rstrip("/")
            if sd and (sd in domain or sd == row_domain_base):
                if is_root:
                    score += 240.0
                else:
                    score += 80.0
                    
        if raw_brand_normalised:
            if matches_brand_phrase(raw_brand_normalised, row_domain_base):
                if is_root:
                    score += 220.0
                else:
                    score += 40.0
    except Exception:
        pass

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
    base_terms = normalise_tokens(raw_query)
    
    if not base_terms:
        base_terms = raw_query.lower().split()
        
    expanded_terms = expand_terms(base_terms)
    weights = term_weights(base_terms, expanded_terms)
    
    intent = "navigational" if len(base_terms) <= 2 else "informational"
    raw_brand_normalised = normalise_for_brand(raw_query)

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
                substr(search_index.content, 1, 5000) as content_sample,
                crawl_db.visited.crawled_at,
                crawl_db.visited.language,
                crawl_db.visited.domain_rank,
                crawl_db.visited.page_rank,     
                bm25(search_index) as bm25      
            FROM search_index
            JOIN crawl_db.visited ON search_index.url = crawl_db.visited.url
            WHERE search_index MATCH ?
            LIMIT ?
        """

        fts_query = build_fts_query(base_terms, mode="AND")
        c.execute(sql_base, (fts_query, CANDIDATE_POOL_SIZE))
        rows = c.fetchall()
        
        suggestion = None
        if len(rows) < 5:
            suggestion = get_spelling_suggestion(conn, raw_query)

        if len(rows) < 5 and len(base_terms) > 1:
            fallback_triggered = True
            loose_query = build_fts_query(base_terms, mode="OR")
            c.execute(sql_base, (loose_query, CANDIDATE_POOL_SIZE))
            rows = c.fetchall()

        seen_norm = set()
        pre_scored = []

        for r in rows:
            row_dict = dict(r)
            norm = re.sub(r"^https?://(www\.)?", "", row_dict["url"].strip("/")).rstrip("/")
            
            if norm in seen_norm:
                continue
            seen_norm.add(norm)

            score = calculate_score(
                conn, row_dict, expanded_terms, weights, intent, nav_slug=None, 
                domain_counts={}, 
                site_directive=site_directive, 
                raw_brand_normalised=raw_brand_normalised,
                user_lang=user_lang
            )
            
            if fallback_triggered:
                score *= 0.8
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
            clean_snip = generate_contextual_snippet(r["content_sample"], base_terms)
            
            if (not clean_snip or len(clean_snip) < 20) and r.get("description"):
                clean_snip = r["description"][:250] + "..."
            
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
        total_pages=total_pages,
        suggestion=suggestion
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
    except Exception:
        return jsonify([])
    finally:
        if conn:
            conn.close()


@app.route("/icon/<domain>")
def icon_proxy(domain):
    domain = re.sub(r'[^a-zA-Z0-9.-]', '', domain)[:50]
    filename = f"{domain}.ico"
    filepath = os.path.join(config.ICONS_DIR, filename)

    if os.path.exists(filepath):
        return send_from_directory(config.ICONS_DIR, filename)

    try:
        remote_url = f"https://www.google.com/s2/favicons?domain={domain}&sz=32"
        r = requests.get(remote_url, timeout=2)
        
        if r.status_code == 200:
            with open(filepath, 'wb') as f:
                f.write(r.content)
            return send_from_directory(config.ICONS_DIR, filename)
    except Exception:
        pass

    return "", 404