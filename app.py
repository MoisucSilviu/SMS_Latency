import os
import sys
import time
import threading
import requests
from flask import Flask, request, render_template_string, Response
from functools import wraps
from dotenv import load_dotenv

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

# --- GLOBAL VARIABLES & APP SETUP ---
results = {}
app = Flask(__name__)

# --- BASIC AUTHENTICATION ---
def check_auth(username, password):
    """Checks if the provided username and password are correct."""
    return username == APP_USERNAME and password == APP_PASSWORD

def authenticate():
    """Sends a 401 Unauthorized response that prompts for login."""
    return Response(
        'Could not verify your access level for that URL.\n'
        'You have to login with proper credentials', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'})

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
        .nav { margin-bottom: 20px; padding-bottom: 10px; border-bottom: 1px solid #ccc; }
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

HTML_RESULT = HTML_HEADER + """
    <article>
        <h2>Test Result</h2>
        {% if error %}
            <p class="error"><strong>Error:</strong><br>{{ error }}</p>
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
        <a href="/" role="button" class="secondary">Run another test</a>
    </article>
""" + HTML_FOOTER

# --- FLASK ROUTES ---
@app.route("/")
@requires_auth
def index():
    return render_template_string(HTML_FORM)

@app.route("/run_test", methods=["POST"])
@requires_auth
def run_latency_test():
    destination_number = request.form["destination_number"]
    message_type = request.form["message_type"]
    text_content = request.form["message_text"]
    test_id = str(time.time())
    
    delivery_event = threading.Event()
    results[test_id] = {"event": delivery_event, "events": {}}
    
    args = (destination_number, message_type, text_content, test_id)
    threading.Thread(target=send_message, args=args).start()
    
    # Wait for the final event (delivered or failed)
    is_complete = delivery_event.wait(timeout=120)
    
    result_data = results.pop(test_id, {})
    events = result_data.get("events", {})

    if result_data.get("error"):
        return render_template_string(HTML_RESULT, error=result_data["error"])
    
    if not is_complete and not events:
         return render_template_string(HTML_RESULT, error="TIMEOUT: No webhooks were received within 120 seconds.")

    # Calculate latencies and format timestamps before rendering
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

# --- CORE LOGIC ---
def send_message(destination_number, message_type, text_content, test_id):
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
    
    if message_type == "mms":
        payload["media"] = ["https://i.imgur.com/e3j2F0u.png"]

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
