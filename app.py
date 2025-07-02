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
BANDWIDTH_APP_ID = os.getenv("BANDWIDTH_APP_ID")
BANDWIDTH_NUMBER = os.getenv("BANDWIDTH_NUMBER")

# BASIC AUTH CREDENTIALS
APP_USERNAME = os.getenv("APP_USERNAME", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "password")

# SUMO LOGIC CONFIGURATION
SUMO_ACCESS_ID = os.getenv("SUMO_ACCESS_ID")
SUMO_ACCESS_KEY = os.getenv("SUMO_ACCESS_KEY")
SUMO_API_ENDPOINT = os.getenv("SUMO_API_ENDPOINT")

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
        .error { background-color: var(--pico-form-element-invalid-background-color); color: var(--pico-form-element-invalid-color); padding: 1rem; border-radius: var(--pico-border-radius); white-space: pre-wrap; word-wrap: break-word; }
        .timeline { list-style-type: none; padding-left: 0; }
        .timeline li { padding-left: 2rem; border-left: 3px solid var(--pico-primary); position: relative; padding-bottom: 1.5rem; margin-left: 1rem; }
        .timeline li::before { content: '✓'; position: absolute; left: -12px; top: 0; background: var(--pico-primary); color: white; width: 24px; height: 24px; border-radius: 50%; text-align: center; line-height: 24px; }
        .sent { color: var(--pico-color-azure-600); }
        .nav { margin-bottom: 20px; padding-bottom: 10px; border-bottom: 1px solid #ccc; }
        pre { background-color: #f5f5f5; padding: 1rem; border-radius: var(--pico-border-radius); white-space: pre-wrap; word-wrap: break-word; }
    </style>
</head>
<body>
<main class="container">
    <nav class="nav">
        <ul>
            <li><strong>Bandwidth Tools</strong></li>
        </ul>
        <ul>
            <li><a href="/">Latency Tester</a></li>
            <li><a href="/status">Message Status</a></li>
        </ul>
    </nav>
"""

HTML_FOOTER = """
</main>
</body>
</html>
"""

HTML_LATENCY_FORM = HTML_HEADER + """
    <article>
        <h2 id="latency">Advanced Messaging DLR Tester</h2>
        <form action="/run_test" method="post">
            <label for="destination_number">Destination Phone Number</label>
            <input type="text" id="destination_number" name="destination_number" placeholder="+15551234567" required>

            <fieldset>
                <legend>Message Type</legend>
                <label for="sms"><input type="radio" id="sms" name="message_type" value="sms" onchange="toggleMediaField()" checked> SMS</label>
                <label for="mms"><input type="radio" id="mms" name="message_type" value="mms" onchange="toggleMediaField()"> MMS</label>
            </fieldset>
            
            <div id="media_url_field" style="display:none;">
                <label for="media_url">Media URL (for MMS only)</label>
                <input type="text" id="media_url" name="media_url" placeholder="https://.../image.png">
            </div>

            <label for="message_text">Text Message</label>
            <textarea id="message_text" name="message_text" placeholder="Enter your text caption here..."></textarea>
            
            <button type="submit">Run DLR Test</button>
        </form>
    </article>
    <script>
        function toggleMediaField() {
            var mediaField = document.getElementById('media_url_field');
            if (document.getElementById('mms').checked) {
                mediaField.style.display = 'block';
            } else {
                mediaField.style.display = 'none';
            }
        }
        toggleMediaField();
    </script>
""" + HTML_FOOTER

HTML_STATUS_FORM = HTML_HEADER + """
    <article>
        <h2 id="status">Message Status & Log Viewer</h2>
        <form action="/get_status" method="post">
            <label for="message_id">Message ID</label>
            <input type="text" id="message_id" name="message_id" required>
            <button type="submit">Fetch Status</button>
        </form>
    </article>
""" + HTML_FOOTER

HTML_RESULT = HTML_HEADER + """
    <article>
        <h2>Result</h2>
        {% if error %}
            <p class="error"><strong>Error:</strong><br>{{ error }}</p>
        {% elif status == 'sent' %}
            <h3 class="sent">✅ MMS Sent Successfully!</h3>
            <p><strong>Message ID:</strong> {{ message_id }}</p>
            <p>A 'message-delivered' report was not received within the 60-second timeout.</p>
            <a href="/search_sumo/{{ message_id }}" role="button">Search for this ID in Sumo Logic</a>
        {% elif message and events %}
            <h3>Message Details</h3>
            <figure><table><tbody>
                <tr><td><strong>From</strong></td><td>{{ message.owner }}</td></tr>
                <tr><td><strong>To</strong></td><td>{{ message.to[0] }}</td></tr>
                <tr><td><strong>Direction</strong></td><td>{{ message.direction }}</td></tr>
                <tr><td><strong>Status</strong></td><td>{{ message.messageStatus }}</td></tr>
            </tbody></table></figure>
            <h3>Event History</h3>
            <figure><table>
                <thead><tr><th>Time</th><th>Type</th><th>Description</th></tr></thead>
                <tbody>
                {% for event in events %}
                    <tr><td>{{ event.time }}</td><td>{{ event.type }}</td><td>{{ event.description }}</td></tr>
                {% endfor %}
                </tbody>
            </table></figure>
        {% else %}
            <h3>DLR Timeline</h3>
            <ul class="timeline">
                <li>
                    <strong>Message Sent to API</strong><br>
                    Timestamp: {{ events.get('sent_str', 'N/A') }}
                </li>
                {% if events.sending %}
                <li>
                    <strong>Sent to Carrier</strong> (Leg 1 Latency: {{ "%.2f"|format(events.sending_latency) }}s)<br>
                    Timestamp: {{ events.get('sending_str', 'N/A') }}
                </li>
                {% endif %}
                {% if events.delivered %}
                <li>
                    <strong>Delivered to Handset</strong> (Leg 2 Latency: {{ "%.2f"|format(events.delivered_latency) }}s)<br>
                    Timestamp: {{ events.get('delivered_str', 'N/A') }}
                </li>
                {% endif %}
            </ul>
            <hr>
            <h4>Total End-to-End Latency: {{ "%.2f"|format(events.total_latency) }} seconds</h4>
            <p><strong>Message ID:</strong> {{ message_id }}</p>
        {% endif %}
        <br>
        <a href="/" role="button" class="secondary">Run another Latency Test</a>
        <a href="/status" role="button" class="secondary">Check a Message Status</a>
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
def latency_tester_page():
    return render_template_string(HTML_LATENCY_FORM)

@app.route("/status")
@requires_auth
def status_viewer_page():
    return render_template_string(HTML_STATUS_FORM)

@app.route("/run_test", methods=["POST"])
@requires_auth
def run_latency_test():
    destination_number = request.form["destination_number"]
    message_type = request.form["message_type"]
    text_content = request.form["message_text"]
    media_url = request.form.get("media_url")
    test_id = str(time.time())
    
    delivery_event = threading.Event()
    results[test_id] = {"event": delivery_event, "events": {}}
    
    args = (destination_number, message_type, text_content, media_url, test_id)
    threading.Thread(target=send_message, args=args).start()
    
    timeout = 60 if message_type == "mms" else 120
    is_complete = delivery_event.wait(timeout=timeout)
    
    result_data = results.pop(test_id, {})
    events = result_data.get("events", {})

    if result_data.get("error"):
        return render_template_string(HTML_RESULT, error=result_data["error"])
    
    if not is_complete and message_type == "mms" and events.get("sent"):
        return render_template_string(HTML_RESULT, status="sent", message_id=result_data.get("message_id"))

    if not is_complete:
        return render_template_string(HTML_RESULT, error=f"TIMEOUT: No final webhook was received after {timeout} seconds.")

    # Calculate latencies and format timestamps for the full DLR timeline
    events["total_latency"] = 0
    if events.get("sent"):
        events["sent_str"] = datetime.fromtimestamp(events["sent"]).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    if events.get("sending"):
        events["sending_str"] = datetime.fromtimestamp(events["sending"]).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        events["sending_latency"] = events["sending"] - events.get("sent", events["sending"])
    if events.get("delivered"):
        events["delivered_str"] = datetime.fromtimestamp(events["delivered"]).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        events["delivered_latency"] = events["delivered"] - events.get("sending", events.get("sent", events["delivered"]))
        events["total_latency"] = events["delivered"] - events.get("sent", events["delivered"])
    
    return render_template_string(HTML_RESULT, message_id=result_data.get("message_id"), events=events)

@app.route("/get_status", methods=["POST"])
@requires_auth
def get_message_status():
    message_id = request.form["message_id"]
    api_url = f"https://messaging.bandwidth.com/api/v2/users/{BANDWIDTH_ACCOUNT_ID}/messages/{message_id}"
    auth = (BANDWIDTH_API_TOKEN, BANDWIDTH_API_SECRET)
    try:
        response = requests.get(api_url, auth=auth, timeout=15)
        if response.status_code == 200:
            message_details = response.json()
            events_url = f"{api_url}/events"
            events_response = requests.get(events_url, auth=auth, timeout=15)
            events = events_response.json() if events_response.status_code == 200 else []
            return render_template_string(HTML_RESULT, message=message_details, events=events)
        else:
            error_message = f"API Error (Status {response.status_code}):\n{response.text}"
            return render_template_string(HTML_RESULT, error=error_message)
    except requests.exceptions.RequestException as e:
        return render_template_string(HTML_RESULT, error=f"Request Error: {e}")

@app.route("/webhook", methods=["POST"])
def handle_webhook():
    data = request.get_json()
    for event in data:
        event_type = event.get("type")
        message_info = event.get("message", {})
        test_id_from_tag = message_info.get("tag")

        if test_id_from_tag in results:
            current_time = time.time()
            if event_type == "message-sending":
                results[test_id_from_tag]["events"]["sending"] = current_time
            elif event_type == "message-delivered":
                results[test_id_from_tag]["events"]["delivered"] = current_time
                results[test_id_from_tag]["event"].set()
            elif event_type == "message-failed":
                results[test_id_from_tag]["error"] = f"Message Failed: {event.get('description')}"
                results[test_id_from_tag]["event"].set()
    return "OK", 200

@app.route("/search_sumo/<message_id>")
@requires_auth
def search_sumo(message_id):
    if not all([SUMO_ACCESS_ID, SUMO_ACCESS_KEY, SUMO_API_ENDPOINT]):
        return render_template_string(HTML_SUMO_RESULT, message_id=message_id, error="Sumo Logic API credentials are not configured.")

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
        error_text = e.response.text if e.response else str(e)
        return render_template_string(HTML_SUMO_RESULT, message_id=message_id, error=f"Sumo Logic API Error: {error_text}")

# --- CORE LOGIC ---
def send_message(destination_number, message_type, text_content, media_url, test_id):
    api_url = f"https://messaging.bandwidth.com/api/v2/users/{BANDWIDTH_ACCOUNT_ID}/messages"
    auth = (BANDWIDTH_API_TOKEN, BANDWIDTH_API_SECRET)
    headers = {"Content-Type": "application/json"}
    
    payload = {
        "to": [destination_number],
        "from": BANDWIDTH_NUMBER,
        "text": text_content,
        "applicationId": BANDWIDTH_APP_ID,
        "tag": test_id
    }
    
    if message_type == "mms" and media_url:
        payload["media"] = [media_url]

    try:
        response = requests.post(api_url, auth=auth, headers=headers, json=payload, timeout=15)
        if response.status_code == 202:
            results[test_id]["events"]["sent"] = time.time()
            results[test_id]["message_id"] = response.json().get("id")
        else:
            results[test_id]["error"] = f"API Error (Status {response.status_code}):\n{response.text}"
            results[test_id]["event"].set()
    except Exception as e:
        results[test_id]["error"] = f"Request Error: {e}"
        results[test_id]["event"].set()

# This block is for local development and will not be used by Gunicorn
if __name__ == "__main__":
    print("This script is intended to be run with a production WSGI server like Gunicorn.")
