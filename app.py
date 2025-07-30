import os
import sys
import logging
import atexit
import json
import requests
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from openai import OpenAI
from research_engine import run_ethical_scraper, safe_get, log_event
from urllib.parse import urljoin
from pathlib import Path
import traceback

# Load environment variables
load_dotenv()

# --- Logging Setup ---
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "scraper_activity.log"
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)

# Flask app setup
app = Flask(__name__, template_folder="templates", static_folder="static")
CORS(app)


# API keys and secrets
RECAPTCHA_SECRET_KEY = os.getenv("RECAPTCHA_SECRET_KEY")
RECAPTCHA_SITE_KEY = os.getenv("RECAPTCHA_SITE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# OpenAI client setup
client = OpenAI(api_key=OPENAI_API_KEY)

# Error handling for uncaught exceptions
def log_exit():
    logging.warning("⚠️ Python interpreter is exiting unexpectedly.")
    traceback.print_stack()

def handle_exception(exc_type, exc_value, exc_traceback):
    if not issubclass(exc_type, SystemExit):
        logging.critical("💥 Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

sys.excepthook = handle_exception

@app.route("/", methods=["GET", "POST"])
def handle_form():
    if request.method == "GET":
        return render_template("form.html", RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY)

    try:
        # reCAPTCHA v3 verification
        recaptcha_response = request.form.get("g-recaptcha-response", "")
        if not recaptcha_response:
            return render_template("form.html", error="Missing reCAPTCHA response.", RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY)

        verify_resp = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": RECAPTCHA_SECRET_KEY, "response": recaptcha_response}
        )
        result = verify_resp.json()
        if not result.get("success") or result.get("score", 0) < 0.5:
            return render_template("form.html", error="reCAPTCHA verification failed. Please try again.", RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY)

        # Sales rep + target form data
        rep_data = {key: request.form.get(key, "") for key in ["rep_email", "rep_name", "rep_company", "product", "objection_needs", "objection_service", "objection_source", "objection_price", "objection_time"]}
        target_data = {key: request.form.get(key, "") for key in ["target_name", "target_url", "recent_news", "locations", "facts", "products_services", "social_media"]}

        # Check for missing required fields
        if not all(rep_data.values()) or not all(target_data.values()):
            return render_template("form.html", error="Please complete all required fields.", RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY)

        # Generate cold call script
        company_info = f"""
Website: {target_data['target_url']}
Recent News: {target_data['recent_news']}
Locations: {target_data['locations']}
Company Facts: {target_data['facts']}
Products & Services: {target_data['products_services']}
Social Media: {target_data['social_media']}
"""
        prompt_descriptions = [
            "Start with 'Good morning' or 'Good afternoon', give the rep's name and company, and ask a closed-end factual question about the target company that pertains to freight to, from or between its locations in the USA and Canada.",
            "A closed-ended question about the customer’s freight service needs relating to, from or between the locations in the USA and Canada.",
            # Add remaining descriptions...
        ]

        # Attempt to generate script items using OpenAI
        script_items = [{"label": label, "options": []} for label in prompt_descriptions]
        attempts = 0

        while any(len(item["options"]) < 4 for item in script_items) and attempts < 10:
            lines = []
            try:
                prompt = f"""
You are a professional cold call script assistant.
Sales Rep: {rep_data['rep_name']} from {rep_data['rep_company']}.
They are selling: {rep_data['product']}.
Target company: {target_data['target_name']}.

Please generate each of the following as a single short, professional, closed-ended sentence:
""" + "\n".join([f"{i+1}. {desc}" for i, desc in enumerate(prompt_descriptions)])

                # Call OpenAI API
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.5,
                    max_tokens=2500
                )

                content = response.choices[0].message.content.strip()
                for line in content.splitlines():
                    if line.strip() and any(line.strip().startswith(f"{i+1}.") for i in range(11)):
                        lines.append(line.strip())

                if len(lines) < 11:
                    logging.warning(f"❌ AI returned only {len(lines)} lines. Retrying...")
                    attempts += 1
                    continue

                for j, line in enumerate(lines):
                    if line not in script_items[j]["options"]:
                        script_items[j]["options"].append(line)

            except Exception as e:
                logging.warning(f"❌ OpenAI call failed on attempt {attempts + 1}: {str(e)}")
                attempts += 1
                continue

        if not all(len(item["options"]) > 0 for item in script_items):
            raise ValueError("Incomplete results from AI.")

        return render_template(
            "index.html",
            script_items=script_items,
            rep_data=rep_data,
            target_data=target_data,
            prompt_descriptions=prompt_descriptions,
            RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY
        )

    except Exception as e:
        logging.exception("Error during script generation")
        return render_template("form.html", error=f"An internal error occurred: {str(e)}", RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY)

@app.route("/push-to-salesdrip", methods=["POST"])
def push_to_salesdrip():
    try:
        from salesdrip_export import save_script_to_crm
        rep_data = {key: request.form.get(key, "") for key in ["rep_email", "rep_name", "rep_company", "product", "objection_needs", "objection_service", "objection_source", "objection_price", "objection_time"]}
        target_data = {key: request.form.get(key, "") for key in ["target_name", "target_url", "recent_news", "locations", "facts", "products_services", "social_media"]}

        if not rep_data["rep_name"] or not rep_data["rep_company"] or not rep_data["product"]:
            return render_template("form.html", error="Missing required fields.", RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY)
        if not target_data["target_name"] or not target_data["target_url"]:
            return render_template("form.html", error="Missing required target company fields.", RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY)

        script_items = [{"label": f"Line {i+1}", "options": [request.form.get(f"script_item_{i}", "")]} for i in range(11)]
        success = save_script_to_crm(rep_data["rep_email"], rep_data, target_data, script_items)

        return render_template(
            "index.html",
            script_items=script_items,
            rep_data=rep_data,
            target_data=target_data,
            crm_status="✅ Synced with SalesDrip" if success else "❌ Failed to sync",
            RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY
        )

    except Exception as e:
        logging.exception("Error pushing to SalesDrip")
        return f"❌ Error: {str(e)}", 500


### Update in app.py (inside run_autoresearch route) ###
### Update in app.py (inside run_autoresearch route) ###

@app.route("/run-autoresearch", methods=["POST"])
def run_autoresearch():
    try:
        data = request.get_json()
        domain = data.get("url", "").strip()
        name = data.get("name", "").strip()
        if not domain or not name:
            return jsonify({"error": "Missing URL or company name"}), 400

        results = run_ethical_scraper(domain)

        # --- Format fallback responses ---
        blog_posts = results.get("articles")
        if not blog_posts:
            blog_posts = "No recent posts found."

        social_media = results.get("social_media", "").strip()
        if not social_media:
            social_media = "No social media found."

        # --- Move fields from company_facts ---
        facts_raw = results.get("company_facts", {})
        products_raw = results.get("products_services", {})

        facts_cleaned = facts_raw.copy()
        product_list = []
        locations_list = []

        if isinstance(facts_cleaned.get("products_services"), list):
            product_list = facts_cleaned.pop("products_services")

        if isinstance(facts_cleaned.get("locations"), list):
            locations_list = facts_cleaned.pop("locations")

        # Remove unwanted keys
        facts_cleaned.pop("contact_info", None)

        # Combine and clean locations
        sitemap_locs = results.get("locations", "").split("; ") if results.get("locations") else []
        all_locations = locations_list + sitemap_locs

        def normalize_location_name(name):
            subs = {
                "us": "united states",
                "usa": "united states",
                "u.s.": "united states",
                "u.s.a.": "united states",
                "the united states": "united states"
            }
            return subs.get(name.lower().strip(), name.strip().lower())

        def dedup_locations(locations):
            seen = set()
            final = []
            blacklist = {"organic", "international", "headquarters", "hq", "global", "warehouse", "warehouses"}
            for loc in sorted(locations, key=lambda x: (-len(x), x)):
                norm = normalize_location_name(loc)
                if norm in blacklist:
                    continue
                if not any(norm in s for s in seen):
                    seen.add(norm)
                    final.append(loc)
            return final

        cleaned_locations = "; ".join(dedup_locations(all_locations))

        # Optionally strip likely misclassifications from product list
        bad_keywords = ["API"]
        filtered_products = [p for p in product_list if all(b.lower() not in p.lower() for b in bad_keywords)]

        return jsonify({
            "facts": facts_cleaned,
            "products_services": {"product_types": filtered_products},
            "locations": cleaned_locations,
            "recent_blog_posts": blog_posts,
            "social_media": social_media
        })

    except Exception as e:
        log_event(f"❌ /run-autoresearch failed: {e}")
        return jsonify({"error": "Something went wrong."}), 500


if __name__ == "__main__":
    app.run(debug=True)
