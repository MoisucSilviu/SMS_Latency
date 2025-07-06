import os
import re
import sys
import time
import threading
import requests
from flask import Flask, request, render_template_string, Response, redirect, url_for, jsonify
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

# ✨ NEW: Parses numbers and names from the format "+1... (Name)"
def parse_destinations(dest_str):
    if not dest_str:
        return []
    # Regex to find a number and an optional name in parentheses
    return re.findall(r'(\+\d{11})\s*(?:\(([^)]+)\))?', dest_str)
DESTINATION_NUMBERS = parse_destinations(os.getenv("DESTINATION_NUMBERS", ""))


# Static Image for MMS
STATIC_MMS_IMAGE_URL = "https://i.imgur.com/e3j2F0u.png"

# Basic Auth Credentials
APP_USERNAME = os.getenv("APP_USERNAME", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "password")

# --- GLOBAL VARIABLES & APP SETUP ---
# This will now store results by a "batch ID" for each bulk run
bulk_results = {}
app = Flask(__name__)

# --- BASIC AUTHENTICATION ---
# ... (Authentication code remains the same) ...
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
        .highlight { background-color: var(--pico-color-green-100); }
        .nav { margin-bottom: 20px; padding-bottom: 10px; border-bottom: 1px solid #ccc; }
        .loader { border: 4px solid #f3f3f3; border-top: 4px solid #3498db; border-radius: 50%; width: 30px; height: 30px; animation: spin 1s linear infinite; margin-top: 10px; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    </style>
</head>
<body>
<main class="container">
    <nav>
        <ul><li><strong>Bandwidth Tools</strong></li></ul>
        <ul><li><a href="/">Bulk Tester</a></li></ul>
    </nav>
"""
HTML_FOOTER = """
</main>
</body>
</html>
"""

HTML_BULK_FORM = HTML_HEADER + """
    <article>
        <h2>Bulk Performance Tester</h2>
        <p>This tool will send an SMS and an MMS from both your Toll-Free and 10DLC numbers to the following destinations:</p>
        {% if numbers %}
            <ul>
            {% for number, name in numbers %}
                <li>{{ number }} {% if name %}({{ name }}){% endif %}</li>
            {% endfor %}
            </ul>
        {% else %}
            <p><em>No destination numbers configured. Please set the DESTINATION_NUMBERS environment variable.</em></p>
        {% endif %}
        <form action="/run_bulk_test" method="post">
            <button type="submit" {% if not numbers %}disabled{% endif %}>Start Performance Test</button>
        </form>
    </article>
""" + HTML_FOOTER

# ✨ NEW: Interactive waiting page with JavaScript polling
HTML_BULK_RESULTS = HTML_HEADER + """
    <article id="results-article">
        <hgroup>
            <h2>Bulk Test Results</h2>
            <p id="status-text">Tests in progress... please wait. The page will update automatically.</p>
        </hgroup>
        <div class="loader" id="loader"></div>
        
        <div id="sms-results" style="display:none;">
            <h3>SMS Results</h3>
            <figure><table id="sms-table"></table></figure>
        </div>

        <div id="mms-results" style="display:none;">
            <h3>MMS Results</h3>
            <figure><table id="mms-table"></table></figure>
        </div>
        
        <br>
        <a href="/" role="button" class="secondary">Run a new test</a>
    </article>
    <script>
        const batchId = '{{ batch_id }}';
        
        function buildTable(data, tableId) {
            let table = document.getElementById(tableId);
            table.innerHTML = `<thead><tr><th>From</th><th>To</th><th>Carrier</th><th>Status</th><th>Latency (s)</th></tr></thead>`;
            let tbody = document.createElement('tbody');
            
            // Highlight the best result
            let bestLatency = Infinity;
            if (data.length > 0) {
                const delivered = data.filter(r => r.latency !== null);
                if (delivered.length > 0) {
                    bestLatency = Math.min(...delivered.map(r => r.latency));
                }
            }

            for (const row of data) {
                let tr = document.createElement('tr');
                if (row.latency === bestLatency) {
                    tr.classList.add('highlight');
                }
                tr.innerHTML = `<td>${row.from_num}</td>
                                <td>${row.to_num}</td>
                                <td>${row.carrier_name}</td>
                                <td>${row.status}</td>
                                <td>${row.latency !== null ? row.latency.toFixed(2) : 'N/A'}</td>`;
                tbody.appendChild(tr);
            }
            table.appendChild(tbody);
        }

        const interval = setInterval(() => {
            fetch(`/api/bulk_status/${batchId}`)
                .then(response => response.json())
                .then(data => {
                    if (data.sms_results.length > 0) {
                        document.getElementById('sms-results').style.display = 'block';
                        buildTable(data.sms_results, 'sms-table');
                    }
                    if (data.mms_results.length > 0) {
                        document.getElementById('mms-results').style.display = 'block';
                        buildTable(data.mms_results, 'mms-table');
                    }

                    if (data.is_complete) {
                        document.getElementById('loader').style.display = 'none';
                        document.getElementById('status-text').innerText = 'All tests are complete.';
                        clearInterval(interval);
                    }
                });
        }, 3000); // Poll every 3 seconds
    </script>
""" + HTML_FOOTER


# --- FLASK ROUTES ---
@app.route("/")
@requires_auth
def index():
    return render_template_string(HTML_BULK_FORM, numbers=DESTINATION_NUMBERS)

@app.route("/run_bulk_test", methods=["POST"])
@requires_auth
def run_bulk_test():
    batch_id = f"batch_{time.time()}"
    bulk_results[batch_id] = {}
    
    from_numbers = [
        {"name": "TF", "number": TF_NUMBER, "appId": TF_APP_ID},
        {"name": "10DLC", "number": TEN_DLC_NUMBER, "appId": TEN_DLC_APP_ID}
    ]
    message_types = ["sms", "mms"]
    
    for dest_num, carrier_name in DESTINATION_NUMBERS:
        for from_data in from_numbers:
            for msg_type in message_types:
                test_id = f"test_{time.time()}_{len(bulk_results[batch_id])}"
                bulk_results[batch_id][test_id] = {
                    "from_num": from_data["number"],
                    "to_num": dest_num,
                    "carrier_name": carrier_name or 'N/A',
                    "type": msg_type.upper(),
                    "status": "Sending...",
                    "latency": None
                }
                args = (batch_id, test_id, from_data["number"], from_data["appId"], dest_num, msg_type, f"{from_data['name']} {msg_type.upper()} Test")
                threading.Thread(target=send_message, args=args).start()

    return redirect(url_for('bulk_results_page', batch_id=batch_id))

# ✨ NEW: Page to display interactive results
@app.route("/bulk_results/<batch_id>")
@requires_auth
def bulk_results_page(batch_id):
    return render_template_string(HTML_BULK_RESULTS, batch_id=batch_id)

# ✨ NEW: API endpoint for JavaScript to poll for status updates
@app.route("/api/bulk_status/<batch_id>")
@requires_auth
def api_bulk_status(batch_id):
    batch = bulk_results.get(batch_id, {})
    all_tests = list(batch.values())
    
    # Check if all tests are in a final state
    is_complete = all(r['status'] not in ['Sending...', 'Sent'] for r in all_tests)
    
    # Separate and sort results
    sms_results = sorted([r for r in all_tests if r['type'] == 'SMS'], key=lambda x: (x['latency'] is None, x['latency']))
    mms_results = sorted([r for r in all_tests if r['type'] == 'MMS'], key=lambda x: (x['latency'] is None, x['latency']))

    return jsonify({
        "is_complete": is_complete,
        "sms_results": sms_results,
        "mms_results": mms_results
    })


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    data = request.get_json()
    for event in data:
        message_info = event.get("message", {})
        test_id_from_tag = message_info.get("tag")
        
        # Find which batch this test belongs to
        target_batch_id = None
        for batch_id, tests in bulk_results.items():
            if test_id_from_tag in tests:
                target_batch_id = batch_id
                break
        
        if target_batch_id:
            event_type = event.get("type")
            if event_type == "message-delivered":
                start_time = bulk_results[target_batch_id][test_id_from_tag].get("start_time")
                if start_time:
                    bulk_results[target_batch_id][test_id_from_tag]["latency"] = time.time() - start_time
                    bulk_results[target_batch_id][test_id_from_tag]["status"] = "Delivered"
            elif event_type == "message-failed":
                bulk_results[target_batch_id][test_id_from_tag]["status"] = f"Failed: {event.get('description')}"
    return "OK", 200

# --- CORE LOGIC ---
def send_message(batch_id, test_id, from_number, application_id, destination_number, message_type, text_content):
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
            if batch_id in bulk_results and test_id in bulk_results[batch_id]:
                bulk_results[batch_id][test_id]["start_time"] = time.time()
                bulk_results[batch_id][test_id]["status"] = "Sent"
        else:
            if batch_id in bulk_results and test_id in bulk_results[batch_id]:
                bulk_results[batch_id][test_id]["status"] = f"API Error ({response.status_code})"
    except Exception:
        if batch_id in bulk_results and test_id in bulk_results[batch_id]:
            bulk_results[batch_id][test_id]["status"] = "Request Error"

# This block is for local development
if __name__ == "__main__":
    print("This script is intended to be run with a production WSGI server like Gunicorn.")
