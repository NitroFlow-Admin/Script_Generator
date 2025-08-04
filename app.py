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
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from models import db
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

# Secret key for sessions
app.secret_key = os.getenv("APP_SECRET_KEY", "dev-secret-key")

# Flask-Login setup
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# --- Database Configuration ---
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///site.db'  # or a full DB URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# --- Create tables if they don't exist ---
with app.app_context():
    db.create_all()



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

@app.route("/auto-research-from-salesdrip", methods=["POST"])
def auto_research_from_salesdrip():
    try:
        data = request.get_json(force=True)
        logging.info(f"📬 Incoming webhook payload: {json.dumps(data, indent=2)}")

        domain = data.get("CompanyWebsite", "").strip()
        if domain and not domain.startswith("http"):
            domain = f"https://{domain}"

        company_name = data.get("CompanyName", "").strip()
        email = data.get("Email", "").strip()
        contact_id = data.get("ContactID", "").strip()

        if not domain or not company_name or not email or not contact_id:
            logging.warning("❌ Missing one or more required fields.")
            return "❌ Missing CompanyWebsite, CompanyName, Email, or ContactID", 400

        logging.info(f"🌐 Auto-research webhook hit for {company_name} ({domain}) — ContactID: {contact_id}")

        results = run_ethical_scraper(domain)
        from salesdrip_export import save_research_to_crm

        # Build research payload
        research_payload = {
            "facts": results.get("company_facts", {}),
            "products_services": results.get("products_services", {}),
            "locations": results.get("locations", ""),
            "recent_blog_posts": results.get("articles", []),
            "social_media": "; ".join(f"{k}: {v}" for k, v in results.get("social_media", {}).items())
        }

        # Save using real contact ID
        save_research_to_crm(email, company_name, research_payload, contact_id=contact_id)

        if "error" in results:
            return f"❌ Research failed: {results['error']}", 500

        # Format the summary text for webhook return (plain text, readable in SalesDrip)
        summary = f"""🧠 Auto-Research Results for {company_name}

🌍 Locations:
{results.get("locations", "N/A")}

🏢 Company Facts:
{json.dumps(results.get("company_facts", {}), indent=2)}

🛍 Products & Services:
{json.dumps(results.get("products_services", {}), indent=2)}

📣 Social Media:
{'; '.join(f'{k}: {v}' for k, v in results.get('social_media', {}).items()) or 'N/A'}

📰 Recent Blog Posts:
"""
        for article in results.get("articles", []):
            summary += f"- {article['title']}\n  {article['url']}\n  {article['excerpt']}\n\n"

        return summary.strip(), 200, {"Content-Type": "text/plain"}

    except Exception as e:
        logging.exception("🔥 Auto-research webhook error")
        return f"❌ Error: {str(e)}", 500

from flask import session, redirect, url_for


@app.route("/results", methods=["GET", "POST"])
@login_required
def results():
    import time, re
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    if request.method == "GET":
        # Show results from session if available
        script_items = session.get("script_items")
        rep_data = session.get("rep_data")
        target_data = session.get("target_data")
        prompt_descriptions = session.get("prompt_descriptions")

        if not script_items or not rep_data or not target_data or not prompt_descriptions:
            logging.warning("⚠️ Missing session data for /results GET. Redirecting to form.")
            return redirect(url_for("form"))

        return render_template("results.html",
            script_items=script_items,
            rep_data=rep_data,
            target_data=target_data,
            prompt_descriptions=prompt_descriptions,
            RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY
        )

    # POST: handle form submission and generate script
    try:
        rep_keys = [
            "rep_email", "rep_name", "rep_company", "product",
            "objection_needs", "objection_service", "objection_source",
            "objection_price", "objection_time"
        ]
        target_keys = [
            "target_name", "target_url", "recent_news", "locations",
            "facts", "products_services", "social_media"
        ]

        rep_data = {k: request.form.get(k, "").strip() for k in rep_keys}
        target_data = {k: request.form.get(k, "").strip() for k in target_keys}

        if not all(rep_data.values()) or not all(target_data.values()):
            missing = [k for k in rep_keys + target_keys if not request.form.get(k)]
            return render_template("form.html", error=f"❌ Missing required fields: {', '.join(missing)}", rep_data=rep_data, target_data=target_data)

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

Instructions:
""" + "\n".join([f"{i+1}. {desc}" for i, desc in enumerate(prompt_descriptions)])

        start = time.time()
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=2500
        )
        raw_output = response.choices[0].message.content.strip()
        logging.info(f"✅ OpenAI returned in {time.time() - start:.2f}s")

        # Parse response
        script_items = []
        current = {"label": "", "options": []}
        lines = raw_output.splitlines()

        for line in lines:
            line = line.strip()
            match = re.match(r"^(\d+)\.\s*(.*)", line)
            if match:
                if current["label"]:
                    script_items.append(current)
                idx = int(match.group(1)) - 1
                label_text = match.group(2).strip()
                if not label_text:
                    label_text = prompt_descriptions[idx] if idx < len(prompt_descriptions) else f"Block {idx+1}"
                current = {
                    "label": label_text,
                    "options": []
                }
            elif line.startswith("- "):
                current["options"].append(line[2:].strip())
        if current["label"]:
            script_items.append(current)

        if len(script_items) != 11 or any(len(item["options"]) != 4 for item in script_items):
            logging.error("❌ Script format error — expected 11 blocks with 4 options each")
            logging.error("🔍 Full OpenAI response:\n" + raw_output)
            return render_template("form.html",
                                   error="❌ AI response was incomplete or misformatted.",
                                   rep_data=rep_data,
                                   target_data=target_data)

        # Save and render
        session["script_items"] = script_items
        session["rep_data"] = rep_data
        session["target_data"] = target_data
        session["prompt_descriptions"] = prompt_descriptions

        return render_template("results.html",
            script_items=script_items,
            rep_data=rep_data,
            target_data=target_data,
            prompt_descriptions=prompt_descriptions,
            RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY
        )

    except Exception as e:
        logging.exception("🔥 Script Generation Error")
        return render_template("form.html",
                               error=f"❌ Internal Error: {str(e)}",
                               rep_data=rep_data if 'rep_data' in locals() else {},
                               target_data=target_data if 'target_data' in locals() else {})



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


from flask import redirect, flash, session
from models import db, User, Team

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "GET":
        return render_template("register.html")

    # --- Get form data ---
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    role = request.form.get("role", "").strip()
    team_name = request.form.get("team", "").strip()

    # --- Validation ---
    if not name or not email or not password or role not in {"manager", "rep"} or not team_name:
        return render_template("register.html", error="All fields are required.")

    # --- Check if user already exists ---
    if User.query.filter_by(email=email).first():
        return render_template("register.html", error="❌ Email already registered.")

    # --- Get or create team ---
    team = Team.query.filter_by(name=team_name).first()
    if not team:
        team = Team(name=team_name)
        db.session.add(team)
        db.session.commit()

    # --- Create user ---
    user = User(
        name=name,
        email=email,
        role=role,
        team_id=team.id
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    flash("✅ Registered successfully. Please log in.")
    return redirect("/login")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    user = User.query.filter_by(email=email).first()

    if not user or not user.check_password(password):
        return render_template("login.html", error="❌ Invalid email or password.")

    login_user(user)
    return redirect("/dashboard")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect("/login")


@app.route("/dashboard")
@login_required
def dashboard():
    if current_user.role == "manager":
        all_teams = Team.query.all()
        return render_template("manager_dashboard.html", user=current_user, teams=all_teams)
    else:
        return render_template("rep_dashboard.html", user=current_user)

from flask_login import login_required, current_user

@app.route("/form", methods=["GET"])
@login_required
def form():
    from flask import session

    regen = request.args.get("regen") == "true"

    # Handle regeneration from saved session
    if regen:
        rep_data = session.get("rep_data")
        target_data = session.get("target_data")

        if not rep_data or not target_data:
            logging.warning("⚠️ Missing session data for regeneration.")
            return redirect(url_for("form"))  # fallback to blank

        # Trigger script generation again by simulating the /generate flow
        logging.info("🔁 Regenerating script via /form?regen=true redirect")
        return redirect(url_for("generate_script"))

    # Default: Pre-fill from logged-in user
    rep_data = {
        "rep_name": current_user.name,
        "rep_email": current_user.email,
        "rep_company": current_user.team.name if current_user.team else "",
        "product": "",
        "objection_needs": "",
        "objection_service": "",
        "objection_source": "",
        "objection_price": "",
        "objection_time": ""
    }

    # Empty target data
    target_data = {
        "target_name": "",
        "target_url": "",
        "recent_news": "",
        "locations": "",
        "facts": "",
        "products_services": "",
        "social_media": ""
    }

    return render_template(
        "form.html",
        rep_data=rep_data,
        target_data=target_data,
        RECAPTCHA_SITE_KEY=RECAPTCHA_SITE_KEY
    )

from models import db

with app.app_context():
    db.create_all()


@app.route("/")
def homepage():
    return render_template("home.html")

from models import Prompt  # make sure Prompt is imported

@app.route("/api/prompts/<int:team_id>", methods=["GET"])
@login_required
def get_prompts_for_team(team_id):
    if current_user.role != "manager":
        return jsonify({"error": "Unauthorized"}), 403

    prompts = Prompt.query.filter_by(team_id=team_id).all()

    return jsonify([
        {
            "id": prompt.id,
            "title": prompt.title,
            "text": prompt.text,
            "versions": prompt.versions
        } for prompt in prompts
    ])


@app.route("/auto-script-from-salesdrip", methods=["POST"])
def auto_script_from_salesdrip():
    try:
        from salesdrip_export import save_script_to_crm
        import time
        import re

        def parse_salesdrip_blob(blob: str) -> dict:
            blob = blob.replace('<br>', '\n').replace('<br/>', '\n')
            pattern = r'"([^"]+)":"((?:[^"\\]|\\.)*?)"'
            matches = re.findall(pattern, blob)
            return {k: v.replace('\\"', '"').replace('\\\\', '\\') for k, v in matches}

        # Step 1: Grab raw request body
        raw_body = request.get_data(as_text=True)
        logging.info(f"📨 Raw SalesDrip webhook body:\n{raw_body}")

        # Step 2: Parse blob without relying on strict JSON
        data = parse_salesdrip_blob(raw_body)

        # Step 3: Extract fields using human-readable keys
        rep_data = {
            "rep_email": data.get("SalesRep Email", ""),
            "rep_name": data.get("SalesRep Name", ""),
            "rep_company": data.get("SalesRep Company", ""),
            "product": data.get("SalesRep Product/service", ""),
            "objection_needs": data.get("SalesRep Needs Objection", ""),
            "objection_service": data.get("SalesRep Service Objection", ""),
            "objection_source": data.get("SalesRep Source Objection", ""),
            "objection_price": data.get("SalesRep Price Objection", ""),
            "objection_time": data.get("SalesRep Time Objection", "")
        }

        target_data = {
            "target_name": data.get("CompanyName", ""),
            "target_url": data.get("CompanyWebsite", ""),
            "recent_news": data.get("Recent Blog/News Posts", ""),
            "locations": data.get("Company Locations", ""),
            "facts": data.get("Company Facts", ""),
            "products_services": data.get("Products & Services", ""),
            "social_media": data.get("Social Media or Other Notes", "")
        }

        email = data.get("Email", "")
        contact_id = data.get("ContactID", "")

        # Step 4: Build the prompt
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

Instructions:
""" + "\n".join([f"{i+1}. {desc}" for i, desc in enumerate(prompt_descriptions)])

        # Step 5: Generate script
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=2500
        )
        raw_output = response.choices[0].message.content.strip()

        # Step 6: Parse the script output (supports "1. Opening:" format)
        script_items = []
        current = {"label": "", "options": []}
        lines = raw_output.splitlines()

        for line in lines:
            line = line.strip()
            match = re.match(r"^(\d+)\.\s*(.*)", line)
            if match:
                if current["label"]:
                    script_items.append(current)
                idx = int(match.group(1)) - 1
                label_text = match.group(2).strip()
                if not label_text:
                    label_text = prompt_descriptions[idx] if idx < len(prompt_descriptions) else f"Block {idx+1}"
                current = {
                    "label": label_text,
                    "options": []
                }
            elif line.startswith("- "):
                current["options"].append(line[2:].strip())
        if current["label"]:
            script_items.append(current)

        if len(script_items) != 11 or any(len(item["options"]) != 4 for item in script_items):
            logging.error("❌ Script format error — check OpenAI output")
            logging.error("🔍 Raw output:\n" + raw_output)
            return "❌ Script formatting issue", 500

        # Step 7: Save to CRM (uses *target's* email and contact ID)
        success = save_script_to_crm(email, rep_data, target_data, script_items, contact_id=contact_id)
        return jsonify({"status": "✅ Script generated and synced" if success else "⚠️ Script generated but failed to sync"}), 200

    except Exception as e:
        logging.exception("🔥 Auto-script webhook error")
        return f"❌ Error: {str(e)}", 500

from models import Prompt

@app.route("/api/prompts/<int:prompt_id>", methods=["DELETE"])
@login_required
def delete_prompt(prompt_id):
    if current_user.role != "manager":
        return jsonify({"error": "Unauthorized"}), 403

    prompt = Prompt.query.get(prompt_id)
    if not prompt:
        return jsonify({"error": "Prompt not found"}), 404

    db.session.delete(prompt)
    db.session.commit()
    return jsonify({"status": "deleted"})


@app.route("/delete-team/<int:team_id>", methods=["POST"])
@login_required
def delete_team(team_id):
    if current_user.role != "manager":
        return "Unauthorized", 403

    team = Team.query.get_or_404(team_id)

    # Optional: prevent deleting the team the manager is currently part of
    if team.id == current_user.team_id:
        flash("❌ You cannot delete your own team.", "error")
        return redirect("/dashboard?section=teams")

    db.session.delete(team)
    db.session.commit()

    flash("✅ Team deleted.", "success")
    return redirect("/dashboard?section=teams")


@app.route("/api/prompts/<int:prompt_id>", methods=["PUT"])
@login_required
def update_prompt(prompt_id):
    if current_user.role != "manager":
        return jsonify({"error": "Unauthorized"}), 403

    prompt = Prompt.query.get(prompt_id)
    if not prompt:
        return jsonify({"error": "Prompt not found"}), 404

    data = request.get_json()
    prompt.title = data.get("title", prompt.title).strip()
    prompt.text = data.get("text", prompt.text).strip()
    prompt.versions = int(data.get("versions", prompt.versions))

    db.session.commit()
    return jsonify({"status": "updated"})


from models import Team
from flask import redirect, request, flash

@app.route("/create-team", methods=["POST"])
@login_required
def create_team():
    if current_user.role != "manager":
        return "Unauthorized", 403

    team_name = request.form.get("team_name", "").strip()
    if not team_name:
        flash("Team name is required.", "error")
        return redirect("/dashboard?section=teams")

    existing = Team.query.filter_by(name=team_name).first()
    if existing:
        flash("A team with that name already exists.", "error")
        return redirect("/dashboard?section=teams")


    new_team = Team(name=team_name)
    db.session.add(new_team)
    db.session.commit()

    flash(f"✅ Team '{team_name}' created.")
    return redirect("/dashboard?section=teams")




@app.route("/api/prompts", methods=["POST"])
@login_required
def create_prompt():
    if current_user.role != "manager":
        return jsonify({"error": "Unauthorized"}), 403

    data = request.get_json()
    title = data.get("title", "").strip()
    text = data.get("text", "").strip()
    versions = int(data.get("versions", 1))
    team_id = int(data.get("team_id"))

    if not title or not text or not team_id:
        return jsonify({"error": "Missing fields"}), 400

    prompt = Prompt(title=title, text=text, versions=versions, team_id=team_id)
    db.session.add(prompt)
    db.session.commit()

    return jsonify({"status": "success", "id": prompt.id})


if __name__ == "__main__":
    app.run(debug=True)

 