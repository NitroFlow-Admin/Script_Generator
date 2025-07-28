import os
import re
import json
import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
import urllib.robotparser

# --- Logging Setup ---
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "scraper_activity.log"
BLACKLIST_LOG_FILE = LOG_DIR / "blacklist.log"
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def log_event(message):
    logging.info(message)
    print(f"[LOG] {message}", flush=True)

def log_blacklisted(domain, reason):
    with open(BLACKLIST_LOG_FILE, "a") as f:
        f.write(f"{domain} blocked: {reason}\n")

# --- Utility Functions ---
def safe_get(url, timeout=10, retries=2):
    headers = {"User-Agent": "Mozilla/5.0"}
    for attempt in range(retries):
        try:
            return requests.get(url, headers=headers, timeout=timeout)
        except Exception as e:
            log_event(f"[Attempt {attempt+1}] Failed to fetch {url}: {e}")
    return None

def is_scraping_allowed(url):
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
        allowed = rp.can_fetch("*", url)
        log_event(f"[robots.txt] Can fetch {url}? {allowed}")
        if not allowed:
            reason = fetch_robots_reason(rp)
            log_blacklisted(parsed.netloc, reason)
        return allowed
    except Exception as e:
        log_event(f"[robots.txt] Failed to parse: {e}")
        return True

def fetch_robots_reason(rp):
    try:
        lines = rp.site_maps() or []
        return "User-agent: * disallowed or root path is blocked"
    except Exception:
        return "robots.txt disallow rules not parsed"

def score_link(url):
    score = 0
    if re.search(r"(blog|article|post|story|blueprint|how-to|case-study)", url, re.I):
        score += 2
    if re.search(r"/Blog\d+/", url, re.I):
        score += 3
    if "read-more" in url.lower() or "resources" in url.lower():
        score += 1
    return score

def is_valid_article(soup):
    text = soup.get_text(" ", strip=True)
    paragraphs = soup.find_all("p")
    h1 = soup.find("h1")
    word_count = sum(len(p.get_text(strip=True).split()) for p in paragraphs)
    return h1 and len(paragraphs) >= 2 and word_count > 150 and len(text) > 300

def extract_article_data(soup, url):
    title_tag = soup.find("h1") or soup.title
    title = title_tag.get_text(strip=True) if title_tag else "Untitled"
    paragraphs = soup.find_all("p")
    content = " ".join(p.get_text(strip=True) for p in paragraphs)
    return {
        "title": title,
        "url": url,
        "content": content[:2500]
    }

def find_links_from_sitemap(domain):
    sitemap_url = urljoin(domain, "/sitemap.xml")
    res = safe_get(sitemap_url)
    if not res or res.status_code != 200:
        return []
    soup = BeautifulSoup(res.content, "xml")
    return [loc.get_text() for loc in soup.find_all("loc") if score_link(loc.get_text()) > 0]

def find_links_from_homepage(domain):
    res = safe_get(domain)
    if not res:
        return []
    soup = BeautifulSoup(res.text, "html.parser")
    links = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            href = urljoin(domain, href)
        if urlparse(href).netloc == urlparse(domain).netloc and score_link(href) > 0:
            links.add(href.rstrip("/"))
    return list(links)

def extract_text_article(url):
    res = safe_get(url)
    if not res or res.status_code != 200:
        return None
    soup = BeautifulSoup(res.text, "html.parser")
    if is_valid_article(soup):
        return extract_article_data(soup, url)
    return None

def run_ethical_scraper(domain, max_articles=5):
    log_event(f"📡 Starting research for: {domain}")
    if not is_scraping_allowed(domain):
        log_event(f"❌ Scraping blocked by robots.txt for: {domain}")
        return []

    links = find_links_from_sitemap(domain)
    if not links:
        links = find_links_from_homepage(domain)

    if not links:
        log_event("⚠️ No blog/resource links found.")
        return []

    scored = sorted(links, key=lambda u: -score_link(u))
    top_links = scored[:max_articles]

    articles = []
    for url in top_links:
        article = extract_text_article(url)
        if article:
            articles.append(article)
            log_event(f"✅ Found article: {article['title']} ({url})")
        else:
            log_event(f"⛔ Invalid article: {url}")

    if not articles:
        log_event("❌ No valid blog articles extracted.")
    else:
        log_event(f"✅ Total articles extracted: {len(articles)}")

    return articles

# --- Entry Point ---
if __name__ == "__main__":
    domain = "https://www.salesdrip.com/"
    results = run_ethical_scraper(domain)
    print(json.dumps(results, indent=2))
