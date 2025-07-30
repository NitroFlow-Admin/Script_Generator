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
    try:
        safe = message.encode("utf-8", "ignore").decode("utf-8", "ignore")
    except Exception:
        safe = "[INVALID LOG MESSAGE]"
    logging.info(safe)
    print(f"[LOG] {safe}", flush=True)

def log_blacklisted(domain, reason):
    with open(BLACKLIST_LOG_FILE, "a") as f:
        f.write(f"{domain} blocked: {reason}\n")

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
            reason = "User-agent: * disallowed or root path is blocked"
            log_blacklisted(parsed.netloc, reason)
        return allowed
    except Exception as e:
        log_event(f"[robots.txt] Failed to parse: {e}")
        return True

def score_link(url):
    score = 0
    # Add e-commerce or product-specific patterns
    if re.search(r"(product|store|shop|item)", url, re.I):
        score += 2
    if "product" in url.lower():  # Specific for product URLs
        score += 3
    if re.search(r"(blog|article|news)", url, re.I):  # Blog-specific patterns
        score += 2
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
    
    # If sitemap is not found or failed to fetch, log and proceed to next step
    if not res or res.status_code != 200:
        log_event(f"⚠️ Could not access sitemap.xml at {sitemap_url}")
        return []

    # If we successfully fetch the sitemap, try to find blog links
    soup = BeautifulSoup(res.content, "xml")
    all_locs = [loc.get_text() for loc in soup.find_all("loc")]
    log_event(f"Found {len(all_locs)} URLs in the sitemap")

    blog_links = [link for link in all_locs if score_link(link) > 0 and not link.endswith(".xml")]
    log_event(f"Identified {len(blog_links)} potential blog links.")

    if not blog_links:
        log_event(f"❌ No blog links found in the sitemap.")
    return blog_links


def deduplicate_locations(locations):
    seen = set()
    final = []
    for loc in sorted(locations, key=lambda x: (-len(x), x)):
        norm = loc.lower().strip()
        if not any(norm in s.lower() or s.lower() in norm for s in seen):
            seen.add(loc)
            final.append(loc)
    return final


from urllib.parse import urljoin, urlparse

def extract_social_media_links(base_url: str) -> dict:
    social_links = {}
    patterns = {
        "LinkedIn": r"(https?://(www\.)?linkedin\.com/company/[^\s\"']+)",
        "Twitter": r"(https?://(www\.)?twitter\.com/[^\s\"']+)",
        "Facebook": r"(https?://(www\.)?facebook\.com/[^\s\"']+)",
        "Instagram": r"(https?://(www\.)?instagram\.com/[^\s\"']+)",
        "YouTube": r"(https?://(www\.)?youtube\.com/[^\s\"']+)"
    }

    def domain_matches_social(profile_url, target_url):
        try:
            profile_slug = urlparse(profile_url).path.lower().strip("/").split("/")[-1]
            target_domain = urlparse(target_url).netloc.lower().replace("www.", "")
            domain_base = target_domain.split(".")[0]  # 'salesdrip.com' → 'salesdrip'

            return domain_base in profile_slug or domain_base in profile_url.lower()
        except Exception as e:
            log_event(f"[DOMAIN MATCH FAIL] {e}")
            return True  # fallback to permissive match

    urls_to_check = [base_url]
    for suffix in ["about", "contact", "home"]:
        urls_to_check.append(urljoin(base_url, suffix))

    for page_url in urls_to_check:
        res = safe_get(page_url)
        if res and res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            html = soup.prettify()
            for platform, regex in patterns.items():
                match = re.search(regex, html, re.I)
                if match and platform not in social_links:
                    profile = match.group(1)
                    if domain_matches_social(profile, base_url):
                        social_links[platform] = profile
        else:
            log_event(f"[SOCIAL] Could not fetch {page_url}")

    log_event(f"[SOCIAL MEDIA FOUND] {social_links}")
    return social_links



# --- Update inside extract_locations_from_main_pages() ---

def extract_locations_from_main_pages(base_url):
    import spacy
    nlp = spacy.load("en_core_web_sm")

    pages = [base_url.rstrip("/")]
    for suffix in ["about", "about-us", "contact", "contact-us", "locations"]:
        pages.append(urljoin(base_url, suffix))

    combined_text = ""
    for page_url in pages:
        res = safe_get(page_url)
        if res and res.status_code == 200:
            soup = BeautifulSoup(res.text, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            visible_text = soup.get_text(separator=" ", strip=True)
            log_event(f"[PAGE TEXT] {page_url} --> {visible_text[:500]}...")
            combined_text += visible_text + " "
        else:
            log_event(f"[SKIP] {page_url} not fetched.")

    doc = nlp(combined_text)
    all_locs = [ent.text.strip() for ent in doc.ents if ent.label_ == "GPE" and len(ent.text) <= 40]
    deduped = deduplicate_locations(all_locs)
    log_event(f"[EXTRACTED LOCATIONS] {deduped}")
    return deduped


def run_ethical_scraper(domain, max_articles=5):
    log_event(f"📡 Starting research for: {domain}")
    
    if not is_scraping_allowed(domain):
        return {"error": "Scraping disallowed by robots.txt."}
    
    # Try to get blog/article links from sitemap
    blog_links = find_links_from_sitemap(domain)
    if not blog_links:
        log_event(f"⚠️ No blog links found. Moving on to scrape homepage for location information.")
    
    # Extract location mentions from main pages
    locations = extract_locations_from_main_pages(domain)

    # Extract company facts and products from homepage
    company_facts = extract_company_facts_from_domain(domain)
    if not company_facts:
        log_event(f"❌ No company facts found.")
        company_facts = {
            "company_facts": {"overview": "Not available", "capabilities": [], "certifications": []},
            "products_services": {"product_types": [], "product_count_estimate": "Not available"}
        }

    # Extract social media links from common pages
    social = extract_social_media_links(domain)

    return {
        "articles": blog_links,
        "locations": "; ".join(locations),
        "company_facts": company_facts.get("company_facts", {}),
        "products_services": company_facts.get("products_services", {}),
        "social_media": social
    }



# --- Entry Point ---
if __name__ == "__main__":
    domain = "https://www.salesdrip.com/"  # Example domain
    results = run_ethical_scraper(domain)
    print(json.dumps(results, indent=2))




# --- AI Company Fact Extraction ---
from openai import OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise RuntimeError("❌ OPENAI_API_KEY not set in environment.")
client = OpenAI(api_key=OPENAI_API_KEY)


def extract_company_facts_from_text(raw_text: str) -> dict:
    import ast
    import re

    prompt_template = '''
You are a professional researcher tasked with analyzing the following text scraped from a company's website.

Please extract and summarize **any important facts** that help someone understand the business. Include information such as:
- What the company does
- Where it operates
- Products or services it offers
- Certifications or partnerships
- Anything else interesting or relevant

Respond **only** with a single well-formatted JSON object. Do not include explanation, markdown, or surrounding text.

Example:
{{
  "company_facts": {{
    "overview": "JHD Corp is a herbal extract supplier headquartered in California with global operations.",
    "products_services": ["Herbal Extracts", "Vitamins", "Amino Acids"],
    "locations": ["Ontario, California", "Canada", "China", "India"],
    "contact_info": {{
      "phone": "+1-626-270-1888",
      "email": "info@jhdcorp.com",
      "address": "2077 S Vineyard Ave, Ontario, CA 91761"
    }},
    "certifications": ["FDA compliant", "ISO 9001 certified"],
    "other_details": ["Partners with cooperative factories in multiple countries"]
  }}
}}

Now here is the company text:
\"\"\" {raw_text} \"\"\"
'''

    prompt = prompt_template.format(raw_text=raw_text)

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=1500
        )

        content = response.choices[0].message.content.strip()

        # Remove code block wrappers if present
        content = re.sub(r"^```(?:json)?", "", content.strip(), flags=re.IGNORECASE)
        content = re.sub(r"```$", "", content.strip())
        content = content.strip()

        # Optional debug logging
        # print("[RAW AI OUTPUT]", content[:500])
        # log_event(f"[DEBUG AI RESPONSE] {content[:250]}")

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return ast.literal_eval(content)
    except Exception as e:
        log_event(f"❌ OpenAI fact compilation failed: {e}")
        return {}


def extract_company_facts_from_domain(url: str) -> dict:
    def get_html_from_url(u: str) -> str:
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            res = requests.get(u, headers=headers, timeout=10)
            if res.status_code == 200:
                return res.text
        except Exception as e:
            log_event(f"❌ Failed to get HTML from {u}: {e}")
        return ""

    def extract_visible_text(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)

    relevant_paths = [
        "",  # homepage
        "about", "about-us", "company", "overview", "who-we-are", "our-story", "mission", "vision", "contact-us"
    ]

    domain = url.rstrip("/")
    combined_text = ""
    successful_pages = 0

    for path in relevant_paths:
        full_url = urljoin(domain + "/", path)
        html = get_html_from_url(full_url)
        if html:
            visible = extract_visible_text(html)
            if visible.strip():
                log_event(f"[FACT SCRAPE] ✅ {full_url} ({len(visible)} chars)")
                combined_text += visible + "\n\n"
                successful_pages += 1
            else:
                log_event(f"[FACT SCRAPE] ⚠️ {full_url} had no visible text.")
        else:
            log_event(f"[FACT SCRAPE] ❌ Failed to fetch {full_url}")

    if not combined_text.strip():
        log_event(f"❌ No usable content extracted from any company-related pages.")
        return {}

    return extract_company_facts_from_text(combined_text)


# --- Entry Point ---
if __name__ == "__main__":
    domain = "https://www.salesdrip.com/"  # Example domain
    results = run_ethical_scraper(domain)
    print(json.dumps(results, indent=2))


__all__ = [
    "run_ethical_scraper",
    "extract_company_facts_from_domain"
]
