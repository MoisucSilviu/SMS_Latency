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


# --- NAVIGATION & SHARED STYLES ---
NAV_BAR = """
<div class="nav">
    <a href="/">Latency Tester</a> | <a href="/status">Message Status Viewer</a>
</div>
"""
STYLES = """
<style>
    body { font-family: sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; border: 1px solid #ddd; border-radius: 10px; }
    label { display: block; margin-top: 15px; font-weight: bold; }
    input[type=text], textarea { width: 100%; padding: 8px; margin-top: 5px; box-sizing: border-box; }
    textarea { resize: vertical; min-height: 80px; }
    input[type=submit] { background-color: #007bff; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; margin-top: 20px; }
    .nav { margin-bottom: 20px; padding-bottom: 10px; border-bottom: 1px solid #ccc; }
    .result { font-size: 1.2em; }
    .error { color: red; background-color: #ffebeb; padding: 10px; border-radius: 5px; white-space: pre-wrap; word-wrap: break-word; }
    table { width: 100%; border-collapse: collapse; margin-top: 20px; }
    th, td { text-align: left; padding: 8px; border-bottom: 1px solid #ddd; }
    th { background-color: #f2f2f2; }
</style>
"""

# --- HTML TEMPLATES ---
HTML_LATENCY_FORM = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Advanced Messaging Tester</title>{STYLES}</head>
<body>
    {NAV_BAR}
    <h2>Advanced Messaging Latency Tester</h2>
    <form action="/run_test" method="post">
        <label for="destination_number">Destination Phone Number:</label>
        <input type="text" id="destination_number" name="destination_number" placeholder="+15551234567" required>
        <label>Message Type:</label>
        <div>
            <input type="radio" id="sms" name="message_type" value="sms" checked> SMS
            <input type="radio" id="mms" name="message_type" value="mms" style="margin-left: 20px;"> MMS
        </div>
        <label for="message_text">Text Message:</label>
        <textarea id="message_text" name="message_text" placeholder="Enter your text caption here..."></textarea>
        <input type="submit" value="Run Latency Test">
    </form>
</body>
</html>
"""

HTML_STATUS_FORM = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Message Status Viewer</title>{STYLES}</head>
<body>
    {NAV_BAR}
    <h2>Message Status & Log Viewer</h2>
    <form action="/get_status" method="post">
        <label for="message_id">Enter Message ID:</label>
        <input type="text" id="message_id" name="message_id" required>
        <input type="submit" value="Fetch Status">
    </form>
</body>
</html>
"""

HTML_RESULT = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Test Result</title>{STYLES}</head>
<body>
    {NAV_BAR}
    <h2>Test Result</h2>
    {% if error %}
        <p class="error"><strong>Error:</strong><br>{{ error }}</p>
    {% elif status == 'sent' %}
        <p class="result sent">✅ Message Sent Successfully!</p>
        <p><strong>Message ID:</strong> {{ message_id }}</p>
        <p>A 'message-delivered' report was not received within the timeout period.</p>
    {% elif message and events %}
        <h3>Message Details</h3>
        <p>
            <strong>From:</strong> {{ message.owner }}<br>
            <strong>To:</strong> {{ message.to[0] }}<br>
            <strong>Direction:</strong> {{ message.direction }}<br>
            <strong>Status:</strong> {{ message.messageStatus }}<br>
        </p>
        <h3>Event History</h3>
        <table>
            <tr><th>Time</th><th>Type</th><th>Description</th></tr>
            {% for event in events %}
            <tr><td>{{ event.time }}</td><td>{{ event.type }}</td><td>{{ event.description }}</td></tr>
            {% endfor %}
        </table>
    {% else %}
        <p class="result">✅ Message Delivered!</p>
        <p><strong>Message ID:</strong> {{ message_id }}</p>
        <p><strong>Total End-to-End Latency:</strong> {{ latency }} seconds</p>
    {% endif %}
    <br>
    <a href="/">Run another test</a> or <a href="/status">Check a message status</a>
</body>
</html>
"""

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
    # ... (this function remains the same as your advanced tester)
    destination_number = request.form["destination_number"]
    message_type = request.form["message_type"]
    text_content = request.form["message_text"]
    test_id = str(time.time())
    delivery_event = threading.Event()
    results[test_id] = {"event": delivery_event, "status": "pending"}
    args = (destination_number, message_type, text_content, test_id)
    threading.Thread(target=send_message, args=args).start()
    timeout = 60 if message_type == "mms" else 120
    delivered_in_time = delivery_event.wait(timeout=timeout)
    result_data = results.pop(test_id, {})
    if result_data.get("error"):
        return render_template_string(HTML_RESULT, error=result_data["error"])
    elif not delivered_in_time:
        if result_data.get("status") == "sent":
             return render_template_string(HTML_RESULT, status="sent", message_id=result_data.get("message_id"))
        else:
             return render_template_string(HTML_RESULT, error=f"TIMEOUT: Did not receive a 'message-delivered' webhook after {timeout} seconds.")
    else:
        return render_template_string(HTML_RESULT, status="delivered", message_id=result_data.get("message_id"), latency=f"{result_data.get('latency', 0):.2f}")

@app.route("/get_status", methods=["POST"])
@requires_auth
def get_message_status():
    # ... (this function is from the status viewer)
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
    # ... (this function remains the same)
    data = request.get_json()
    for event in data:
        if event.get("type") == "message-delivered":
            test_id_from_tag = event.get("message", {}).get("tag")
            if test_id_from_tag in results:
                if results[test_id_from_tag].get("status") == "sent":
                    results[test_id_from_tag]["latency"] = time.time() - results[test_id_from_tag]["start_time"]
                    results[test_id_from_tag]["status"] = "delivered"
                    results[test_id_from_tag]["event"].set()
    return "OK", 200

# --- CORE LOGIC ---
def send_message(destination_number, message_type, text_content, test_id):
    # ... (this function remains the same)
    api_url = f"https://messaging.bandwidth.com/api/v2/users/{BANDWIDTH_ACCOUNT_ID}/messages"
    auth = (BANDWIDTH_API_TOKEN, BANDWIDTH_API_SECRET)
    payload = {"to": [destination_number], "from": BANDWIDTH_NUMBER, "text": text_content, "applicationId": BANDWIDTH_APP_ID, "tag": test_id}
    if message_type == "mms":
        payload["media"] = ["https://i.imgur.com/example.png"]
    try:
        response = requests.post(api_url, auth=auth, json=payload, timeout=15)
        if response.status_code == 202:
            results[test_id]["start_time"] = time.time()
            results[test_id]["status"] = "sent"
            results[test_id]["message_id"] = response.json().get("id")
        else:
            results[test_id]["error"] = f"API Error (Status {response.status_code}):\n{response.text}"
            results[test_id]["event"].set()
    except Exception as e:
        results[test_id]["error"] = f"Request Error: {e}"
        results[test_id]["event"].set()


# This block is only for local development
if __name__ == "__main__":
    print("This script is intended to be run with a production WSGI server like Gunicorn.")
