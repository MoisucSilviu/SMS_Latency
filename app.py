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

# --- HTML TEMPLATES ---
HTML_FORM = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Advanced Messaging Tester</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"/>
    <style> body > main { padding: 2rem; } </style>
</head>
<body>
<main class="container">
    <article>
        <h2>Advanced Messaging Latency Tester</h2>
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
            
            <button type="submit">Run Latency Test</button>
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
        toggleMediaField(); // Run on page load
    </script>
</main>
</body>
</html>
"""

HTML_RESULT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Test Result</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"/>
    <style>
        body > main { padding: 2rem; }
        .error { background-color: var(--pico-form-element-invalid-background-color); color: var(--pico-form-element-invalid-color); padding: 1rem; border-radius: var(--pico-border-radius); }
        .sent { color: var(--pico-color-azure-600); }
    </style>
</head>
<body>
<main class="container">
    <article>
        <h2>Test Result</h2>
        {% if error %}
            <p class="error"><strong>Error:</strong><br>{{ error }}</p>
        {% elif status == 'sent' %}
            <h3 class="sent">✅ Message Sent Successfully!</h3>
            <p><strong>Message ID:</strong> {{ message_id }}</p>
            <p>A 'message-delivered' report was not received within the timeout period.</p>
        {% else %}
            <h3>✅ Message Delivered!</h3>
            <p><strong>Message ID:</strong> {{ message_id }}</p>
            <p><strong>Total End-to-End Latency:</strong> {{ latency }} seconds</p>
        {% endif %}
        <a href="/" role="button" class="secondary">Run another test</a>
    </article>
</main>
</body>
</html>
"""

# --- FLASK ROUTES ---
@app.route("/")
@requires_auth
def index():
    return render_template_string(HTML_FORM)

@app.route("/run_test", methods=["POST"])
@requires_auth
def run_latency_test():
    # ✨ GET THE MEDIA URL FROM THE FORM
    destination_number = request.form["destination_number"]
    message_type = request.form["message_type"]
    text_content = request.form["message_text"]
    media_url = request.form.get("media_url") # Use .get() for the optional field

    test_id = str(time.time())
    delivery_event = threading.Event()
    results[test_id] = {"event": delivery_event, "status": "pending"}
    
    # ✨ PASS THE MEDIA URL TO THE SENDING FUNCTION
    args = (destination_number, message_type, text_content, media_url, test_id)
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

@app.route("/webhook", methods=["POST"])
def handle_webhook():
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
# ✨ UPDATE THE FUNCTION TO USE THE PROVIDED MEDIA URL
def send_message(destination_number, message_type, text_content, media_url, test_id):
    api_url = f"https://messaging.bandwidth.com/api/v2/users/{BANDWIDTH_ACCOUNT_ID}/messages"
    auth = (BANDWIDTH_API_TOKEN, BANDWIDTH_API_SECRET)
    payload = {"to": [destination_number], "from": BANDWIDTH_NUMBER, "text": text_content, "applicationId": BANDWIDTH_APP_ID, "tag": test_id}
    
    # Use the media_url from the form if provided for MMS
    if message_type == "mms" and media_url:
        payload["media"] = [media_url]
        
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

# This block is only for local development and will not be used by Gunicorn
if __name__ == "__main__":
    print("This script is intended to be run with a production WSGI server like Gunicorn.")
