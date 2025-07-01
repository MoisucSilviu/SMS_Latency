import os
import sys
import time
import threading
import requests
from flask import Flask, request, render_template_string, Response
from functools import wraps
from pyngrok import ngrok
from dotenv import load_dotenv  # ✨ 1. IMPORT THE LIBRARY

load_dotenv()  # ✨ 2. LOAD THE .env FILE

# --- CONFIGURATION ---
BANDWIDTH_ACCOUNT_ID = os.getenv("BANDWIDTH_ACCOUNT_ID")
BANDWIDTH_API_TOKEN = os.getenv("BANDWIDTH_API_TOKEN")
BANDWIDTH_API_SECRET = os.getenv("BANDWIDTH_API_SECRET")
BANDWIDTH_APP_ID = os.getenv("BANDWIDTH_APP_ID")
BANDWIDTH_NUMBER = os.getenv("BANDWIDTH_NUMBER")
NGROK_STATIC_DOMAIN = "mite-sure-monster.ngrok-free.app"

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
HTML_FORM = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>SMS Latency Tester</title>
    </head>
<body>
    <h2>SMS End-to-End Latency Tester</h2>
    <form action="/run_test" method="post">
        <label for="destination_number">Enter destination phone number (e.g., +15551234567):</label>
        <input type="text" id="destination_number" name="destination_number" required>
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
    </head>
<body>
    <h2>Test Result</h2>
    {% if error %}
        <p>{{ error }}</p>
    {% else %}
        <p>✅ Message Delivered!</p>
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
    destination_number = request.form["destination_number"]
    test_id = str(time.time())
    delivery_event = threading.Event()
    results[test_id] = {"event": delivery_event}
    threading.Thread(target=send_sms, args=(destination_number, test_id)).start()
    delivered_in_time = delivery_event.wait(timeout=120)
    result_data = results.pop(test_id, {})
    if not delivered_in_time and 'error' not in result_data:
        result_data["error"] = "TIMEOUT: Did not receive a 'message-delivered' webhook after 120 seconds."
    if result_data.get("error"):
        return render_template_string(HTML_RESULT, error=result_data["error"])
    else:
        return render_template_string(HTML_RESULT, message_id=result_data.get("message_id"),
                                      latency=f"{result_data.get('latency', 0):.2f}")


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
def send_sms(destination_number, test_id):
    api_url = f"https://messaging.bandwidth.com/api/v2/users/{BANDWIDTH_ACCOUNT_ID}/messages"
    auth = (BANDWIDTH_API_TOKEN, BANDWIDTH_API_SECRET)
    headers = {"Content-Type": "application/json"}
    payload = {
        "to": [destination_number],
        "from": BANDWIDTH_NUMBER,
        "text": "Latency test initiated from web.",
        "applicationId": BANDWIDTH_APP_ID,
        "tag": test_id
    }
    try:
        response = requests.post(api_url, auth=auth, headers=headers, json=payload, timeout=15)
        if response.status_code == 202:
            results[test_id]["start_time"] = time.time()
        else:
            results[test_id]["error"] = f"API Error: {response.status_code} - {response.text}"
            results[test_id]["event"].set()
    except Exception as e:
        results[test_id]["error"] = f"Request Error: {e}"
        results[test_id]["event"].set()


# --- MAIN ---
def main():
    if not all([BANDWIDTH_ACCOUNT_ID, BANDWIDTH_API_TOKEN, BANDWIDTH_API_SECRET, APP_USERNAME, APP_PASSWORD]):
        print("❌ FATAL ERROR: One or more required environment variables are missing.")
        print("Please ensure you have a .env file with all required credentials.")
        sys.exit(1)

    print(f"--- Connecting to static ngrok domain: {NGROK_STATIC_DOMAIN} ---")
    ngrok.connect(5000, domain=NGROK_STATIC_DOMAIN)
    print(f"Web interface is live at: https://{NGROK_STATIC_DOMAIN}")
    app.run(port=5000)


if __name__ == "__main__":
    main()