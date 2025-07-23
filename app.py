import os
import re
import sys
import time
import threading
import requests
import io
from flask import Flask, request, render_template_string, Response, redirect, url_for, jsonify
from functools import wraps
from dotenv import load_dotenv
from datetime import datetime
from PIL import Image
import pytesseract

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

def parse_destinations(dest_str):
    if not dest_str: return []
    return re.findall(r'(\+\d{11})\s*(?:\(([^)]+)\))?', dest_str)
DESTINATION_NUMBERS = parse_destinations(os.getenv("DESTINATION_NUMBERS", ""))
STATIC_MMS_IMAGE_URL = "https://i.imgur.com/e3j2F0u.png"
CARRIER_LIMITS = {"AT&T": 1000, "T-Mobile": 1000, "Verizon": 1200, "Toll-Free": 525}

# --- GLOBAL VARIABLES & APP SETUP ---
active_tests = {}
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
    <title>Bandwidth Support Dashboard</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"/>
    <style>
        body > main { padding: 2rem; max-width: 1200px; }
        .error { background-color: var(--pico-form-element-invalid-background-color); color: var(--pico-form-element-invalid-color); padding: 1rem; border-radius: var(--pico-border-radius); }
        .highlight { background-color: var(--pico-color-green-100); }
        .timeline { list-style-type: none; padding-left: 0; }
        .timeline li { padding-left: 2rem; border-left: 3px solid var(--pico-primary); position: relative; padding-bottom: 1.5rem; margin-left: 1rem; }
        .timeline li::before { content: '✓'; position: absolute; left: -12px; top: 0; background: var(--pico-primary); color: white; width: 24px; height: 24px; border-radius: 50%; text-align: center; line-height: 24px; }
        .sent { color: var(--pico-color-azure-600); }
        .loader { border: 4px solid #f3f3f3; border-top: 4px solid #3498db; border-radius: 50%; width: 30px; height: 30px; animation: spin 1s linear infinite; margin: 20px auto; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        pre { background-color: #f5f5f5; padding: 1rem; border-radius: var(--pico-border-radius); white-space: pre-wrap; word-wrap: break-word; }
        .grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); grid-gap: 2rem; }
        section[role="tabpanel"] { display: none; padding: 1.5rem 0; border-top: 1px solid var(--pico-muted-border-color);}
        section[role="tabpanel"][aria-hidden="false"] { display: block; }
        [role="tab"] { font-weight: bold; }
        [role="tab"][aria-selected="true"] { background-color: var(--pico-primary-background); }
    </style>
</head>
<body>
<main class="container">
    <hgroup><h1>Bandwidth Support Dashboard</h1><p>A unified interface for messaging and analysis tools.</p></hgroup>
"""
HTML_NAVIGATION = """
    <div role="tablist" class="grid">
        <button role="tab" data-target="dlr-tester">DLR Tester</button>
        <button role="tab" data-target="bulk-tester">Bulk Tester</button>
        <button role="tab" data-target="mms-analyzer">MMS Analysis</button>
    </div>
"""
HTML_FOOTER = """
</main>
<script>
    const tabs = document.querySelectorAll('[role="tab"]');
    const tabPanels = document.querySelectorAll('[role="tabpanel"]');
    tabs.forEach(tab => {
        tab.addEventListener('click', (e) => {
            tabs.forEach(t => t.setAttribute('aria-selected', 'false'));
            tabPanels.forEach(p => p.setAttribute('aria-hidden', 'true'));
            const targetId = e.target.getAttribute('data-target');
            e.target.setAttribute('aria-selected', 'true');
            document.getElementById(targetId).setAttribute('aria-hidden', 'false');
        });
    });
    const hash = window.location.hash.substring(1);
    const targetTab = hash ? document.querySelector(`[data-target='${hash}']`) : tabs[0];
    if(targetTab) {
        targetTab.click();
    }
</script>
</body>
</html>
"""
HTML_DASHBOARD = HTML_HEADER + HTML_NAVIGATION + """
    <section role="tabpanel" id="dlr-tester">
        <article><h2>Advanced Messaging DLR Tester</h2><form action="/run_test" method="post"><fieldset><legend>From Number Type</legend><label for="tfn"><input type="radio" id="tfn" name="from_number_type" value="tf" checked> Toll-Free</label><label for="10dlc"><input type="radio" id="10dlc" name="from_number_type" value="10dlc"> 10DLC</label></fieldset><label for="destination_number">Destination Phone Number</label><input type="text" id="destination_number" name="destination_number" placeholder="+15551234567" required><fieldset><legend>Message Type</legend><label for="sms"><input type="radio" id="sms" name="message_type" value="sms" checked> SMS</label><label for="mms"><input type="radio" id="mms" name="message_type" value="mms"> MMS</label></fieldset><label for="message_text">Text Message</label><textarea id="message_text" name="message_text" placeholder="Enter your text caption here..."></textarea><button type="submit">Run DLR Test</button></form></article>
    </section>
    <section role="tabpanel" id="bulk-tester" aria-hidden="true">
        <article><h2>Bulk Performance Tester</h2><p>This tool will send an SMS and an MMS from both your Toll-Free and 10DLC numbers to the following destinations:</p>{% if numbers %}<ul>{% for number, name in numbers %}<li>{{ number }} {% if name %}({{ name }}){% endif %}</li>{% endfor %}</ul>{% else %}<p><em>No destination numbers configured.</em></p>{% endif %}<form action="/run_bulk_test" method="post"><button type="submit" {% if not numbers %}disabled{% endif %}>Start Performance Test</button></form></article>
    </section>
    <section role="tabpanel" id="mms-analyzer" aria-hidden="true">
        <article><h2>MMS Media Analysis Tool</h2><p>Enter a media URL to check its technical details and compare against carrier limits.</p><form action="/run_analysis" method="post"><label for="media_url">Media URL</label><input type="text" id="media_url" name="media_url" placeholder="https://.../image.png" required><button type="submit">Analyze Media</button></form></article>
    </section>
""" + HTML_FOOTER
HTML_DLR_RESULT = HTML_HEADER + """
    <article>
        <h2>Test Result</h2>
        {% if error %}<p class="error"><strong>Error:</strong><br>{{ error }}</p>
        {% elif status == 'sent' %}<h3 class="sent">✅ MMS Sent Successfully!</h3><p><strong>Message ID:</strong> {{ message_id }}</p><hr><p><strong>Note:</strong> A 'message-delivered' report was not received within the 60-second timeout.</p>
        {% else %}<h3>DLR Timeline</h3><ul class="timeline"><li><strong>Message Sent to API</strong><br>Timestamp: {{ events.get('sent_str', 'N/A') }}</li>{% if events.sending %}<li><strong>Sent to Carrier</strong> (Leg 1 Latency: {{ "%.2f"|format(events.sending_latency) }}s)<br>Timestamp: {{ events.get('sending_str', 'N/A') }}</li>{% endif %}{% if events.delivered %}<li><strong>Delivered to Handset</strong> (Leg 2 Latency: {{ "%.2f"|format(events.delivered_latency) }}s)<br>Timestamp: {{ events.get('delivered_str', 'N/A') }}</li>{% endif %}</ul><hr><h4>Total End-to-End Latency: {{ "%.2f"|format(events.total_latency) }} seconds</h4><p><strong>Message ID:</strong> {{ message_id }}</p>{% endif %}<br><a href="/" role="button" class="secondary">Back to Dashboard</a>
    </article>
""" + HTML_FOOTER
HTML_BULK_RESULTS_PAGE = HTML_HEADER + """
    <article id="results-article"><hgroup><h2>Bulk Test Results</h2><p id="status-text">Tests in progress...</p></hgroup><div class="loader" id="loader"></div><div class="grid"><div id="sms-10dlc-results" style="display:none;"><h3>SMS Results (10DLC)</h3><figure><table id="sms-10dlc-table"></table></figure></div><div id="sms-tf-results" style="display:none;"><h3>SMS Results (Toll-Free)</h3><figure><table id="sms-tf-table"></table></figure></div></div><div class="grid"><div id="mms-10dlc-results" style="display:none;"><h3>MMS Results (10DLC)</h3><figure><table id="mms-10dlc-table"></table></figure></div><div id="mms-tf-results" style="display:none;"><h3>MMS Results (Toll-Free)</h3><figure><table id="mms-tf-table"></table></figure></div></div><br><a href="/" role="button" class="secondary">Back to Dashboard</a></article>
    <script>
        const batchId = '{{ batch_id }}';
        function buildTable(data, tableId) {
            let table = document.getElementById(tableId);
            table.innerHTML = `<thead><tr><th>To</th><th>Carrier</th><th>Status</th><th>Latency (s)</th></tr></thead>`;
            let tbody = document.createElement('tbody');
            let bestLatency = Infinity;
            if (data.length > 0) { const delivered = data.filter(r => r.latency !== null); if (delivered.length > 0) { bestLatency = Math.min(...delivered.map(r => r.latency)); }}
            for (const row of data) {
                let tr = document.createElement('tr');
                if (row.latency === bestLatency) { tr.classList.add('highlight'); }
                tr.innerHTML = `<td>${row.to_num}</td><td>${row.carrier_name}</td><td>${row.status}</td><td>${row.latency !== null ? row.latency.toFixed(2) : 'N/A'}</td>`;
                tbody.appendChild(tr);
            }
            table.appendChild(tbody);
        }
        function updateResults(data) {
            const sections = {"sms-10dlc": data.sms.dlc, "sms-tf": data.sms.tf, "mms-10dlc": data.mms.dlc, "mms-tf": data.mms.tf};
            for (const key in sections) { if (sections[key].length > 0) { document.getElementById(key + '-results').style.display = 'block'; buildTable(sections[key], key + '-table'); }}
        }
        const interval = setInterval(() => {
            fetch(`/api/bulk_status/${batchId}`).then(response => response.json()).then(data => {
                updateResults(data.results);
                if (data.is_complete) {
                    document.getElementById('loader').style.display = 'none';
                    document.getElementById('status-text').innerText = 'All tests are complete.';
                    clearInterval(interval);
                }
            });
        }, 3000);
    </script>
""" + HTML_FOOTER
HTML_ANALYSIS_RESULT = HTML_HEADER + """
    <article>
        <hgroup><h2>Analysis Report</h2><p><strong>URL:</strong> <a href="{{ url }}" target="_blank" style="word-break:break-all;">{{ url }}</a></p></hgroup>
        {% if error %}<p class="error"><strong>Error:</strong> {{ error }}</p>
        {% else %}
        <div class="grid-2">
            <section><h4>Technical Details</h4><ul>{% for check in checks %}<li>{{ check.icon }} {{ check.message }}</li>{% endfor %}</ul></section>
            <section><h4>Spam Risk Analysis</h4><ul>{% for check in spam_checks %}<li>{{ check.icon }} {{ check.message }}</li>{% endfor %}</ul></section>
        </div>
        <h4>Carrier Compatibility</h4>
        <figure><table><thead><tr><th>Carrier</th><th>Status</th><th>Note</th></tr></thead><tbody>
        {% for carrier in analysis %}<tr><td><strong>{{ carrier.name }}</strong></td><td>{{ '✅ OK' if carrier.status == 'OK' else '❌ REJECT' }}</td><td>{{ carrier.note }}</td></tr>{% endfor %}
        </tbody></table></figure>
        {% if show_preview %}<hr><h4>Media Preview</h4><figure><img src="{{ url }}" alt="Media Preview" style="max-width:100%;"></figure>{% endif %}
        {% endif %}
        <a href="/" role="button" class="secondary">Back to Dashboard</a>
    </article>
""" + HTML_FOOTER

# --- FLASK ROUTES ---
@app.route("/")
@requires_auth
def dashboard():
    return render_template_string(HTML_DASHBOARD, numbers=DESTINATION_NUMBERS)

@app.route("/health")
def health_check():
    return "OK", 200

@app.route("/run_test", methods=["POST"])
@requires_auth
def run_latency_test():
    from_number_type = request.form["from_number_type"]
    from_number = TF_NUMBER if from_number_type == 'tf' else TEN_DLC_NUMBER
    application_id = TF_APP_ID if from_number_type == 'tf' else TEN_DLC_APP_ID
    destination_number = request.form["destination_number"]
    message_type = request.form["message_type"]
    text_content = request.form["message_text"]
    test_id = f"single_{time.time()}"
    delivery_event = threading.Event()
    active_tests[test_id] = {"event": delivery_event, "events": {}}
    args = (from_number, application_id, destination_number, message_type, text_content, test_id)
    threading.Thread(target=send_message, args=args).start()
    timeout = 60 if message_type == "mms" else 120
    is_complete = delivery_event.wait(timeout=timeout)
    result_data = active_tests.pop(test_id, {})
    events = result_data.get("events", {})
    if result_data.get("error"):
        return render_template_string(HTML_DLR_RESULT, error=result_data["error"])
    if not is_complete and message_type == "mms" and events.get("sent"):
        return render_template_string(HTML_DLR_RESULT, status="sent", message_id=result_data.get("message_id"))
    if not is_complete:
        return render_template_string(HTML_DLR_RESULT, error=f"TIMEOUT: No final webhook was received after {timeout} seconds.")
    events["total_latency"] = 0
    if events.get("sent"): events["sent_str"] = datetime.fromtimestamp(events["sent"]).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    if events.get("sending"):
        events["sending_str"] = datetime.fromtimestamp(events["sending"]).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        events["sending_latency"] = events["sending"] - events.get("sent", 0)
    if events.get("delivered"):
        events["delivered_str"] = datetime.fromtimestamp(events["delivered"]).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        events["delivered_latency"] = events["delivered"] - events.get("sending", events.get("sent", 0))
        events["total_latency"] = events["delivered"] - events.get("sent", 0)
    return render_template_string(HTML_DLR_RESULT, message_id=result_data.get("message_id"), events=events)

@app.route("/run_bulk_test", methods=["POST"])
@requires_auth
def run_bulk_test():
    batch_id = f"batch_{time.time()}"
    active_tests[batch_id] = {"start_time": time.time(), "tests": {}}
    from_numbers = [{"name": "TF", "number": TF_NUMBER, "appId": TF_APP_ID}, {"name": "10DLC", "number": TEN_DLC_NUMBER, "appId": TEN_DLC_APP_ID}]
    message_types = ["sms", "mms"]
    for dest_num, carrier_name in DESTINATION_NUMBERS:
        for from_data in from_numbers:
            for msg_type in message_types:
                test_id = f"bulk_{time.time()}_{len(active_tests[batch_id]['tests'])}"
                active_tests[test_id] = {
                    "batch_id": batch_id, "from_name": from_data["name"], "from_num": from_data["number"],
                    "to_num": dest_num, "carrier_name": carrier_name or 'N/A', "type": msg_type.upper(),
                    "status": "Sending...", "latency": None
                }
                args = (from_data["number"], from_data["appId"], dest_num, msg_type, f"{from_data['name']} {msg_type.upper()} Test", test_id)
                threading.Thread(target=send_message, args=args).start()
    return redirect(url_for('bulk_results_page', batch_id=batch_id))

@app.route("/bulk_results/<batch_id>")
@requires_auth
def bulk_results_page(batch_id):
    return render_template_string(HTML_BULK_RESULTS_PAGE, batch_id=batch_id)

@app.route("/api/bulk_status/<batch_id>")
@requires_auth
def api_bulk_status(batch_id):
    all_tests = [test for test in active_tests.values() if test.get("batch_id") == batch_id]
    is_complete = all(r['status'] not in ['Sending...', 'Sent'] for r in all_tests)
    batch_start_time = 0
    if batch_id in active_tests:
        batch_start_time = active_tests[batch_id].get("start_time", 0)
    if not is_complete and batch_start_time and (time.time() - batch_start_time > 125):
        is_complete = True
        for test in all_tests:
            if test['status'] == 'Sent': test['status'] = 'Timed Out'
    if is_complete and all_tests:
        tests_to_remove = [tid for tid, test in active_tests.items() if test.get("batch_id") == batch_id]
        for tid in tests_to_remove: active_tests.pop(tid, None)
        active_tests.pop(batch_id, None) 
    results_payload = {"sms": {"tf": [], "dlc": []}, "mms": {"tf": [], "dlc": []}}
    for test in all_tests:
        if test["type"] == 'SMS':
            results_payload["sms"]["tf" if test["from_name"] == 'TF' else "dlc"].append(test)
        elif test["type"] == 'MMS':
            results_payload["mms"]["tf" if test["from_name"] == 'TF' else "dlc"].append(test)
    for msg_type in results_payload:
        for num_type in results_payload[msg_type]:
            results_payload[msg_type][num_type].sort(key=lambda x: (x['latency'] is None, x['latency']))
    return jsonify({"is_complete": is_complete, "results": results_payload})

@app.route("/run_analysis", methods=["POST"])
@requires_auth
def run_analysis():
    media_url = request.form["media_url"]
    checks, spam_checks, analysis, show_preview = [], [], [], False
    try:
        response = requests.get(media_url, allow_redirects=True, timeout=10)
        response.raise_for_status()
        checks.append({"icon": "✅", "message": f"URL is accessible (Status Code: 200)."})
        content_type = response.headers.get('Content-Type', 'N/A')
        if any(t in content_type for t in ['image/jpeg', 'image/png', 'image/gif']):
            checks.append({"icon": "✅", "message": f"Content-Type '{content_type}' is supported."}); show_preview = True
        else:
            checks.append({"icon": "⚠️", "message": f"Warning: Content-Type '{content_type}' may not be supported."})
        size_in_kb = len(response.content) / 1024
        checks.append({"icon": "✅", "message": f"File size is {size_in_kb:.0f} KB."})
        for carrier, limit in CARRIER_LIMITS.items():
            status, note = ("OK", f"Within ~{limit}KB limit.") if size_in_kb <= limit else ("REJECT", f"Exceeds ~{limit}KB limit.")
            analysis.append({"name": carrier, "status": status, "note": note})
        if show_preview:
            img = Image.open(io.BytesIO(response.content)); width, height = img.size
            aspect_ratio = height / width if width > 0 else 0
            spam_checks.append({"icon": "✅" if aspect_ratio <= 3 else "⚠️", "message": "Standard aspect ratio." if aspect_ratio <= 3 else "Image is very tall/thin, may increase spam risk."})
            try:
                text_in_image = pytesseract.image_to_string(img)
                spam_checks.append({"icon": "✅" if len(text_in_image.strip()) < 50 else "⚠️", "message": "Image is not primarily text-based." if len(text_in_image.strip()) < 50 else "Image contains significant text, increasing spam risk."})
                if any(s in text_in_image for s in ['bit.ly', 't.co']):
                    spam_checks.append({"icon": "❌", "message": "Image text contains a URL shortener, a high spam risk."})
                else:
                    spam_checks.append({"icon": "✅", "message": "No URL shorteners detected in image text."})
            except Exception:
                spam_checks.append({"icon": "⚠️", "message": "Could not perform OCR text analysis."})
        return render_template_string(HTML_ANALYSIS_RESULT, url=media_url, checks=checks, spam_checks=spam_checks, analysis=analysis, show_preview=show_preview)
    except requests.exceptions.RequestException as e:
        return render_template_string(HTML_ANALYSIS_RESULT, url=media_url, error=f"Could not connect to URL. Error: {e}")

@app.route("/webhook", methods=["POST"])
def handle_webhook():
    data = request.get_json()
    for event in data:
        message_info = event.get("message", {})
        test_id_from_tag = message_info.get("tag")
        if not test_id_from_tag: continue
        if test_id_from_tag in active_tests:
            test_info = active_tests[test_id_from_tag]
            event_type = event.get("type")
            if event_type == "message-delivered":
                start_time = test_info.get("start_time")
                if start_time:
                    test_info["latency"] = time.time() - start_time
                    test_info["status"] = "Delivered"
                if test_info.get("event"):
                    test_info.setdefault("events", {})["delivered"] = time.time()
                    test_info["event"].set()
            elif event_type == "message-failed":
                error_msg = f"Failed: {event.get('description')}"
                test_info["status"] = error_msg
                if test_info.get("event"):
                    test_info["error"] = error_msg
                    test_info["event"].set()
            elif event_type == "message-sending" and test_info.get("events") is not None:
                   test_info["events"]["sending"] = time.time()
    return "OK", 200

@app.route("/report-delivery", methods=["POST"])
def report_delivery():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400
    test_id = data.get("messageId")
    timestamp_ms = data.get("timestamp")
    if not test_id or not timestamp_ms:
        return jsonify({"error": "Missing messageId or timestamp"}), 400
    print(f"HANDSET DLR RECEIVED for test_id: {test_id}")
    if test_id in active_tests:
        test_info = active_tests[test_id]
        delivery_time_seconds = timestamp_ms / 1000.0
        test_info["status"] = "Delivered (Handset)"
        test_info.setdefault("events", {})["delivered"] = delivery_time_seconds
        start_time = test_info.get("start_time")
        if start_time:
             test_info["latency"] = delivery_time_seconds - start_time
        if test_info.get("event"):
            test_info["event"].set()
        return jsonify({"status": "success", "message": f"Test {test_id} updated."}), 200
    else:
        return jsonify({"error": f"Test ID {test_id} not found or already completed."}), 404

# --- CORE LOGIC ---
def send_message(from_number, application_id, destination_number, message_type, text_content, test_id):
    api_url = f"https://messaging.bandwidth.com/api/v2/users/{BANDWIDTH_ACCOUNT_ID}/messages"
    auth = (BANDWIDTH_API_TOKEN, BANDWIDTH_API_SECRET)
    headers = {"Content-Type": "application/json"}
    full_text_content = f"{text_content} ID: {test_id}"
    payload = {"to": [destination_number], "from": from_number, "text": full_text_content, "applicationId": application_id, "tag": test_id}
    if message_type == "mms":
        payload["media"] = [STATIC_MMS_IMAGE_URL]
    try:
        response = requests.post(api_url, auth=auth, headers=headers, json=payload, timeout=15)
        if test_id in active_tests:
            if response.status_code == 202:
                active_tests[test_id]["start_time"] = time.time()
                active_tests[test_id]["status"] = "Sent"
                if not test_id.startswith("bulk_"):
                    active_tests[test_id]["message_id"] = response.json().get("id")
                    active_tests[test_id].setdefault("events", {})["sent"] = active_tests[test_id]["start_time"]
            else:
                error_msg = f"API Error ({response.status_code})"
                active_tests[test_id]["status"] = error_msg
                if active_tests[test_id].get("event"):
                    active_tests[test_id]["error"] = error_msg
                    active_tests[test_id]["event"].set()
    except Exception as e:
        error_msg = "Request Error"
        if test_id in active_tests:
            active_tests[test_id]["status"] = error_msg
            if active_tests[test_id].get("event"):
                active_tests[test_id]["error"] = str(e)
                active_tests[test_id]["event"].set()

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    print("This script is intended to be run with a production WSGI server like Gunicorn.")
