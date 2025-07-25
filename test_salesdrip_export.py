from dotenv import load_dotenv
load_dotenv()


from salesdrip_export import save_script_to_crm

rep_data = {
    "rep_name": "Test Rep",
    "rep_company": "TestCo",
    "product": "Test Product",
    "objection_needs": "They don't believe they need it.",
    "objection_service": "They prefer their current provider.",
    "objection_source": "They don't trust our company.",
    "objection_price": "Too expensive.",
    "objection_time": "No time right now."
}

target_data = {
    "target_name": "Test Target Inc",
    "target_url": "https://example.com",
    "recent_news": "Just raised Series B funding.",
    "locations": "New York, Austin, Remote",
    "facts": "250 employees, SaaS company",
    "products_services": "HR software, payroll integrations",
    "social_media": "@testtarget"
}

script_items = [
    {"label": "Opening Script", "options": ["Good morning, this is Test Rep from TestCo. I noticed your company recently raised Series B funding—can I ask how you're handling onboarding at scale?"]},
    {"label": "Customer Assessment", "options": ["Do you sell primarily to mid-sized companies or enterprise accounts?"]},
    {"label": "Needs Assessment", "options": ["Is employee onboarding still a bottleneck in your growth strategy?"]},
    {"label": "Risk Assessment", "options": ["Are you concerned about losing top candidates due to slow hiring processes?"]},
    {"label": "Solution Assessment", "options": ["Would a 7-day onboarding cut reduce churn in your HR team?"]},
    {"label": "Needs Objection Resolution", "options": ["Would it be valuable to explore if your team is truly at max efficiency right now?"]},
    {"label": "Service Objection Resolution", "options": ["Would you consider a trial to compare us to your current vendor?"]},
    {"label": "Source Objection Resolution", "options": ["Would seeing our G2 reviews address any concerns about credibility?"]},
    {"label": "Price Objection Resolution", "options": ["If ROI exceeds cost in 60 days, would budget still be a blocker?"]},
    {"label": "Time Objection Resolution", "options": ["If we handle 90% of the onboarding, would it still be 'bad timing'?"]},
    {"label": "Closing Question", "options": ["Would it be a bad idea to send over a sample onboarding plan today?"]}
]

email = "test@salesdrip.com"

success = save_script_to_crm(email, rep_data, target_data, script_items)

print("✅ Success" if success else "❌ Failed")
