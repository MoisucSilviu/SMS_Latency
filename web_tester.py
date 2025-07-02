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
MEDIA_URL = os.getenv("MEDIA_URL", "https://www.google.com/images/branding/googlelogo/1x/googlelogo_color_272x92dp.png")


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

# --- HTML TEMPLATES ---
# ✨ MODIFIED to include message type selection and a text area
HTML_FORM = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Advanced Messaging Tester</title>
    <style>
        body { font-family: sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; border: 1px solid #ddd; border-radius: 10px; }
        label { display: block; margin-top: 15px; font-weight: bold; }
        input[type=text], textarea { width: 100%; padding: 8px; margin-top: 5px; box-sizing: border-box; }
        textarea { resize: vertical; min-height: 80px; }
        .radio-group { margin-top: 5px; }
        input[type=submit] { background-color: #007bff; color: white; padding: 10px 20px; border: none; border-radius: 5px; cursor: pointer; margin-top: 20px; }
    </style>
</head>
<body>
    <h2>Advanced Messaging Latency Tester</h2>
    <form action="/run_test" method="post">
        
        <label for="destination_number">Destination Phone Number:</label>
        <input type="text" id="destination_number" name="destination_number" placeholder="+15551234567" required>

        <label>Message Type:</label>
        <div class="radio-group">
            <input type="radio" id="sms" name="message_type" value="sms" checked>
            <label for="sms" style="display: inline-block; font-weight: normal;">SMS</label>
            <input type="radio" id="mms" name="message_type" value="mms" style="margin-left: 20px;">
            <label for="mms" style="display: inline-block; font-weight: normal;">MMS</label>
        </div>

        <label for="message_text">Text Message:</label>
        <textarea id="message_text" name="message_text" placeholder="Enter your message here..."></textarea>
        
        <input type="submit" value="Run Test">
    </form>
</body>
</html>
"""

HTML_RESULT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Test Result</title>
    <style>
        body { font-family: sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; border: 1px solid #ddd; border-radius: 10px; }
        .result { font-size: 1.2em; }
        .error { color: red; background-color: #ffebeb; padding: 10px; border-radius: 5px; white-space: pre-wrap; word-wrap: break-word; }
    </style>
</head>
<body>
    <h2>Test Result</h2>
    {% if error %}
        <p class="error"><strong>Error:</strong><br>{{ error }}</p>
    {% else %}
        <p class="result">✅ Message Delivered!</p>
        <p><strong>Message ID:</strong> {{ message_id }}</p>
        <p><strong>Total End-to-End Latency:</strong> {{ latency }} seconds</p>
    {% endif %}
    <a href="/">Run another test</a>
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
def run_test():
    # ✨ MODIFIED to get all form data
    destination_number = request.form["destination_number"]
    message_type = request.form["message_type"]
    text_content = request.form["message_text"]
    
    test_id = str(time.time())
    delivery_event = threading.Event()
    results[test_id] = {"event": delivery_event}
    
    # ✨ MODIFIED to pass all data to the sending function
    args = (destination_number, message_type, text_content, test_id)
    threading.Thread(target=send_message, args=args).start()
    
    delivered_in_time = delivery_event.wait(timeout=120)
    result_data = results.pop(test_id, {})
    if not delivered_in_time and 'error' not in result_data:
        result_data["error"] = "TIMEOUT: Did not receive a 'message-delivered' webhook after 120 seconds."
    if result_data.get("error"):
        return render_template_string(HTML_RESULT, error=result_data["error"])
    else:
        return render_template_string(HTML_RESULT, message_id=result_data.get("message_id"), latency=f"{result_data.get('latency', 0):.2f}")

@app.route("/webhook", methods=["POST"])
def handle_webhook():
    data = request.get_json()
    for event in data:
        if event.get("type") == "message-delivered":
            test_id_from_tag = event.get("message", {}).get("tag")
            if test_id_from_tag in results:
                start_time = results[test_id_from_tag].get("start_time")
                if start_time:
                    results[test_id_from_tag]["latency"] = time.time() - start_time
                    results[test_id_from_tag]["message_id"] = event.get("message", {}).get("id")
                    results[test_id_from_tag]["event"].set()
    return "OK", 200

# --- CORE LOGIC ---
# ✨ RENAMED and MODIFIED to handle both SMS and MMS
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
    
    # Add media URL only if the type is MMS
    if message_type == "mms":
        payload["media"] = [MEDIA_URL]

    try:
        response = requests.post(api_url, auth=auth, headers=headers, json=payload, timeout=15)
        if response.status_code == 202:
            results[test_id]["start_time"] = time.time()
        else:
            error_details = response.json()
            error_description = error_details.get('description', 'No description provided.')
            results[test_id]["error"] = f"API Error (Status {response.status_code}):\n{error_description}"
            results[test_id]["event"].set()
    except Exception as e:
        results[test_id]["error"] = f"Request Error: {e}"
        results[test_id]["event"].set()

# This block is only for local development and will not be used by Gunicorn on Render
if __name__ == "__main__":
    print("This script is intended to be run with a production WSGI server like Gunicorn.")
