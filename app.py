import os
import sys
import time
import threading
import requests
from flask import Flask, request, render_template_string, Response
from functools import wraps
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables from a .env file
load_dotenv()

# --- CONFIGURATION ---
BANDWIDTH_ACCOUNT_ID = os.getenv("BANDWIDTH_ACCOUNT_ID")
BANDWIDTH_API_TOKEN = os.getenv("BANDWIDTH_API_TOKEN")
BANDWIDTH_API_SECRET = os.getenv("BANDWIDTH_API_SECRET")

# Number Configurations
TF_NUMBER = os.getenv("TF_NUMBER")
TF_APP_ID = os.getenv("TF_APP_ID")
TEN_DLC_NUMBER = os.getenv("TEN_DLC_NUMBER")
TEN_DLC_APP_ID = os.getenv("TEN_DLC_APP_ID")

# Comma-separated list of destination numbers for the bulk test
DESTINATION_NUMBERS = [num.strip() for num in os.getenv("DESTINATION_NUMBERS", "").split(',') if num.strip()]

# Static Image for MMS
STATIC_MMS_IMAGE_URL = "https://i.imgur.com/e3j2F0u.png"

# Basic Auth Credentials
APP_USERNAME = os.getenv("APP_USERNAME", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "password")

# --- GLOBAL VARIABLES & APP SETUP ---
results = {}
app = Flask(__name__)

# --- BASIC AUTHENTICATION ---
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

# --- HTML TEMPLATES & STYLES ---
HTML_HEADER = """
<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Bandwidth Messaging Tools</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"/>
    <style>
        body > main { padding: 2rem; }
        .error { background-color: var(--pico-form-element-invalid-background-color); color: var(--pico-form-element-invalid-color); padding: 1rem; border-radius: var(--pico-border-radius); }
        .highlight { background-color: var(--pico-color-green-100); }
    </style>
</head>
<body>
<main class="container">
    <nav>
        <ul><li><strong>Bandwidth Tools</strong></li></ul>
        <ul>
            <li><a href="/">DLR Tester</a></li>
            <li><a href="/bulk">Bulk Tester</a></li>
        </ul>
    </nav>
"""
HTML_FOOTER = """
</main>
</body>
</html>
"""
HTML_FORM = HTML_HEADER + """
    <article>
        <h2 id="latency">Advanced Messaging DLR Tester</h2>
        <form action="/run_test" method="post">
            <fieldset>
                <legend>From Number Type</legend>
                <label for="tfn"><input type="radio" id="tfn" name="from_number_type" value="tf" checked> Toll-Free</label>
                <label for="10dlc"><input type="radio" id="10dlc" name="from_number_type" value="10dlc"> 10DLC</label>
            </fieldset>

            <label for="destination_number">Destination Phone Number</label>
            <input type="text" id="destination_number" name="destination_number" placeholder="+15551234567" required>

            <fieldset>
                <legend>Message Type</legend>
                <label for="sms"><input type="radio" id="sms" name="message_type" value="sms" checked> SMS</label>
                <label for="mms"><input type="radio" id="mms" name="message_type" value="mms"> MMS</label>
            </fieldset>

            <label for="message_text">Text Message</label>
            <textarea id="message_text" name="message_text" placeholder="Enter your text caption here..."></textarea>
            
            <button type="submit">Run DLR Test</button>
        </form>
    </article>
""" + HTML_FOOTER
HTML_BULK_FORM = HTML_HEADER + """
    <article>
        <h2>Bulk Latency Runner</h2>
        <p>This tool will send an SMS and an MMS from both your Toll-Free and 10DLC numbers to the following destinations:</p>
        <ul>
        {% for num in numbers %}
            <li>{{ num }}</li>
        {% endfor %}
        </ul>
        <p>A total of <strong>""" + str(2 * 2 * len(DESTINATION_NUMBERS)) + """</strong> messages will be sent.</p>
        <form action="/run_bulk_test" method="post">
            <button type="submit">Start Performance Test</button>
        </form>
    </article>
""" + HTML_FOOTER
HTML_BULK_RESULT = HTML_HEADER + """
    <article>
        <h2>Bulk Test Results</h2>
        {% if best_result %}
            <mark><strong>Best Performance:</strong> {{ best_result.from_num }} ({{ best_result.type }}) to {{ best_result.to_num }} with a latency of {{ "%.2f"|format(best_result.latency) }}s.</mark>
        {% endif %}

        <h4>All Results:</h4>
        <figure>
        <table>
            <thead>
                <tr>
                    <th>From Number</th>
                    <th>To Number</th>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Latency (s)</th>
                </tr>
            </thead>
            <tbody>
            {% for result in results %}
                <tr class="{{ 'highlight' if result.is_best else '' }}">
                    <td>{{ result.from_num }}</td>
                    <td>{{ result.to_num }}</td>
                    <td>{{ result.type }}</td>
                    <td>{{ result.status }}</td>
                    <td>{{ "%.2f"|format(result.latency) if result.latency is not none else 'N/A' }}</td>
                </tr>
            {% endfor %}
            </tbody>
        </table>
        </figure>
        <a href="/bulk" role="button" class="secondary">Run another bulk test</a>
    </article>
""" + HTML_FOOTER

# --- FLASK ROUTES ---
@app.route("/")
@requires_auth
def index():
    # ✨ FIX: Added the missing return statement here
    return render_template_string(HTML_FORM)

@app.route("/bulk")
@requires_auth
def bulk_tester_page():
    return render_template_string(HTML_BULK_FORM, numbers=DESTINATION_NUMBERS)

@app.route("/run_test", methods=["POST"])
@requires_auth
def run_latency_test():
    # This route is for the single DLR tester, which we can simplify or remove
    # For now, let's just redirect to the main page to avoid confusion
    return redirect(url_for('index'))

@app.route("/run_bulk_test", methods=["POST"])
@requires_auth
def run_bulk_test():
    from_numbers = {
        "TF": {"number": TF_NUMBER, "appId": TF_APP_ID},
        "10DLC": {"number": TEN_DLC_NUMBER, "appId": TEN_DLC_APP_ID}
    }
    message_types = ["sms", "mms"]
    batch_test_ids = []

    for dest_num in DESTINATION_NUMBERS:
        for from_name, from_data in from_numbers.items():
            for msg_type in message_types:
                test_id = f"bulk_{time.time()}_{len(batch_test_ids)}"
                batch_test_ids.append(test_id)
                results[test_id] = {
                    "from_num": from_data["number"],
                    "to_num": dest_num,
                    "type": msg_type.upper(),
                    "status": "Sending...",
                    "latency": None
                }
                args = (from_data["number"], from_data["appId"], dest_num, msg_type, f"{from_name} {msg_type.upper()} Test", test_id)
                threading.Thread(target=send_message, args=args).start()

    time.sleep(125)

    final_results = []
    best_result = None
    for test_id in batch_test_ids:
        result = results.pop(test_id, None)
        if result:
            if result.get("status") == "Delivered" and (best_result is None or result.get("latency", float('inf')) < best_result.get("latency", float('inf'))):
                best_result = result
            final_results.append(result)
    
    if best_result:
        for result in final_results:
            result["is_best"] = (result.get("latency") is not None and result.get("latency") == best_result.get("latency"))

    return render_template_string(HTML_BULK_RESULT, results=final_results, best_result=best_result)

@app.route("/webhook", methods=["POST"])
def handle_webhook():
    data = request.get_json()
    for event in data:
        event_type = event.get("type")
        message_info = event.get("message", {})
        test_id_from_tag = message_info.get("tag")

        if test_id_from_tag in results:
            if event_type == "message-delivered":
                start_time = results[test_id_from_tag].get("start_time")
                if start_time:
                    results[test_id_from_tag]["latency"] = time.time() - start_time
                    results[test_id_from_tag]["status"] = "Delivered"
            elif event_type == "message-failed":
                results[test_id_from_tag]["status"] = f"Failed: {event.get('description')}"

    return "OK", 200

# --- CORE LOGIC ---
def send_message(from_number, application_id, destination_number, message_type, text_content, test_id):
    api_url = f"https://messaging.bandwidth.com/api/v2/users/{BANDWIDTH_ACCOUNT_ID}/messages"
    auth = (BANDWIDTH_API_TOKEN, BANDWIDTH_API_SECRET)
    headers = {"Content-Type": "application/json"}
    
    payload = {
        "to": [destination_number],
        "from": from_number,
        "text": text_content,
        "applicationId": application_id,
        "tag": test_id
    }
    
    if message_type == "mms":
        payload["media"] = [STATIC_MMS_IMAGE_URL]

    try:
        response = requests.post(api_url, auth=auth, headers=headers, json=payload, timeout=15)
        if response.status_code == 202:
            if test_id in results:
                results[test_id]["start_time"] = time.time()
                results[test_id]["status"] = "Sent"
        else:
            if test_id in results:
                results[test_id]["status"] = f"API Error ({response.status_code})"
    except Exception:
        if test_id in results:
            results[test_id]["status"] = "Request Error"

# This block is for local development
if __name__ == "__main__":
    print("This script is intended to be run with a production WSGI server like Gunicorn.")
