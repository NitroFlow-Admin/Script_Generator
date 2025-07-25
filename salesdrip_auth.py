import os
import requests
import logging

GREENROPE_ACCOUNT = os.getenv("GREENROPE_ACCOUNT_ID")
greenrope_token = None

def get_greenrope_token():
    global greenrope_token
    if greenrope_token:
        return greenrope_token

    url = "https://api.stgi.net/v2/api/login"
    payload = {
        "Email": os.getenv("GREENROPE_EMAIL"),
        "Password": os.getenv("GREENROPE_PASSWORD"),
        "ExpiryMinutes": 0,
        "ExcludeLoginData": False
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        logging.info(f"[DEBUG] Login response status: {response.status_code}")

        with open("/tmp/greenrope_login_response.txt", "w", encoding="utf-8") as f:
            f.write(response.text)

        response.raise_for_status()
        json_data = response.json()

        if "data" not in json_data or "AccessToken" not in json_data["data"]:
            logging.error(f"[ERROR] Unexpected login format: {json_data}")
            raise Exception("AccessToken missing")

        greenrope_token = json_data["data"]["AccessToken"]
        logging.info("GreenRope token acquired")
        return greenrope_token

    except Exception as e:
        logging.error(f"[ERROR] Login failed: {str(e)}", exc_info=True)
        raise
