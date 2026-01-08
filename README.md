# vigilare

```text
       _       _ _                
__   _(_) __ _(_) | __ _ _ __ ___ 
\ \ / / |/ _` | | |/ _` | '__/ _ \
 \ V /| | (_| | | | (_| | | |  __/
  \_/ |_|\__, |_|_|\__,_|_|  \___|
         |___/                    
```

**Vigilare is an independent search engine dedicated to the free exchange of information. Results are delivered without restriction, curation, or censorship.**
         
**License: GNU AGPLv3**

**Status: Alpha / Active Development**

---

## Project Overview
Vigilare (Latin: to remain watchful, vigilant) is an open source, privacy-first search engine designed to operate outside of commercial influence and state-level filtration. The project addresses the increasing prevalence of algorithmic curation and surveillance by providing a decentralised, auditable alternative for information retrieval.

---

## Core Principles
### 1. Algorithmic Transparency
The source code governing result ranking is fully open-source. This ensures the indexing process remains auditable, preventing the implementation of filters or hidden biases commonly found in proprietary engines.

### 2. Zero-Retention Architecture
Privacy is implemented at the architectural level rather than as a user setting. The system architecture precludes the creation of user profiles, thus providing:
* No IP Address Logging
* No Persistent Cookies
* No Search History Logging or Retention

### 3. Unrestricted Access
The indexing crawler operates without geographic bias. Content is indexed based on availability rather than regional regulatory compliance, ensuring the preservation of information often targeted for censorship or removal.

---

## Technical Architecture

The system utilises a concurrent, high-throughput pipeline designed to run efficiently on consumer hardware without the overhead of enterprise clusters.

### 1. Concurrent Crawler Engine (`/crawler`)
The core spider (`bot.py`) is a multi-threaded, polite web crawler.
* **Threaded Dispatch:** Uses a pool of worker threads (Fetchers & Parsers) to handle network I/O and HTML parsing in parallel.
* **Domain Governance:** Enforces strict per-domain rate limiting and politeness policies to prevent accidentally DoS-ing target sites.
* **Bloom Filters:** Uses probabilistic data structures for O(1) duplicate detection, handling millions of URLs with minimal resource usage.

### 2. Split-Storage Architecture (`/data`)
To overcome SQLite's write-locking limitations, the system separates concerns into three distinct databases:
1.  **Crawl DB:** Stores the Frontier (queue) and metadata. Optimised for high-speed writes.
2.  **Storage DB:** Stores compressed HTML content. Optimised for bulk storage.
3.  **Search DB:** Stores the Inverted Index (FTS5). Optimised for complex read queries.
* **WAL Mode:** All databases use Write-Ahead Logging to allow readers (Search) and writers (Crawler) to operate simultaneously.

### 3. Incremental Indexer (`indexer.py`)
A standalone service that bridges the gap between raw storage and the search index.
* **Batch Processing:** Reads raw HTML from Storage, strips clutter, and updates the Search DB in transactions.
* **Language Detection:** Analyses content during indexing to support language-specific search filtering.

### 4. Search Interface (`/app`)
* **Ranking Algorithm:** Uses a custom hybrid scoring system combining BM25 (text relevance), Domain Authority, and Content Freshness.
* **Two-Pass Ranking:** Implements a strict-to-loose fallback strategy and a domain diversity penalty to prevent result clutter.

---

## Directory Structure

```text
vigilare/
├── app/                  # Web Interface Logic
│   ├── templates/        # HTML Frontend
│   └── routes.py         # Search & Ranking Logic
├── crawler/              # Spider Logic
│   ├── bot.py            # Core Crawling Loop (Fetcher/Parser/Writer)
│   └── utils.py          # Bloom Filters & URL Canonicalisation
├── data/                 # Persistent Storage (Ignored by Git)
│   ├── vigilare_crawl.db   # Frontier & Metadata
│   ├── vigilare_storage.db # Compressed HTML Content
│   ├── vigilare_search.db  # FTS5 Search Index
│   └── vigilare.log        # Debug Logs
├── config.py             # Global Configuration & Tuning
├── flush_wal.py          # Manual WAL Clean for Debugging Purposes
├── indexer.py            # Service: Processes raw HTML into Search Index
├── indexer_state.txt     # Persistent Storage of Indexer Progress
├── init_db.py            # Setup: Creates Schema & Injects Seeds
├── monitor.py            # Real-time System Health Dashboard
├── run_crawler.py        # Entry Point: Launches the Spider threads
└── run_web.py            # Entry Point: Starts the Flask Web Server
```

---

## License
### Licensed under the GNU Affero General Public License v3.0 (AGPLv3).
This software is provided to enforce algorithmic transparency. Any network service deploying this code must provide the full source code to all users interacting with the service. This clause ensures that the search engine remains a public good and cannot be privatised or modified in secrecy.
