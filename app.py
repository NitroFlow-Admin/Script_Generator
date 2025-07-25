from flask import Flask, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from openai import OpenAI
from dotenv import load_dotenv
import logging
import os
import sys
import atexit

load_dotenv()

RECAPTCHA_SECRET_KEY = os.getenv("RECAPTCHA_SECRET_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def log_exit():
    logging.warning("⚠️ Python interpreter is exiting unexpectedly.")
atexit.register(log_exit)

def handle_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, SystemExit):
        logging.error("🔻 SystemExit detected. Exit code: %s", exc_value)
    else:
        logging.critical("💥 Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))

sys.excepthook = handle_exception

app = Flask(__name__, template_folder="templates", static_folder="static")
limiter = Limiter(get_remote_address, app=app, default_limits=["5 per minute", "100 per day"])
client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]",
    handlers=[logging.FileHandler("server.log"), logging.StreamHandler()]
)

@app.route("/", methods=["GET", "POST"])
def handle_form():
    if request.method == "GET":
        return render_template("form.html")

    try:
        rep_data = {key: request.form.get(key, "") for key in [
            "rep_email", "rep_name", "rep_company", "product",
            "objection_needs", "objection_service", "objection_source",
            "objection_price", "objection_time"]}

        target_data = {key: request.form.get(key, "") for key in [
            "target_name", "target_url", "recent_news", "locations",
            "facts", "products_services", "social_media"]}

        if not rep_data["rep_name"] or not rep_data["rep_company"] or not rep_data["product"]:
            return render_template("form.html", error="Missing required Sales Rep fields.")
        if not target_data["target_name"] or not target_data["target_url"]:
            return render_template("form.html", error="Missing required Target Company fields.")

        company_info = f"""
Website: {target_data['target_url']}
Recent News: {target_data['recent_news']}
Locations: {target_data['locations']}
Company Facts: {target_data['facts']}
Products & Services: {target_data['products_services']}
Social Media: {target_data['social_media']}
"""

        labels = [
            "Opening Script", "Customer Assessment", "Needs Assessment",
            "Risk Assessment", "Solution Assessment", "Needs Objection Resolution",
            "Service Objection Resolution", "Source Objection Resolution",
            "Price Objection Resolution", "Time Objection Resolution",
            "Closing Question"
        ]

        prompt_descriptions = request.form.getlist("prompt_descriptions") if "prompt_descriptions" in request.form else [
            f"Start with 'Good morning' or 'Good afternoon', give the rep's name and company, and ask a closed-end factual question about the target company.",
            f"A closed-ended question about the target company's customer base.",
            f"A closed-ended question about the customer’s needs.",
            f"A closed-ended question that includes a risk and the consequence of not addressing it.",
            f"A closed-ended question involving a solution and the risk of not implementing it.",
            f"One closed-ended question that helps the buyer resolve this objection: \"{rep_data['objection_needs']}\"",
            f"One closed-ended question that helps the buyer resolve this objection: \"{rep_data['objection_service']}\"",
            f"One closed-ended question that helps the buyer resolve this objection: \"{rep_data['objection_source']}\"",
            f"One closed-ended question that helps the buyer resolve this objection: \"{rep_data['objection_price']}\"",
            f"One closed-ended question that helps the buyer resolve this objection: \"{rep_data['objection_time']}\"",
            "One short, professional yes/no question to close the call."
        ]

        script_items = [{"label": label, "options": []} for label in labels]
        attempts = 0

        while any(len(item["options"]) < 4 for item in script_items) and attempts < 10:
            lines = []

            try:
                prompt = f"""
You are a professional cold call script assistant.

The sales rep is named {rep_data['rep_name']} from {rep_data['rep_company']}.
They are selling: {rep_data['product']}.
The target company is: {target_data['target_name']}.
Here is what we know about the company:
{company_info}

Please generate each of the following as a single short, professional, closed-ended sentence:
""" + "\n".join([f"{i+1}. {desc}" for i, desc in enumerate(prompt_descriptions)]) + """

Return each result as a numbered list, with no explanation or extra commentary.
"""

                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.5,
                    max_tokens=2500
                )

                content = response.choices[0].message.content.strip()
                logging.debug("OpenAI raw response:\n" + content)

                for line in content.splitlines():
                    if line.strip() and any(line.strip().startswith(f"{i+1}.") for i in range(11)):
                        lines.append(line.strip())

                if len(lines) < 11:
                    logging.warning(f"❌ AI returned only {len(lines)} lines. Retrying...")
                    logging.debug("Prompt sent to OpenAI:\n" + prompt)
                    attempts += 1
                    continue

                for j, line in enumerate(lines):
                    if line not in script_items[j]["options"]:
                        script_items[j]["options"].append(line)

            except Exception as e:
                logging.warning(f"❌ OpenAI call failed on attempt {attempts + 1}: {str(e)}")
                attempts += 1
                continue

            attempts += 1

        for item in script_items:
            if len(item["options"]) == 0:
                raise ValueError(f"Incomplete results from AI: missing script item '{item['label']}'")

        logging.debug("RENDER: script_items = %s", script_items)
        logging.debug("RENDER: rep_data = %s", rep_data)
        logging.debug("RENDER: target_data = %s", target_data)
        logging.debug("RENDER: prompt_descriptions = %s", prompt_descriptions)

        try:
            return render_template(
                "index.html",
                script_items=script_items,
                rep_data=rep_data,
                target_data=target_data,
                prompt_descriptions=prompt_descriptions
            )
        except Exception as e:
            logging.exception("🔥 Template rendering failed!")
            raise

    except Exception as e:
        logging.exception("Error during script generation")
        return render_template("form.html", error=f"An internal error occurred: {str(e)}")


@app.route("/push-to-salesdrip", methods=["POST"])
def push_to_salesdrip():
    from salesdrip_export import save_script_to_crm
    try:
        rep_data = {key: request.form.get(key, "") for key in [
            "rep_email", "rep_name", "rep_company", "product",
            "objection_needs", "objection_service", "objection_source",
            "objection_price", "objection_time"]}

        target_data = {key: request.form.get(key, "") for key in [
            "target_name", "target_url", "recent_news", "locations",
            "facts", "products_services", "social_media"]}
        
        if not rep_data["rep_name"] or not rep_data["rep_company"] or not rep_data["product"]:
            return render_template("form.html", error="Missing required Sales Rep fields.")
        if not target_data["target_name"] or not target_data["target_url"]:
            return render_template("form.html", error="Missing required Target Company fields.")

        script_items = [
            {"label": f"Line {i+1}", "options": [request.form.get(f"script_item_{i}", "")]} for i in range(11)
        ]

        success = save_script_to_crm(rep_data["rep_email"], rep_data, target_data, script_items)

        prompt_descriptions = [
            f"Start with 'Good morning' or 'Good afternoon', give the rep's name and company, and ask a closed-end factual question about the target company.",
            f"A closed-ended question about the target company's customer base.",
            f"A closed-ended question about the customer’s needs.",
            f"A closed-ended question that includes a risk and the consequence of not addressing it.",
            f"A closed-ended question involving a solution and the risk of not implementing it.",
            f"One closed-ended question that helps the buyer resolve this objection: \"{rep_data['objection_needs']}\"",
            f"One closed-ended question that helps the buyer resolve this objection: \"{rep_data['objection_service']}\"",
            f"One closed-ended question that helps the buyer resolve this objection: \"{rep_data['objection_source']}\"",
            f"One closed-ended question that helps the buyer resolve this objection: \"{rep_data['objection_price']}\"",
            f"One closed-ended question that helps the buyer resolve this objection: \"{rep_data['objection_time']}\"",
            "One short, professional yes/no question to close the call."
        ]

        return render_template(
            "index.html",
            script_items=script_items,
            rep_data=rep_data,
            target_data=target_data,
            crm_status="✅ Synced with SalesDrip" if success else "❌ Failed to sync",
            prompt_descriptions=prompt_descriptions
        )

    except Exception as e:
        logging.exception("Error pushing to SalesDrip")
        return f"❌ Error: {str(e)}", 500



if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
