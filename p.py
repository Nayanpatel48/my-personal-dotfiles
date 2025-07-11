#!/usr/bin/env python3
import os
import time
import json
import feedparser
import urllib.parse
import re
from urllib.parse import urlparse
from pathlib import Path
import urllib.request

# —————————————————————————————————
# CONFIGURATION
# —————————————————————————————————

STORAGE_PATH = "ai_updates.json"
FETCH_LIMIT = 60

BLOCKED_DOMAINS = {
    "example-fake-news.com",
    "india-tabloid.co.in",
    "spurious-source.net",
}

TRUSTED_DOMAINS = {
    "openai.com": 10,
    "arxiv.org": 9,
    "nature.com": 9,
    "sciencedaily.com": 8,
    "venturebeat.com": 7,
    "techcrunch.com": 7,
    "forbes.com": 6,
}

# —————————————————————————————————
# HELPERS
# —————————————————————————————————

def load_archive():
    if Path(STORAGE_PATH).is_file():
        return json.loads(Path(STORAGE_PATH).read_text())
    return {}

def save_archive(data):
    Path(STORAGE_PATH).write_text(json.dumps(data, indent=2))

def domain_of(url: str) -> str:
    return urlparse(url).netloc.lower()

def sentence_split(text: str):
    parts = re.split(r'(?<=[.!?]) +', text.strip())
    return [p.replace('\n', ' ').strip() for p in parts if p.strip()]

def extract_summary(text: str, max_sentences: int = 5):
    sents = sentence_split(text)
    return " ".join(sents[:max_sentences]) if sents else text

def fetch_page_metadata(url):
    """Scrapes published time and section/category metadata from page."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            html_text = r.read().decode(errors="ignore")
    except:
        return None, None

    published_time = None
    section = None

    # Published time patterns
    patterns = [
        r'<meta[^>]+property="article:published_time"[^>]+content="([^"]+)"',
        r'<meta[^>]+name="date"[^>]+content="([^"]+)"',
        r'<meta[^>]+property="og:published_time"[^>]+content="([^"]+)"',
    ]
    for pat in patterns:
        match = re.search(pat, html_text, re.I)
        if match:
            published_time = match.group(1)
            break

    # Section/category patterns
    sec_patterns = [
        r'<meta[^>]+property="article:section"[^>]+content="([^"]+)"',
        r'<meta[^>]+name="section"[^>]+content="([^"]+)"',
    ]
    for pat in sec_patterns:
        match = re.search(pat, html_text, re.I)
        if match:
            section = match.group(1)
            break

    return published_time, section

def score_item(title, summary, query, domain, published_rss, published_page):
    # keyword match score
    all_words = re.findall(r"\w+", (title + " " + summary).lower())
    q_words = re.findall(r"\w+", query.lower())
    if not all_words or not q_words:
        match_score = 0.0
    else:
        count = sum(1 for w in all_words if w in q_words)
        match_score = (count / len(all_words)) * 60  # max 60%

    # domain trust score
    domain_score = TRUSTED_DOMAINS.get(domain, 5) / 10 * 20  # max 20%

    # recency score
    def parse_time(t):
        try:
            return time.mktime(time.strptime(t, "%Y-%m-%dT%H:%M:%SZ"))
        except:
            try:
                return time.mktime(time.strptime(t[:19], "%Y-%m-%dT%H:%M:%S"))
            except:
                return None

    pub_time = parse_time(published_page) or parse_time(published_rss)
    if pub_time:
        hours_ago = (time.time() - pub_time) / 3600
        recency_score = max(0, (72 - hours_ago) / 72 * 20)  # max 20%
    else:
        recency_score = 10

    total_score = match_score + domain_score + recency_score
    return round(min(total_score, 100.0), 1)

def build_rss_urls(query: str):
    q = urllib.parse.quote_plus(query)
    return [
        f"https://news.google.com/rss/search?q={q}",
        "http://export.arxiv.org/rss/cs.AI",
        "https://www.sciencedaily.com/rss/computers_math/artificial_intelligence.xml",
        "https://www.nature.com/subjects/artificial-intelligence/rss"
    ]

# —————————————————————————————————
# CORE LOGIC
# —————————————————————————————————

def fetch_and_rank(query: str):
    seen = load_archive()
    items = []

    for feed_url in build_rss_urls(query):
        d = feedparser.parse(feed_url)
        for entry in d.entries[:FETCH_LIMIT]:
            link = entry.link
            domain = domain_of(link)
            if domain in BLOCKED_DOMAINS:
                continue

            uid = entry.get("id", link)
            title = entry.title.strip()
            published_rss = entry.get("published", time.strftime("%Y-%m-%dT%H:%M:%SZ"))
            raw_desc = getattr(entry, "summary", "") or title
            clean_summary = re.sub(r"<.*?>", "", raw_desc).strip()
            summary = extract_summary(clean_summary, max_sentences=5)

            published_page, section = fetch_page_metadata(link)

            score = score_item(title, summary, query, domain, published_rss, published_page)

            items.append({
                "uid": uid,
                "title": title,
                "link": link,
                "published_rss": published_rss,
                "published_page": published_page or "unknown",
                "section": section or "unknown",
                "summary": summary,
                "source": domain,
                "score": score
            })

            if uid not in seen:
                seen[uid] = {"title": title, "link": link, "published": published_rss}

    save_archive(seen)
    items.sort(key=lambda x: (x["score"], x["published_rss"]), reverse=True)
    return items[:FETCH_LIMIT]

# —————————————————————————————————
# ENTRYPOINT
# —————————————————————————————————

def main():
    query = input("Enter search term or phrase: ").strip()
    if not query:
        print("No query entered. Exiting.")
        return

    print(f"\n🔍 Fetching & ranking top AI updates for “{query}” (full scrape)…\n")
    results = fetch_and_rank(query)

    if not results:
        print("No results found.")
        return

    for i, it in enumerate(results, 1):
        print(f"{i}. [{it['score']}%] {it['title']}")
        print(f"    ↪ {it['link']} (Source: {it['source']})")
        print(f"    Section: {it['section']}")
        print(f"    Published (RSS): {it['published_rss']}")
        print(f"    Published (Page): {it['published_page']}")
        print(f"    Summary: {it['summary']}\n")

if __name__ == "__main__":
    main()
