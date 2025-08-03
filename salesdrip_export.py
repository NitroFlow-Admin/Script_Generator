import requests
import logging
import os
import json
from salesdrip_auth import get_greenrope_token

GREENROPE_ACCOUNT = os.getenv("GREENROPE_ACCOUNT_ID")

def save_script_to_crm(email, rep_data, target_data, script_items):
    token = get_greenrope_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "accountID": GREENROPE_ACCOUNT,
        "Content-Type": "application/json"
    }

    contact_id = 2
    group_name = "***2025 Ai Integrated Sales"

    def safe_option(item, index):
        try:
            return item["options"][index]
        except (IndexError, KeyError, TypeError):
            return ""

    # FieldNum mappings
    field_map = [
        # Opening Script
        (1074, script_items[0], 0),
        (1076, script_items[0], 1),
        (1073, script_items[0], 2),
        (1075, script_items[0], 3),

        # Customer Assessment
        (1057, script_items[1], 0),
        (1058, script_items[1], 1),
        (1059, script_items[1], 2),
        (1060, script_items[1], 3),

        # Needs Assessment
        (1064, script_items[2], 0),
        (1062, script_items[2], 1),
        (1063, script_items[2], 2),
        (1061, script_items[2], 3),

        # Risk Assessment
        (1065, script_items[3], 0),
        (1068, script_items[3], 1),
        (1067, script_items[3], 2),
        (1066, script_items[3], 3),

        # Solution Assessment
        (1069, script_items[4], 0),
        (1070, script_items[4], 1),
        (1072, script_items[4], 2),
        (1071, script_items[4], 3),

        # Needs Objection Resolution
        (1081, script_items[5], 0),
        (1079, script_items[5], 1),
        (1080, script_items[5], 2),
        (1078, script_items[5], 3),

        # Service Objection Resolution
        (1085, script_items[6], 0),
        (1084, script_items[6], 1),
        (1082, script_items[6], 2),
        (1083, script_items[6], 3),

        # Source Objection Resolution
        (1089, script_items[7], 0),
        (1088, script_items[7], 1),
        (1087, script_items[7], 2),
        (1086, script_items[7], 3),

        # Price Objection Resolution
        (1093, script_items[8], 0),
        (1092, script_items[8], 1),
        (1091, script_items[8], 2),
        (1090, script_items[8], 3),

        # Time Objection Resolution
        (1097, script_items[9], 0),
        (1096, script_items[9], 1),
        (1094, script_items[9], 2),
        (1095, script_items[9], 3),

        # Closing Question
        (1056, script_items[10], 0),
        (1055, script_items[10], 1),
        (1054, script_items[10], 2),
        (1053, script_items[10], 3),
    ]

    user_fields = [
        {"FieldNum": fnum, "FieldValue": safe_option(item, idx)}
        for (fnum, item, idx) in field_map
        if safe_option(item, idx).strip()  # only include non-blank
    ]

    payload = {
        "Contacts": [
            {
                "contactId": contact_id,
                "Email": email,
                "Firstname": rep_data.get("rep_name", ""),
                "Company": rep_data.get("rep_company", ""),
                "Groups": [{"GroupName": group_name}],
                "UserDefinedFields": user_fields
            }
        ]
    }

    logging.debug("[DEBUG] Payload to SalesDrip:\n%s", json.dumps(payload, indent=2))

    try:
        response = requests.put(
            "https://api.stgi.net/v2/api/contact",
            headers=headers,
            json=payload,
            timeout=10
        )
        logging.debug("[DEBUG] SalesDrip response: %s", response.text)
        response.raise_for_status()
        logging.info(f"✅ Contact {email} updated successfully in SalesDrip CRM.")
        return True
    except Exception as e:
        logging.error(f"[ERROR] Failed to update contact {email}: {e}", exc_info=True)
        return False

def save_research_to_crm(email, company_name, research_data, contact_id):
    token = get_greenrope_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "accountID": GREENROPE_ACCOUNT,
        "Content-Type": "application/json"
    }

    group_name = "***2025 Ai Integrated Sales"

    # Validate contact_id strictly
    if not contact_id or not str(contact_id).isdigit():
        raise ValueError("❌ Invalid or missing contact_id")

    # Extract facts and nested products_services if necessary
    facts = research_data.get("facts", {})
    products_services = research_data.get("products_services")

    if not products_services and isinstance(facts, dict):
        products_services = facts.pop("products_services", None)

    # Format fields as plain text
    def format_facts(f):
        if not isinstance(f, dict):
            return str(f)

        excluded_keys = {"products_services", "locations"}
        cleaned = {k: v for k, v in f.items() if k not in excluded_keys}

        lines = []
        for key, value in cleaned.items():
            if isinstance(value, list):
                value_str = "; ".join(str(v) for v in value)
            elif isinstance(value, dict):
                value_str = "; ".join(f"{k}: {v}" for k, v in value.items())
            else:
                value_str = str(value)
            lines.append(f"{key.capitalize()}: {value_str}")
        return "\n".join(lines)

    def format_products(p):
        if not p:
            return "No products or services found."

        lines = []

        if isinstance(p, dict):
            for key, val in p.items():
                if isinstance(val, list):
                    lines.extend(str(v) for v in val)
                elif isinstance(val, str):
                    lines.append(val)
        elif isinstance(p, list):
            lines.extend(str(v) for v in p)
        else:
            lines.append(str(p))

        return "\n".join(lines)

    def format_blogs(articles):
        if not articles:
            return "No recent blog posts found."
        lines = []
        for a in articles:
            lines.append(f"{a.get('title', '')}\n{a.get('url', '')}\n{a.get('excerpt', '')}\n")
        return "\n".join(lines)

    user_fields = [
        {"FieldNum": 1034, "FieldValue": format_blogs(research_data.get("recent_blog_posts", []))},
        {"FieldNum": 1035, "FieldValue": research_data.get("locations", "")},
        {"FieldNum": 1036, "FieldValue": format_facts(facts)},
        {"FieldNum": 1037, "FieldValue": format_products(products_services)},
        {"FieldNum": 1038, "FieldValue": research_data.get("social_media", "")}
    ]

    payload = {
        "Contacts": [
            {
                "contactId": int(contact_id),
                "Email": email,
                "Firstname": company_name,
                "Company": company_name,
                "Groups": [{"GroupName": group_name}],
                "UserDefinedFields": user_fields
            }
        ]
    }

    logging.debug("[DEBUG] Research Payload to SalesDrip:\n%s", json.dumps(payload, indent=2))

    try:
        response = requests.put(
            "https://api.stgi.net/v2/api/contact",
            headers=headers,
            json=payload,
            timeout=10
        )
        logging.debug("[DEBUG] SalesDrip response: %s", response.text)
        response.raise_for_status()
        logging.info(f"✅ Contact {email} (ID: {contact_id}) research fields updated successfully.")
        return True
    except Exception as e:
        logging.error(f"[ERROR] Failed to update research data for contact {email} (ID: {contact_id}): {e}", exc_info=True)
        return False
