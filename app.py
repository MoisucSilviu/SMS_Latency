import os
import sys
import time
import threading
import requests
from flask import Flask, request, render_template_string, Response
from functools import wraps
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

# --- CONFIGURATION ---
BANDWIDTH_ACCOUNT_ID = os.getenv("BANDWIDTH_ACCOUNT_ID")
BANDWIDTH_API_TOKEN = os.getenv("BANDWIDTH_API_TOKEN")
BANDWIDTH_API_SECRET = os.getenv("BANDWIDTH_API_SECRET")
BANDWIDTH_APP_ID = os.getenv("BANDWIDTH_APP_ID")
BANDWIDTH_NUMBER = os.getenv("BANDWIDTH_NUMBER")
APP_USERNAME = os.getenv("APP_USERNAME", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "password")

# --- SUMO LOGIC CONFIG ---
SUMO_ACCESS_ID = os.getenv("SUMO_ACCESS_ID")
SUMO_ACCESS_KEY = os.getenv("SUMO_ACCESS_KEY")
SUMO_API_ENDPOINT = os.getenv("SUMO_API_ENDPOINT")

# --- APP SETUP ---
results = {}
app = Flask(__name__)

# --- AUTHENTICATION ---
def check_auth(username, password):
    return username == APP_USERNAME and password == APP_PASSWORD
def authenticate():
    return Response('Login Required', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})
def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated

# --- HTML & STYLES ---
HTML_HEADER = """
<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Bandwidth Support Tools</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"/>
    <style>
        body > main { padding: 2rem; }
        .error { background-color: var(--pico-form-element-invalid-background-color); color: var(--pico-form-element-invalid-color); padding: 1rem; border-radius: var(--pico-border-radius); white-space: pre-wrap; word-wrap: break-word; }
        .sent { color: var(--pico-color-azure-600); }
        pre { background-color: #f5f5f5; padding: 1rem; border-radius: var(--pico-border-radius); white-space: pre-wrap; word-wrap: break-word; }
    </style>
</head>
<body>
<main class="container">
    <nav><ul><li><strong>Bandwidth Support Tools</strong></li></ul></nav>
"""
HTML_FOOTER = """
</main>
</body>
</html>
"""
HTML_FORM = HTML_HEADER + """
    <article>
        <h2>Message Tester</h2>
        <form action="/run_test" method="post">
            <label for="destination_number">Destination Phone Number</label>
            <input type="text" id="destination_number" name="destination_number" placeholder="+15551234567" required>
            <label for="message_text">Text Message</label>
            <textarea id="message_text" name="message_text" placeholder="Enter your text caption here..."></textarea>
            <button type="submit">Run Test</button>
        </form>
    </article>
""" + HTML_FOOTER
HTML_RESULT = HTML_HEADER + """
    <article>
        <h2>Test Result</h2>
        {% if error %}
            <p class="error"><strong>Error:</strong><br>{{ error }}</p>
        {% else %}
            <h3 class="sent">✅ Message Sent Successfully!</h3>
            <p><strong>Message ID:</strong> {{ message_id }}</p>
            <hr>
            <p>The message was accepted by the API. You can now search for its logs.</p>
            <a href="/search_sumo/{{ message_id }}" role="button">Search for this ID in Sumo Logic</a>
        {% endif %}
        <br>
        <a href="/" role="button" class="secondary">Run another test</a>
    </article>
""" + HTML_FOOTER
HTML_SUMO_RESULT = HTML_HEADER + """
    <article>
        <h2>Sumo Logic Search Results</h2>
        <p>Showing logs for Message ID: <strong>{{ message_id }}</strong></p>
        {% if error %}
            <p class="error"><strong>Error:</strong><br>{{ error }}</p>
        {% elif logs %}
            <h4>Found {{ logs|length }} log entries:</h4>
            <pre><code>{% for log in logs %}{{ log }}{% endfor %}</code></pre>
        {% else %}
            <p>No logs found for this Message ID in the given time range.</p>
        {% endif %}
        <a href="/" role="button" class="secondary">Back to Tester</a>
    </article>
""" + HTML_FOOTER

# --- FLASK ROUTES ---
@app.route("/")
@requires_auth
def index():
    return render_template_string(HTML_FORM)

@app.route("/run_test", methods=["POST"])
@requires_auth
def run_test():
    destination_number = request.form["destination_number"]
    text_content = request.form["message_text"]
    
    api_url = f"https://messaging.bandwidth.com/api/v2/users/{BANDWIDTH_ACCOUNT_ID}/messages"
    auth = (BANDWIDTH_API_TOKEN, BANDWIDTH_API_SECRET)
    payload = {
        "to": [destination_number],
        "from": BANDWIDTH_NUMBER,
        "text": text_content,
        "applicationId": BANDWIDTH_APP_ID,
    }

    try:
        response = requests.post(api_url, auth=auth, headers={"Content-Type": "application/json"}, json=payload, timeout=15)
        response_data = response.json()
        if response.status_code == 202:
            message_id = response_data.get("id")
            return render_template_string(HTML_RESULT, message_id=message_id)
        else:
            error_description = response_data.get('description', 'No description provided.')
            return render_template_string(HTML_RESULT, error=f"API Error (Status {response.status_code}):\n{error_description}")
    except Exception as e:
        return render_template_string(HTML_RESULT, error=f"Request Error: {e}")

@app.route("/search_sumo/<message_id>")
@requires_auth
def search_sumo(message_id):
    if not all([SUMO_ACCESS_ID, SUMO_ACCESS_KEY, SUMO_API_ENDPOINT]):
        return render_template_string(HTML_SUMO_RESULT, message_id=message_id, error="Sumo Logic API credentials are not configured.")

    # Customize your query here
    sumo_query = f'_index=msg_api "{message_id}"'
    
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    auth = (SUMO_ACCESS_ID, SUMO_ACCESS_KEY)
    search_payload = {"query": sumo_query, "from": "now-15m", "to": "now", "timeZone": "UTC"}

    try:
        create_job_url = f"{SUMO_API_ENDPOINT}/api/v1/search/jobs"
        create_response = requests.post(create_job_url, auth=auth, headers=headers, json=search_payload, timeout=15)
        create_response.raise_for_status()
        job_id = create_response.json()["id"]

        for _ in range(15):
            time.sleep(1)
            status_url = f"{SUMO_API_ENDPOINT}/api/v1/search/jobs/{job_id}"
            status_response = requests.get(status_url, auth=auth, timeout=5)
            status_response.raise_for_status()
            job_status = status_response.json()
            if job_status["state"] == "DONE GATHERING RESULTS":
                results_url = f"{SUMO_API_ENDPOINT}/api/v1/search/jobs/{job_id}/messages?offset=0&limit=100"
                results_response = requests.get(results_url, auth=auth, timeout=15)
                results_response.raise_for_status()
                logs = [msg["map"]["_raw"] + "\n" for msg in results_response.json()["messages"]]
                return render_template_string(HTML_SUMO_RESULT, message_id=message_id, logs=logs)
        
        return render_template_string(HTML_SUMO_RESULT, message_id=message_id, error="Sumo Logic search timed out.")
    except requests.exceptions.RequestException as e:
        error_text = e.response.text if hasattr(e, 'response') and e.response else str(e)
        return render_template_string(HTML_SUMO_RESULT, message_id=message_id, error=f"Sumo Logic API Error: {error_text}")

# This block is for local development and will not be used by Gunicorn
if __name__ == "__main__":
    print("This script is intended to be run with a production WSGI server like Gunicorn.")
