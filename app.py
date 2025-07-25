from flask import Flask, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from openai import OpenAI
from dotenv import load_dotenv
import logging
import os

load_dotenv()

RECAPTCHA_SECRET_KEY = os.getenv("RECAPTCHA_SECRET_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

app = Flask(__name__)
limiter = Limiter(get_remote_address, app=app, default_limits=["5 per minute", "100 per day"])
client = OpenAI(api_key=OPENAI_API_KEY)
logging.basicConfig(level=logging.DEBUG)

@app.route("/", methods=["GET", "POST"])
def handle_form():
    if request.method == "GET":
        return render_template("form.html")

    try:
        rep_data = {
            "rep_name": request.form["rep_name"],
            "rep_company": request.form["rep_company"],
            "product": request.form["product"],
            "objection_needs": request.form["objection_needs"],
            "objection_service": request.form["objection_service"],
            "objection_source": request.form["objection_source"],
            "objection_price": request.form["objection_price"],
            "objection_time": request.form["objection_time"]
        }

        target_data = {
            "target_name": request.form["target_name"],
            "target_url": request.form["target_url"],
            "recent_news": request.form["recent_news"],
            "locations": request.form["locations"],
            "facts": request.form["facts"],
            "products_services": request.form["products_services"],
            "social_media": request.form["social_media"]
        }

        company_info = f"""
        Website: {target_data['target_url']}
        Recent News: {target_data['recent_news']}
        Locations: {target_data['locations']}
        Company Facts: {target_data['facts']}
        Products & Services: {target_data['products_services']}
        Social Media: {target_data['social_media']}
        """

        labels = [
            "Opening Script",
            "Customer Assessment",
            "Needs Assessment",
            "Risk Assessment",
            "Solution Assessment",
            "Needs Objection Resolution",
            "Service Objection Resolution",
            "Source Objection Resolution",
            "Price Objection Resolution",
            "Time Objection Resolution",
            "Closing Question"
        ]

        script_items = [{} for _ in labels]

        for idx, label in enumerate(labels):
            script_items[idx] = {"label": label, "options": []}

        attempts = 0
        while any(len(item["options"]) < 4 for item in script_items) and attempts < 10:
            prompt = f"""
You are a professional cold call script assistant.

The sales rep is named {rep_data['rep_name']} from {rep_data['rep_company']}.
They are selling: {rep_data['product']}.
The target company is: {target_data['target_name']}.
Here is what we know about the company:
{company_info}

Please generate each of the following as a single short, professional, closed-ended sentence:

1. Opening Script: Start with \"Good morning\" or \"Good afternoon\", give the rep's name and company, and ask a closed-end factual question about the target company.

2. Customer Assessment Question: A closed-ended question about the target company's customer base.

3. Needs Assessment Question: A closed-ended question about the customer’s needs.

4. Risk Assessment Question: A closed-ended question that includes a risk and the consequence of not addressing it.

5. Solution Assessment Question: A closed-ended question involving a solution and the risk of not implementing it.

6. Needs Objection Resolution: One closed-ended question that helps the buyer resolve this objection: \"{rep_data['objection_needs']}\"

7. Service Objection Resolution: One closed-ended question that helps the buyer resolve this objection: \"{rep_data['objection_service']}\"

8. Source Objection Resolution: One closed-ended question that helps the buyer resolve this objection: \"{rep_data['objection_source']}\"

9. Price Objection Resolution: One closed-ended question that helps the buyer resolve this objection: \"{rep_data['objection_price']}\"

10. Time Objection Resolution: One closed-ended question that helps the buyer resolve this objection: \"{rep_data['objection_time']}\"

11. Closing Question: One short, professional yes/no question to close the call.

Return each result as a numbered list, with no explanation or extra commentary.
"""

            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=2500
            )

            lines = [line.strip() for line in response.choices[0].message.content.strip().splitlines() if line.strip()]

            for j, line in enumerate(lines):
                if line not in script_items[j]["options"]:
                    script_items[j]["options"].append(line)

            attempts += 1

        return render_template("index.html", script_items=script_items, rep_data=rep_data, target_data=target_data)

    except Exception as e:
        logging.exception("Error during script generation")
        return render_template("form.html", error="An internal error occurred. Please try again.")

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")
