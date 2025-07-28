import os
import json
import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from datetime import datetime
from pathlib import Path
import openai
import re
from dotenv import load_dotenv
load_dotenv()

# Set OpenAI key
from openai import OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Log file setup
LOG_PATH = Path("logs/crawl_log.jsonl")
LOG_PATH.parent.mkdir(exist_ok=True)

# Safer requests wrapper
def safe_get(url, timeout=10, **kwargs):
    headers = kwargs.pop("headers", {"User-Agent": "Mozilla/5.0"})
    try:
        return requests.get(url, timeout=timeout, headers=headers, **kwargs)
    except Exception:
        return None

def extract_company_data(html):
    soup = BeautifulSoup(html, "html.parser")
    data = {"facts": {}, "products_services": {}, "locations": None, "recent_news": None, "social_media": None}

    og_data = {}
    for tag in soup.find_all("meta"):
        if tag.get("property", "").startswith("og:") or tag.get("name", "").startswith("twitter:"):
            key = tag.get("property") or tag.get("name")
            og_data[key] = tag.get("content", "")

    if "og:description" in og_data:
        data["facts"]["description"] = og_data["og:description"]
    if "og:title" in og_data:
        data["facts"]["headline"] = og_data["og:title"]

    ld_json_blocks = soup.find_all("script", {"type": "application/ld+json"})
    for block in ld_json_blocks:
        try:
            content = json.loads(block.string.strip())
            if isinstance(content, list):
                for entry in content:
                    if isinstance(entry, dict) and "@type" in entry:
                        content = entry
                        break
            if content.get("@type") == "Organization":
                if "name" in content:
                    data["facts"]["company_name"] = content["name"]
                if "url" in content:
                    data["facts"]["website"] = content["url"]
                if "contactPoint" in content:
                    cp = content["contactPoint"]
                    if isinstance(cp, dict) and "telephone" in cp:
                        data["facts"]["phone"] = cp["telephone"]
            if content.get("@type") == "Product":
                data["products_services"]["description"] = content.get("description", "")
                data["facts"]["rating"] = content.get("aggregateRating", {}).get("ratingValue")
                if "review" in content:
                    reviews = content["review"]
                    if isinstance(reviews, list):
                        quotes = []
                        for r in reviews:
                            author = r.get("author", {}).get("name", "Anonymous")
                            quote = r.get("reviewBody", "")
                            quotes.append(f"{author}: {quote}")
                        data["social_media"] = "\n\n".join(quotes)
        except Exception:
            continue

    hero = soup.find("div", class_="heroContent")
    if hero:
        summary = []
        if hero.find("h1"):
            summary.append(hero.find("h1").get_text(strip=True))
        if hero.find("p"):
            summary.append(hero.find("p").get_text(strip=True))
        data["facts"]["hero_section"] = "\n".join(summary)

    footer = soup.find("footer")
    if footer:
        footer_text = re.sub(r"\s+", " ", footer.get_text(" ", strip=True))
        match = re.search(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)*),\s+(Utah|California|Texas|New York|Florida|Illinois|Georgia|Nevada|Arizona|Colorado|Washington|Oregon|Ohio|Pennsylvania|North Carolina|South Carolina|Tennessee|Virginia|Alabama|Michigan|Missouri|Ontario|Quebec|British Columbia|England|Scotland|Wales|Ireland|London|Berlin|Sydney|Melbourne|Mumbai|Delhi)\b", footer_text)
        if match:
            city, state = match.groups()
            data["locations"] = f"{city}, {state}, United States" if state in ["Utah", "California", "Texas", "New York", "Florida", "Illinois", "Georgia", "Nevada", "Arizona", "Colorado", "Washington", "Oregon", "Ohio", "Pennsylvania", "North Carolina", "South Carolina", "Tennessee", "Virginia", "Alabama", "Michigan", "Missouri"] else f"{city}, {state}"

    integrations = soup.find_all("div", class_="intItem")
    integration_texts = [i.find("p").get_text(strip=True) for i in integrations if i.find("p")]
    if integration_texts:
        data["products_services"]["integrations"] = integration_texts


    if not data["facts"].get("description"):
        data["facts"]["description"] = soup.title.string if soup.title else ""

    quotes = [b.get_text(strip=True) for b in soup.find_all("blockquote") if b.get_text(strip=True) and len(b.get_text(strip=True).split()) > 5]
    if quotes:
        data["social_media"] = "\n\n".join(quotes)

    return data

def scrape_structured_data(url):
    res = safe_get(url)
    if not res:
        return {"json_ld": [], "meta_tags": {}}
    soup = BeautifulSoup(res.text, 'html.parser')
    structured_data = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            structured_data.append(json.loads(tag.string))
        except:
            pass
    metas = soup.find_all('meta')
    og_data = {tag.get('property') or tag.get('name'): tag.get('content') for tag in metas if (tag.get('property', '').startswith('og:') or tag.get('name', '').startswith('twitter:')) and tag.get('content')}
    return {"json_ld": structured_data, "meta_tags": og_data}

def is_scraping_allowed(url):
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        res = safe_get(robots_url, timeout=5)
        if not res or res.status_code != 200:
            return True
        lines = res.text.lower().splitlines()
        disallowed, current_user_agent = [], None
        for line in lines:
            if line.startswith("user-agent:"):
                current_user_agent = line.split(":")[1].strip()
            elif current_user_agent == "*" and line.startswith("disallow:"):
                disallowed.append(line.split(":")[1].strip())
        for rule in disallowed:
            if urlparse(url).path.startswith(rule):
                return False
        return True
    except:
        return True

def extract_tos_text(home_url):
    try:
        res = safe_get(home_url, timeout=8)
        if not res: return "", None
        soup = BeautifulSoup(res.text, "html.parser")
        tos_links = [a['href'] for a in soup.find_all("a", href=True) if 'terms' in a['href'].lower()]
        for link in tos_links:
            full_url = urljoin(home_url, link)
            tos_res = safe_get(full_url, timeout=8)
            if tos_res and tos_res.status_code == 200:
                return tos_res.text[:6000], full_url
    except:
        return "", None
    return "", None

def is_scraping_banned_in_tos(tos_text):
    if not tos_text:
        return False
    try:
        prompt = ("Does this Terms of Service prohibit scraping or automated access?\nReply 'YES' or 'NO'.\n\n" + tos_text)
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        return "yes" in response.choices[0].message.content.strip().lower()
    except:
        return False

from requests_html import HTMLSession

def scrape_site(url):
    try:
        session = HTMLSession()
        res = session.get(url, timeout=10)

        try:
            res.html.render(timeout=8, sleep=1)
        except Exception as e:
            return f"JS render failed: {str(e)}"

        text_blocks = []
        for tag in res.html.find("p, li, h1, h2, h3, .productItem, .card, .block, .section"):
            text = tag.text.strip()
            if text and len(text.split()) > 4:
                text_blocks.append(text)

        return "\n".join(text_blocks[:80])
    except Exception as e:
        return f"Scrape failed: {str(e)}"

def summarize_blocks(name, site_text, news_text, structured):
    try:
        og = structured.get("meta_tags", {})
        json_ld = structured.get("json_ld", [])

        meta_summary = "\n".join(f"{k}: {v}" for k, v in og.items())
        json_ld_summary = json.dumps(json_ld, indent=2)[:2000]

        prompt = (
            f"You are a research assistant summarizing data about a company called {name}.\n\n"
            f"WEBSITE TEXT CONTENT:\n{site_text[:3000]}\n\n"
            f"NEWS:\n{news_text[:1000]}\n\n"
            f"STRUCTURED METADATA:\n\n---META TAGS---\n{meta_summary}\n\n---JSON-LD---\n{json_ld_summary}\n\n"
            "Please extract the following as clearly and informatively as possible:\n"
            "- Products & Services: What does this company actually sell or manufacture? List their main product types, categories, or use cases.\n"
            "- Recent News: Any relevant events, announcements, press releases, partnerships, etc.\n"
            "- Locations: Where are they based? Offices, warehouses, manufacturing sites, etc.\n"
            "- Company Facts: Certifications, years in business, claims of scale or reputation, etc.\n"
            "- Social Media Mentions: Testimonials, quotes, or user-generated insights from the web.\n\n"
            "Return a valid JSON response with keys: products_services, recent_news, locations, facts, social_media."
        )

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            timeout=20
        )

        content = response.choices[0].message.content.strip()

        with open("gpt_output_debug.txt", "w") as f:
            f.write(content)

        return json.loads(content)

    except Exception as e:
        print("❌ GPT PARSE FAILED:", str(e))
        return {
            "recent_news": "",
            "locations": "",
            "facts": "",
            "products_services": "",
            "social_media": ""
        }
    
def find_internal_news_links(home_url):
    res = safe_get(home_url, timeout=8)
    if not res:
        return []

    soup = BeautifulSoup(res.text, "html.parser")
    internal_links = []

    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if any(x in href for x in ["/news", "/blog", "/insights", "/press", "/media", "/updates", "/stories", "/resources"]):
            full_url = urljoin(home_url, href)
            if urlparse(full_url).netloc == urlparse(home_url).netloc:
                internal_links.append(full_url)

    # Deduplicate and return top 3
    return list(dict.fromkeys(internal_links))[:3]

    
def scrape_company_news_from_site(home_url):
    try:
        news_urls = find_internal_news_links(home_url)
        if not news_urls:
            return "No recent news pages found."

        all_text = []

        for url in news_urls:
            html = safe_get(url)
            if not html:
                continue

            soup = BeautifulSoup(html.text, "html.parser")

            # Try headlines first
            headlines = [h.get_text(strip=True) for h in soup.find_all(["h1", "h2", "h3"]) if len(h.get_text(strip=True).split()) > 3]
            paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True).split()) > 10]

            # Combine top few items
            combined = headlines[:3] + paragraphs[:3]
            if combined:
                all_text.append(f"--- {url} ---\n" + "\n".join(combined))

        return "\n\n".join(all_text[:3]) if all_text else "No news text extracted."
    except Exception as e:
        return f"Error scraping news: {str(e)}"




def run_auto_research(company_name, company_url):
    robots_ok = is_scraping_allowed(company_url)
    tos_text, tos_url = extract_tos_text(company_url)
    tos_ok = not is_scraping_banned_in_tos(tos_text)

    if not robots_ok:
        raise Exception("Blocked by robots.txt")
    if not tos_ok:
        raise Exception("Terms of Service prohibit scraping.")

    html = safe_get(company_url)
    fallback = extract_company_data(html.text if html else "")

    site_content = scrape_site(company_url)

    # If JS scrape fails or gives nothing, fallback to basic scraping
    if not site_content.strip():
        html = safe_get(company_url)
        if html:
            soup = BeautifulSoup(html.text, "html.parser")
            text_blocks = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True).split()) > 5]
            site_content = "\n".join(text_blocks[:80])

    news = scrape_company_news_from_site(company_url)

    structured = scrape_structured_data(company_url)

    summary = summarize_blocks(company_name, site_content, news, structured)

    for key in ["facts", "products_services", "locations", "recent_news", "social_media"]:
        if not summary.get(key):
            summary[key] = fallback.get(key)

    return summary


