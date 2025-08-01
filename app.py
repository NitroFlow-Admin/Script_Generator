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
        logging.info("📥 Form POST received:")
        logging.info(json.dumps(request.form.to_dict(), indent=2))

        # --- reCAPTCHA Verification ---
        recaptcha_response = request.form.get("g-recaptcha-response", "")
        if not recaptcha_response:
            return render_template("form.html", error="❌ Missing reCAPTCHA response.", RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY)

        verify_resp = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": RECAPTCHA_SECRET_KEY, "response": recaptcha_response}
        )
        result = verify_resp.json()
        logging.info(f"🔐 reCAPTCHA response: {result}")
        if not result.get("success") or result.get("score", 0) < 0.5:
            return render_template("form.html", error="❌ reCAPTCHA verification failed. Please try again.", RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY)

        # --- Collect and validate form inputs ---
        rep_keys = ["rep_email", "rep_name", "rep_company", "product", "objection_needs", "objection_service", "objection_source", "objection_price", "objection_time"]
        target_keys = ["target_name", "target_url", "recent_news", "locations", "facts", "products_services", "social_media"]

        rep_data = {k: request.form.get(k, "").strip() for k in rep_keys}
        target_data = {k: request.form.get(k, "").strip() for k in target_keys}

        if not all(rep_data.values()) or not all(target_data.values()):
            missing = [k for k in rep_keys + target_keys if not request.form.get(k)]
            return render_template("form.html", error=f"❌ Missing required fields: {', '.join(missing)}", RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY, rep_data=rep_data, target_data=target_data)

        # --- Correct Prompt Descriptions ---
        prompt_descriptions = [
            "Opening: Start with 'Good morning' or 'Good afternoon', give the rep's name and company, and ask a closed-ended factual question about the target company related to freight between USA and Canada.",
            "Customer Assessment: Ask a closed-ended question that probes how the target manages its freight operations across USA/Canada.",
            "Needs Assessment: Ask a closed-ended question about the company’s current or upcoming freight needs.",
            "Risk Assessment: Ask a closed-ended question that highlights risk and consequences of not addressing freight gaps.",
            "Solution Assessment: Ask a closed-ended question about what the company looks for in a freight partner.",
            "Needs Objection: Ask a closed-ended question countering the 'we're happy with our current carrier' objection.",
            "Service Objection: Ask a closed-ended question addressing prior service dissatisfaction.",
            "Source Objection: Ask a closed-ended question addressing concerns about using brokers.",
            "Price Objection: Ask a closed-ended question about value relative to cost.",
            "Time Objection: Ask a closed-ended question countering the 'not a good time' objection.",
            "Closing Question: Ask a closed-ended final call-to-action or decision qualifier question."
        ]

        # --- Build prompt with strict formatting guidance ---
        prompt = f"""
You are a professional cold call script assistant.

Sales Rep: {rep_data['rep_name']} from {rep_data['rep_company']}.
They are selling: {rep_data['product']}.
Target company: {target_data['target_name']}.

Please return exactly 11 blocks.
Each block must be numbered 1–11, and contain exactly 4 bullet points:
- version A
- version B
- version C
- version D

Do not add commentary. Do not change format. Do not skip numbers.

Example:
1.
- version A
- version B
- version C
- version D
2.
...

Instructions:
""" + "\n".join([f"{i+1}. {desc}" for i, desc in enumerate(prompt_descriptions)])

        # --- Call OpenAI ---
        import time
        start = time.time()
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=2500
        )
        raw_output = response.choices[0].message.content.strip()

        with open("logs/openai_raw_output.txt", "w") as f:
            f.write(raw_output)

        logging.info(f"✅ OpenAI returned in {time.time() - start:.2f}s")
        logging.debug(f"🧠 Raw Output:\n{raw_output}")

        # --- Parse AI Output ---
        script_items = []
        current = {"label": "", "options": []}
        lines = raw_output.splitlines()

        for line in lines:
            line = line.strip()
            import re
            if re.match(r"^\d+\.$", line):
                if current["label"]:
                    script_items.append(current)
                idx = int(line.split(".")[0]) - 1
                current = {
                    "label": prompt_descriptions[idx] if idx < len(prompt_descriptions) else f"Extra Block {idx+1}",
                    "options": []
                }
            elif line.startswith("- "):
                current["options"].append(line[2:].strip())

        if current["label"]:
            script_items.append(current)

        # Sanity check — must be 11 blocks, each with 4 options
        if len(script_items) != 11 or any(len(item["options"]) != 4 for item in script_items):
            raise ValueError("❌ AI response was incomplete or misformatted.")

        return render_template(
            "index.html",
            script_items=script_items,
            rep_data=rep_data,
            target_data=target_data,
            prompt_descriptions=prompt_descriptions,
            RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY
        )

    except Exception as e:
        logging.exception("🔥 Script Generation Error")
        return render_template(
            "form.html",
            error=f"❌ Internal Error: {str(e)}",
            RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY,
            rep_data=rep_data if 'rep_data' in locals() else {},
            target_data=target_data if 'target_data' in locals() else {}
        )



@app.route("/push-to-salesdrip", methods=["POST"])
def push_to_salesdrip():
    try:
        from salesdrip_export import save_script_to_crm

        # Extract all 4 versions of each script prompt
        script_items = []
        for i in range(11):
            versions = request.form.getlist(f"script_item_{i}")
            script_items.append({
                "label": f"Line {i+1}",
                "options": [v.strip() for v in versions if v.strip()]
            })

        # Descriptions used for labeling the prompt blocks
        prompt_descriptions = [
            "Opening Script",
            "Customer Assessment",
            "Needs Assessment",
            "Risk Assessment",
            "Solution Assessment",
            "Needs Objection",
            "Service Objection",
            "Source Objection",
            "Price Objection",
            "Time Objection",
            "Closing Question"
        ]

        # Dummy email required by SalesDrip API
        dummy_email = "script.only@ai.local"

        # Call the export function with script-only data
        success = save_script_to_crm(dummy_email, {}, {}, script_items)

        return render_template(
            "index.html",
            script_items=script_items,
            rep_data={},           # empty placeholders
            target_data={},        # empty placeholders
            prompt_descriptions=prompt_descriptions,
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

        social_data = results.get("social_media", {})

        if isinstance(social_data, dict) and social_data:
            social_media = "; ".join(f"{platform}: {url}" for platform, url in social_data.items())
        else:
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
            blacklist = {"organic", "international", "headquarters", "hq", "global", "warehouse", "warehouses", "ai", "us"}
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
