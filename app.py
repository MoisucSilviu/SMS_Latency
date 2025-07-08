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
TF_NUMBER = os.getenv("TF_NUMBER")
TF_APP_ID = os.getenv("TF_APP_ID")
TEN_DLC_NUMBER = os.getenv("TEN_DLC_NUMBER")
TEN_DLC_APP_ID = os.getenv("TEN_DLC_APP_ID")
APP_USERNAME = os.getenv("APP_USERNAME", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "password")
STATIC_MMS_IMAGE_URL = "https://i.imgur.com/e3j2F0u.png"

def parse_destinations(dest_str):
    if not dest_str: return []
    return re.findall(r'(\+\d{11})\s*(?:\(([^)]+)\))?', dest_str)
DESTINATION_NUMBERS = parse_destinations(os.getenv("DESTINATION_NUMBERS", ""))

CARRIER_LIMITS = {"AT&T": 1000, "T-Mobile": 1000, "Verizon": 1200, "Sprint/Legacy": 2000, "Toll-Free": 525}

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
    <title>Bandwidth Support Tools</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"/>
    <style>
        body > main { padding: 2rem; }
        .error { background-color: var(--pico-form-element-invalid-background-color); color: var(--pico-form-element-invalid-color); padding: 1rem; border-radius: var(--pico-border-radius); }
        .highlight { background-color: var(--pico-color-green-100); }
        .timeline { list-style-type: none; padding-left: 0; }
        .timeline li { padding-left: 2rem; border-left: 3px solid var(--pico-primary); position: relative; padding-bottom: 1.5rem; margin-left: 1rem; }
        .timeline li::before { content: '✓'; position: absolute; left: -12px; top: 0; background: var(--pico-primary); color: white; width: 24px; height: 24px; border-radius: 50%; text-align: center; line-height: 24px; }
        .sent { color: var(--pico-color-azure-600); }
        .loader { border: 4px solid #f3f3f3; border-top: 4px solid #3498db; border-radius: 50%; width: 30px; height: 30px; animation: spin 1s linear infinite; margin-top: 10px; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        pre { background-color: #f5f5f5; padding: 1rem; border-radius: var(--pico-border-radius); white-space: pre-wrap; word-wrap: break-word; }
        details { margin-bottom: 1rem; }
    </style>
</head>
<body>
<main class="container">
    <nav>
        <ul><li><strong>Bandwidth Tools</strong></li></ul>
        <ul>
            <li><a href="/">DLR Tester</a></li>
            <li><a href="/bulk">Bulk Tester</a></li>
            <li><a href="/analyze">MMS Analysis Tool</a></li>
        </ul>
    </nav>
"""
HTML_FOOTER = """
</main>
</body>
</html>
"""

# --- All HTML templates for different pages ---
HTML_DLR_FORM = HTML_HEADER + """<article><h2>Advanced Messaging DLR Tester</h2><form action="/run_test" method="post"><fieldset><legend>From Number Type</legend><label for="tfn"><input type="radio" id="tfn" name="from_number_type" value="tf" checked> Toll-Free</label><label for="10dlc"><input type="radio" id="10dlc" name="from_number_type" value="10dlc"> 10DLC</label></fieldset><label for="destination_number">Destination Phone Number</label><input type="text" id="destination_number" name="destination_number" placeholder="+15551234567" required><fieldset><legend>Message Type</legend><label for="sms"><input type="radio" id="sms" name="message_type" value="sms" checked> SMS</label><label for="mms"><input type="radio" id="mms" name="message_type" value="mms"> MMS</label></fieldset><label for="message_text">Text Message</label><textarea id="message_text" name="message_text" placeholder="Enter your text caption here..."></textarea><button type="submit">Run DLR Test</button></form></article>""" + HTML_FOOTER
HTML_DLR_RESULT = HTML_HEADER + """<article><h2>Test Result</h2>{% if error %}<p class="error"><strong>Error:</strong><br>{{ error }}</p>{% elif status == 'sent' %}<h3 class="sent">✅ MMS Sent Successfully!</h3><p><strong>Message ID:</strong> {{ message_id }}</p><hr><p><strong>Note:</strong> A 'message-delivered' report was not received within the 60-second timeout.</p>{% else %}<h3>DLR Timeline</h3><ul class="timeline"><li><strong>Message Sent to API</strong><br>Timestamp: {{ events.get('sent_str', 'N/A') }}</li>{% if events.sending %}<li><strong>Sent to Carrier</strong> (Leg 1 Latency: {{ "%.2f"|format(events.sending_latency) }}s)<br>Timestamp: {{ events.get('sending_str', 'N/A') }}</li>{% endif %}{% if events.delivered %}<li><strong>Delivered to Handset</strong> (Leg 2 Latency: {{ "%.2f"|format(events.delivered_latency) }}s)<br>Timestamp: {{ events.get('delivered_str', 'N/A') }}</li>{% endif %}</ul><hr><h4>Total End-to-End Latency: {{ "%.2f"|format(events.total_latency) }} seconds</h4><p><strong>Message ID:</strong> {{ message_id }}</p>{% endif %}<br><a href="/" role="button" class="secondary">Run another test</a></article>""" + HTML_FOOTER
HTML_BULK_FORM = HTML_HEADER + """<article><h2>Bulk Performance Tester</h2><p>This tool will send an SMS and an MMS from both your Toll-Free and 10DLC numbers to the following destinations:</p>{% if numbers %}<ul>{% for number, name in numbers %}<li>{{ number }} {% if name %}({{ name }}){% endif %}</li>{% endfor %}</ul>{% else %}<p><em>No destination numbers configured. Please set the DESTINATION_NUMBERS environment variable.</em></p>{% endif %}<form action="/run_bulk_test" method="post"><button type="submit" {% if not numbers %}disabled{% endif %}>Start Performance Test</button></form></article>""" + HTML_FOOTER
HTML_BULK_RESULTS_PAGE = HTML_HEADER + """<article id="results-article"><hgroup><h2>Bulk Test Results</h2><p id="status-text">Tests in progress... please wait.</p></hgroup><div class="loader" id="loader"></div><div class="grid"><div id="sms-10dlc-results" style="display:none;"><h3>SMS Results (10DLC)</h3><figure><table id="sms-10dlc-table"></table></figure></div><div id="sms-tf-results" style="display:none;"><h3>SMS Results (Toll-Free)</h3><figure><table id="sms-tf-table"></table></figure></div></div><div class="grid"><div id="mms-10dlc-results" style="display:none;"><h3>MMS Results (10DLC)</h3><figure><table id="mms-10dlc-table"></table></figure></div><div id="mms-tf-results" style="display:none;"><h3>MMS Results (Toll-Free)</h3><figure><table id="mms-tf-table"></table></figure></div></div><br><a href="/bulk" role="button" class="secondary">Run a new bulk test</a></article><script>const batchId = '{{ batch_id }}';function buildTable(data, tableId) {let table = document.getElementById(tableId);table.innerHTML = `<thead><tr><th>To</th><th>Carrier</th><th>Status</th><th>Latency (s)</th></tr></thead>`;let tbody = document.createElement('tbody');let bestLatency = Infinity;if (data.length > 0) {const delivered = data.filter(r => r.latency !== null);if (delivered.length > 0) { bestLatency = Math.min(...delivered.map(r => r.latency)); }}for (const row of data) {let tr = document.createElement('tr');if (row.latency === bestLatency) { tr.classList.add('highlight'); }tr.innerHTML = `<td>${row.to_num}</td><td>${row.carrier_name}</td><td>${row.status}</td><td>${row.latency !== null ? row.latency.toFixed(2) : 'N/A'}</td>`;tbody.appendChild(tr);}table.appendChild(tbody);}function updateResults(data) {const sections = {"sms-10dlc": data.sms.dlc, "sms-tf": data.sms.tf, "mms-10dlc": data.mms.dlc, "mms-tf": data.mms.tf};for (const key in sections) {if (sections[key].length > 0) {document.getElementById(key + '-results').style.display = 'block';buildTable(sections[key], key + '-table');}}}const interval = setInterval(() => {fetch(`/api/bulk_status/${batchId}`).then(response => response.json()).then(data => {updateResults(data.results);if (data.is_complete) {document.getElementById('loader').style.display = 'none';document.getElementById('status-text').innerText = 'All tests are complete.';clearInterval(interval);}});}, 3000);</script>""" + HTML_FOOTER
HTML_INSPECTOR_FORM = HTML_HEADER + """<article><h2>MMS Media Analysis Tool</h2><p>Enter a media URL to check its technical details and compare against carrier limits.</p><form action="/run_analysis" method="post"><label for="media_url">Media URL</label><input type="text" id="media_url" name="media_url" placeholder="https://.../image.png" required><button type="submit">Analyze Media</button></form></article>""" + HTML_FOOTER
HTML_INSPECTOR_RESULT = HTML_HEADER + """<article><hgroup><h2>Analysis Report</h2><p><strong>URL:</strong> <a href="{{ url }}" target="_blank" style="word-break:break-all;">{{ url }}</a></p></hgroup>{% if error %}<p class="error"><strong>Error:</strong> {{ error }}</p>{% else %}<div class="grid"><section><h4>Technical Details</h4><ul>{% for check in checks %}<li>{{ check.icon }} {{ check.message }}</li>{% endfor %}</ul>{% if show_preview %}<hr><h4>Media Preview</h4><figure><img src="{{ url }}" alt="Media Preview"></figure>{% endif %}</section><section><h4>Carrier Compatibility</h4><figure><table><thead><tr><th>Carrier</th><th>Status</th><th>Note</th></tr></thead><tbody>{% for carrier in analysis %}<tr><td><strong>{{ carrier.name }}</strong></td><td><span class="{{'success' if carrier.status == 'OK' else 'error'}}">{{ carrier.status }}</span></td><td>{{ carrier.note }}</td></tr>{% endfor %}</tbody></table></figure></section></div>{% endif %}<a href="/analyze" role="button" class="secondary">Analyze another URL</a></article>""" + HTML_FOOTER
HTML_TROUBLESHOOTER = HTML_HEADER + """<article><h2>MMS Troubleshooter Quick Reference</h2><details><summary><strong>File Size & Type Limits</strong></summary><ul><li><strong>Max Size (General):</strong> 1MB is the highest size that will pass through without transcoding. For best results, keep files under 600KB.</li><li><strong>Toll-Free Max Size:</strong> 525KB total for all media files.</li><li><strong>Supported File Types:</strong> <code>image/jpeg</code>, <code>image/png</code>, <code>image/gif</code>.</li></ul><figure><table><thead><tr><th>Carrier</th><th>Max File Size</th></tr></thead><tbody><tr><td>AT&T</td><td>1MB</td></tr><tr><td>Verizon</td><td>1.2MB</td></tr><tr><td>T-Mobile</td><td>1MB</td></tr></tbody></table></figure></details><details><summary><strong>Common Error Codes & Issues</strong></summary><ul><li><strong>Error <code>554 not allowed</code>:</strong> Too many connections per IP.</li><li><strong>Error <code>Response code '554', state SEND_BODY->SEND_MAIL</code>:</strong> Destination carrier is likely denying the message due to file size.</li></ul></details><details><summary><strong>Group Messaging (MMS) Behavior</strong></summary><ul><li><strong>Google Voice:</strong> Puts all recipients in the <code>Cc:</code> field.</li><li><strong>T-Mobile 10DLC:</strong> Inbound group MMS may only deliver to the first number. Outbound group MMS may be delivered as individual SMS.</li></ul></details></article>""" + HTML_FOOTER

# --- FLASK ROUTES ---
@app.route("/")
@requires_auth
def index():
    return render_template_string(HTML_DLR_FORM)

@app.route("/bulk")
@requires_auth
def bulk_tester_page():
    return render_template_string(HTML_BULK_FORM, numbers=DESTINATION_NUMBERS)

@app.route("/analyze")
@requires_auth
def inspector_page():
    return render_template_string(HTML_INSPECTOR_FORM)

@app.route("/troubleshoot")
@requires_auth
def troubleshooter_page():
    return render_template_string(HTML_TROUBLESHOOTER)

@app.route("/run_test", methods=["POST"])
@requires_auth
def run_latency_test():
    # ... logic for single DLR test ...
    pass

@app.route("/run_bulk_test", methods=["POST"])
@requires_auth
def run_bulk_test():
    # ... logic for bulk test ...
    pass

@app.route("/bulk_results/<batch_id>")
@requires_auth
def bulk_results_page(batch_id):
    return render_template_string(HTML_BULK_RESULTS_PAGE, batch_id=batch_id)

@app.route("/api/bulk_status/<batch_id>")
@requires_auth
def api_bulk_status(batch_id):
    # ... logic for bulk status API ...
    pass

@app.route("/run_analysis", methods=["POST"])
@requires_auth
def run_analysis():
    media_url = request.form["media_url"]
    checks, analysis, show_preview = [], [], False
    try:
        response = requests.head(media_url, allow_redirects=True, timeout=10)
        if response.status_code != 200:
            return render_template_string(HTML_INSPECTOR_RESULT, url=media_url, error=f"URL not accessible (Status Code: {response.status_code}).")
        
        checks.append({"icon": "✅", "message": f"URL is accessible."})
        content_type = response.headers.get('Content-Type', 'N/A')
        if any(t in content_type for t in ['image/jpeg', 'image/png', 'image/gif']):
            checks.append({"icon": "✅", "message": f"Content-Type '{content_type}' is supported."}); show_preview = True
        else:
            checks.append({"icon": "⚠️", "message": f"Warning: Content-Type '{content_type}' may not be supported."})

        content_length = response.headers.get('Content-Length')
        if content_length:
            size_in_kb = int(content_length) / 1024
            checks.append({"icon": "✅", "message": f"File size is {size_in_kb:.0f} KB."})
            for carrier, limit in CARRIER_LIMITS.items():
                status, note = ("OK", f"Within {limit}KB limit.") if size_in_kb <= limit else ("REJECT", f"Exceeds {limit}KB limit.")
                analysis.append({"name": carrier, "status": status, "note": note})
        else:
            checks.append({"icon": "⚠️", "message": "Could not determine file size."})
        return render_template_string(HTML_INSPECTOR_RESULT, url=media_url, checks=checks, analysis=analysis, show_preview=show_preview)
    except requests.exceptions.RequestException as e:
        return render_template_string(HTML_INSPECTOR_RESULT, url=media_url, error=f"Could not connect to URL. Error: {e}")

@app.route("/webhook", methods=["POST"])
def handle_webhook():
    # ... logic for webhook handler ...
    pass

# --- CORE LOGIC ---
def send_message(from_number, application_id, destination_number, message_type, text_content, test_id, is_bulk=False):
    # ... logic for sending messages ...
    pass

# This block is for local development
if __name__ == "__main__":
    print("This script is intended to be run with a production WSGI server like Gunicorn.")
