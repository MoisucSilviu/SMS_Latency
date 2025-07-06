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

def parse_destinations(dest_str):
    if not dest_str: return []
    return re.findall(r'(\+\d{11})\s*(?:\(([^)]+)\))?', dest_str)
DESTINATION_NUMBERS = parse_destinations(os.getenv("DESTINATION_NUMBERS", ""))

STATIC_MMS_IMAGE_URL = "https://i.imgur.com/e3j2F0u.png"
APP_USERNAME = os.getenv("APP_USERNAME", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "password")

# --- GLOBAL VARIABLES & APP SETUP ---
single_test_results = {}
bulk_results = {}
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
        .grid { grid-template-columns: 1fr 1fr; }
        .loader { border: 4px solid #f3f3f3; border-top: 4px solid #3498db; border-radius: 50%; width: 30px; height: 30px; animation: spin 1s linear infinite; margin-top: 10px; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
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
HTML_DLR_FORM = HTML_HEADER + """
    <article>
        <h2>Advanced Messaging DLR Tester</h2>
        <form action="/run_test" method="post">
            </form>
    </article>
""" + HTML_FOOTER
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

# ✨ MODIFIED: HTML for the bulk results page to have 4 distinct sections
HTML_BULK_RESULTS_PAGE = HTML_HEADER + """
    <article id="results-article">
        <hgroup>
            <h2>Bulk Test Results</h2>
            <p id="status-text">Tests in progress... please wait. The page will update automatically.</p>
        </hgroup>
        <div class="loader" id="loader"></div>
        
        <div class="grid">
            <div id="sms-10dlc-results" style="display:none;">
                <h3>SMS Results (10DLC)</h3>
                <figure><table id="sms-10dlc-table"></table></figure>
            </div>
            <div id="sms-tf-results" style="display:none;">
                <h3>SMS Results (Toll-Free)</h3>
                <figure><table id="sms-tf-table"></table></figure>
            </div>
        </div>
        <div class="grid">
            <div id="mms-10dlc-results" style="display:none;">
                <h3>MMS Results (10DLC)</h3>
                <figure><table id="mms-10dlc-table"></table></figure>
            </div>
            <div id="mms-tf-results" style="display:none;">
                <h3>MMS Results (Toll-Free)</h3>
                <figure><table id="mms-tf-table"></table></figure>
            </div>
        </div>
        <br>
        <a href="/bulk" role="button" class="secondary">Run a new bulk test</a>
    </article>
    <script>
        const batchId = '{{ batch_id }}';
        
        function buildTable(data, tableId) {
            let table = document.getElementById(tableId);
            table.innerHTML = `<thead><tr><th>To</th><th>Carrier</th><th>Status</th><th>Latency (s)</th></tr></thead>`;
            let tbody = document.createElement('tbody');
            
            let bestLatency = Infinity;
            if (data.length > 0) {
                const delivered = data.filter(r => r.latency !== null);
                if (delivered.length > 0) { bestLatency = Math.min(...delivered.map(r => r.latency)); }
            }

            for (const row of data) {
                let tr = document.createElement('tr');
                if (row.latency === bestLatency) { tr.classList.add('highlight'); }
                tr.innerHTML = `<td>${row.to_num}</td>
                                <td>${row.carrier_name}</td>
                                <td>${row.status}</td>
                                <td>${row.latency !== null ? row.latency.toFixed(2) : 'N/A'}</td>`;
                tbody.appendChild(tr);
            }
            table.appendChild(tbody);
        }

        function updateResults(data) {
            const sections = {
                "sms-10dlc": data.sms.dlc,
                "sms-tf": data.sms.tf,
                "mms-10dlc": data.mms.dlc,
                "mms-tf": data.mms.tf
            };

            for (const key in sections) {
                if (sections[key].length > 0) {
                    document.getElementById(key + '-results').style.display = 'block';
                    buildTable(sections[key], key + '-table');
                }
            }
        }

        const interval = setInterval(() => {
            fetch(`/api/bulk_status/${batchId}`)
                .then(response => response.json())
                .then(data => {
                    updateResults(data.results);
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
    return render_template_string(HTML_DLR_FORM)

@app.route("/bulk")
@requires_auth
def bulk_tester_page():
    return render_template_string(HTML_BULK_FORM, numbers=DESTINATION_NUMBERS)

# ✨ MODIFIED: Main DLR tester route simplified
@app.route("/run_test", methods=["POST"])
@requires_auth
def run_latency_test():
    # This feature is now secondary to the bulk tester.
    # We can fully implement it later if needed, for now redirecting.
    return redirect(url_for('index'))

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
                test_id = f"bulk_{time.time()}_{len(bulk_results[batch_id])}"
                bulk_results[batch_id][test_id] = {
                    "from_name": from_data["name"],
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

@app.route("/bulk_results/<batch_id>")
@requires_auth
def bulk_results_page(batch_id):
    return render_template_string(HTML_BULK_RESULTS_PAGE, batch_id=batch_id)

# ✨ MODIFIED: API endpoint to structure data for the new tables
@app.route("/api/bulk_status/<batch_id>")
@requires_auth
def api_bulk_status(batch_id):
    batch = bulk_results.get(batch_id, {})
    all_tests = list(batch.values())
    is_complete = all(r['status'] not in ['Sending...', 'Sent'] for r in all_tests)
    
    results_payload = {
        "sms": {"tf": [], "dlc": []},
        "mms": {"tf": [], "dlc": []}
    }

    for test in all_tests:
        if test["type"] == 'SMS':
            if test["from_name"] == 'TF':
                results_payload["sms"]["tf"].append(test)
            else:
                results_payload["sms"]["dlc"].append(test)
        elif test["type"] == 'MMS':
            if test["from_name"] == 'TF':
                results_payload["mms"]["tf"].append(test)
            else:
                results_payload["mms"]["dlc"].append(test)
    
    # Sort each list by latency
    for msg_type in results_payload:
        for num_type in results_payload[msg_type]:
            results_payload[msg_type][num_type].sort(key=lambda x: (x['latency'] is None, x['latency']))

    return jsonify({"is_complete": is_complete, "results": results_payload})

@app.route("/webhook", methods=["POST"])
def handle_webhook():
    data = request.get_json()
    for event in data:
        message_info = event.get("message", {})
        test_id_from_tag = message_info.get("tag")
        
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
    payload = {"to": [destination_number], "from": from_number, "text": text_content, "applicationId": application_id, "tag": test_id}
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
