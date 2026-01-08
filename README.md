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

**Status: Active Development**

---

## Project Overview
Vigilare (Latin: to remain watchful, vigilant) is an open source, privacy-first search engine designed to operate outside of commercial influence and state-level filtration. The project addresses the increasing prevalence of algorithmic curation and surveillance by providing a decentralised, auditable alternative for information retrieval.

---

## Core Principles
### 1. Algorithmic Transparency
The source code governing result ranking is fully open-source. This ensures the indexing process remains auditable, preventing the implementation of filters or hidden biases commonly found in proprietary engines.

### 2. Zero-Retention Architecture
Privacy is implemented at the architectural level rather than as a user setting. The system architecture precludes the creation of user profiles, thus providing:
    - No IP Address Logging
    - No Persistent Cookies
    - No Search History Logging or Retention

### 3. Unrestricted Access
The indexing crawler operates without geographic bias. Content is indexed based on availability rather than regional regulatory compliance, ensuring the preservation of information often targeted for censorship or removal.

---

## Technical Architecture

The system utilises a modular, micro-service style architecture designed for high throughput on consumer hardware.

### 1. Crawler Engine (`/crawler`)
The core crawler (`bot.py`) is an asynchronous, polite web crawler. 
* **Duplicate Detection:** Implements Bloom Filters to check visited URLs in constant time with minimal memory footprint, avoiding expensive database lookups for every discovered link.
* **State Management:** The crawler state is persisted to disk, allowing operations to pause and resume without data loss.

### 2. Storage (`/data`)
Data persistence allows for separation between the crawling and serving logic.
* **Database:** Uses SQLite with WAL (Write-Ahead Logging) enabled to handle concurrent reads (search queries) and writes (indexing) without locking the database.
* **File Structure:** All persistent state, including the databases, inverted index, Bloom filters, and logs, are isolated in the `data/` directory for easy backup and migration.

### 3. Web Interface (`/app`)
* **Backend:** A lightweight Flask application (`routes.py`) serves the frontend and handles search query processing.
* **Frontend:** Minimalist HTML templates (`index.html`) designed for maximum accessibility and speed.

---

## Directory Structure

```text
vigilare/
├── app/                  # Web Interface Logic
│   ├── templates/        # HTML Frontend
│   └── routes.py         # Search Request Handlers
├── crawler/              # Spider Logic
│   ├── bot.py            # Core Crawling Loop
│   └── utils.py          # Helper Functions
├── data/                 # Persistent Storage (Ignored by Git)
│   ├── bloom_*.bin       # Probabilistic Data Structures
│   ├── vigilare.db       # Main Search Index
│   └── vigilare.log      # Crawler Activity Logging
├── run_crawler.py        # Entry Point: Start the Spider
├── run_web.py            # Entry Point: Start the Search Engine
├── indexer.py            # Standalone Indexing Service
├── config.py             # Global Configuration
└── monitor.py            # System Health Monitoring
```

---

## License
### Licensed under the GNU Affero General Public License v3.0 (AGPLv3).
This software is provided to enforce algorithmic transparency. Any network service deploying this code must provide the full source code to all users interacting with the service. This clause ensures that the search engine remains a public good and cannot be privatised or modified in secrecy.
