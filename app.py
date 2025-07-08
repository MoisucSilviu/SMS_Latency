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

# Load environment variables
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

# --- GLOBAL VARIABLES & APP SETUP ---
# ✨ FIX: Using a single, flat dictionary for all active tests is simpler and more robust.
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

# --- HTML TEMPLATES & STYLES (Unchanged) ---
# ... All HTML template strings are the same as the previous version ...

# --- FLASK ROUTES ---
@app.route("/")
@requires_auth
def index():
    # ... (function is unchanged) ...
    pass

@app.route("/bulk")
@requires_auth
def bulk_tester_page():
    # ... (function is unchanged) ...
    pass

# ... (other non-essential routes can be here)

@app.route("/run_test", methods=["POST"])
@requires_auth
def run_latency_test():
    # ... (This logic now uses the new `active_tests` dictionary)
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
    # ... (rest of the function is the same)
    pass

@app.route("/run_bulk_test", methods=["POST"])
@requires_auth
def run_bulk_test():
    batch_id = f"batch_{time.time()}"
    from_numbers = [{"name": "TF", "number": TF_NUMBER, "appId": TF_APP_ID}, {"name": "10DLC", "number": TEN_DLC_NUMBER, "appId": TEN_DLC_APP_ID}]
    message_types = ["sms", "mms"]
    
    for dest_num, carrier_name in DESTINATION_NUMBERS:
        for from_data in from_numbers:
            for msg_type in message_types:
                test_id = f"bulk_{time.time()}_{len(active_tests)}"
                active_tests[test_id] = {
                    "batch_id": batch_id,
                    "from_name": from_data["name"], "from_num": from_data["number"],
                    "to_num": dest_num, "carrier_name": carrier_name or 'N/A',
                    "type": msg_type.upper(), "status": "Sending...", "latency": None
                }
                args = (from_data["number"], from_data["appId"], dest_num, msg_type, f"{from_data['name']} {msg_type.upper()} Test", test_id)
                threading.Thread(target=send_message, args=args).start()
                
    return redirect(url_for('bulk_results_page', batch_id=batch_id))

@app.route("/bulk_results/<batch_id>")
@requires_auth
def bulk_results_page(batch_id):
    # ... (function is unchanged)
    pass

@app.route("/api/bulk_status/<batch_id>")
@requires_auth
def api_bulk_status(batch_id):
    all_tests = [test for test in active_tests.values() if test.get("batch_id") == batch_id]
    is_complete = all(r['status'] not in ['Sending...', 'Sent'] for r in all_tests)

    # If the run is complete, clean up the old tests after a short grace period
    if is_complete:
        tests_to_remove = [tid for tid, test in active_tests.items() if test.get("batch_id") == batch_id]
        for tid in tests_to_remove:
            active_tests.pop(tid, None)
            
    # ... (rest of the sorting and JSON response logic is the same)
    pass

# ✨ FIX: The webhook handler is now much simpler and more reliable
@app.route("/webhook", methods=["POST"])
def handle_webhook():
    data = request.get_json()
    for event in data:
        message_info = event.get("message", {})
        test_id_from_tag = message_info.get("tag")

        # Directly check if the test is one we are currently tracking
        if test_id_from_tag in active_tests:
            event_type = event.get("type")
            test_info = active_tests[test_id_from_tag]

            if event_type == "message-delivered":
                start_time = test_info.get("start_time")
                if start_time:
                    test_info["latency"] = time.time() - start_time
                    test_info["status"] = "Delivered"
                # If it's a single test, signal completion
                if test_info.get("event"):
                    test_info["event"].set()

            elif event_type == "message-failed":
                test_info["status"] = f"Failed: {event.get('description')}"
                if test_info.get("event"):
                    test_info["event"].set()
                    
            elif event_type == "message-sending" and test_info.get("events") is not None:
                 test_info["events"]["sending"] = time.time()
                 
    return "OK", 200

# --- CORE LOGIC ---
# ✨ FIX: The send_message function is also simplified
def send_message(from_number, application_id, destination_number, message_type, text_content, test_id):
    api_url = f"https://messaging.bandwidth.com/api/v2/users/{BANDWIDTH_ACCOUNT_ID}/messages"
    auth = (BANDWIDTH_API_TOKEN, BANDWIDTH_API_SECRET)
    headers = {"Content-Type": "application/json"}
    payload = {"to": [destination_number], "from": from_number, "text": text_content, "applicationId": application_id, "tag": test_id}
    
    if message_type == "mms":
        payload["media"] = [STATIC_MMS_IMAGE_URL]

    try:
        response = requests.post(api_url, auth=auth, headers=headers, json=payload, timeout=15)
        if test_id in active_tests: # Check if test hasn't been cancelled/timed out
            if response.status_code == 202:
                active_tests[test_id]["start_time"] = time.time()
                active_tests[test_id]["status"] = "Sent"
                # For single tests, also save the message ID
                if not test_id.startswith("bulk_"):
                    active_tests[test_id]["message_id"] = response.json().get("id")
            else:
                error_msg = f"API Error ({response.status_code})"
                active_tests[test_id]["status"] = error_msg
                if active_tests[test_id].get("event"):
                    active_tests[test_id]["error"] = error_msg
                    active_tests[test_id]["event"].set()
    except Exception as e:
        error_msg = f"Request Error"
        if test_id in active_tests:
            active_tests[test_id]["status"] = error_msg
            if active_tests[test_id].get("event"):
                active_tests[test_id]["error"] = str(e)
                active_tests[test_id]["event"].set()

# This block is for local development
if __name__ == "__main__":
    print("This script is intended to be run with a production WSGI server like Gunicorn.")
