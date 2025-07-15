import os
import re
import sys
import time
import threading
import requests
import io
import base64
from flask import Flask, request, render_template, Response, redirect, url_for, jsonify
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

def parse_destinations(dest_str):
    if not dest_str: return []
    return re.findall(r'(\+\d{11})\s*(?:\(([^)]+)\))?', dest_str)
DESTINATION_NUMBERS = parse_destinations(os.getenv("DESTINATION_NUMBERS", ""))
STATIC_MMS_IMAGE_URL = "https://i.imgur.com/e3j2F0u.png"

# --- GLOBAL VARIABLES & APP SETUP ---
active_tests = {}
app = Flask(__name__, template_folder="templates")

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

# --- FLASK ROUTES ---
@app.route("/")
@requires_auth
def dashboard():
    """Renders the main dashboard with all tools."""
    return render_template('dashboard.html', numbers=DESTINATION_NUMBERS)

@app.route("/health")
def health_check():
    """A simple, unprotected health check endpoint for Render."""
    return "OK", 200

@app.route("/run_test", methods=["POST"])
@requires_auth
def run_latency_test():
    """Handles the form submission for the single DLR test."""
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
    return render_template('result_page.html', result_type='dlr', result_data=result_data, is_complete=is_complete, message_type=message_type)

@app.route("/run_bulk_test", methods=["POST"])
@requires_auth
def run_bulk_test():
    """Orchestrates the bulk performance test."""
    batch_id = f"batch_{time.time()}"
    from_numbers = [{"name": "TF", "number": TF_NUMBER, "appId": TF_APP_ID}, {"name": "10DLC", "number": TEN_DLC_NUMBER, "appId": TEN_DLC_APP_ID}]
    message_types = ["sms", "mms"]
    for dest_num, carrier_name in DESTINATION_NUMBERS:
        for from_data in from_numbers:
            for msg_type in message_types:
                test_id = f"bulk_{time.time()}_{len(active_tests)}"
                active_tests[test_id] = {
                    "batch_id": batch_id, "from_name": from_data["name"], "from_num": from_data["number"],
                    "to_num": dest_num, "carrier_name": carrier_name or 'N/A', "type": msg_type.upper(),
                    "status": "Sending...", "latency": None, "start_time": time.time()
                }
                args = (from_data["number"], from_data["appId"], dest_num, msg_type, f"{from_data['name']} {msg_type.upper()} Test", test_id)
                threading.Thread(target=send_message, args=args).start()
    return redirect(url_for('bulk_results_page', batch_id=batch_id))

@app.route("/bulk_results/<batch_id>")
@requires_auth
def bulk_results_page(batch_id):
    """Renders the interactive results page for the bulk test."""
    return render_template('result_page.html', result_type='bulk', batch_id=batch_id)

@app.route("/api/bulk_status/<batch_id>")
@requires_auth
def api_bulk_status(batch_id):
    """API endpoint for JavaScript to poll for bulk test status updates."""
    all_tests = [test for test in active_tests.values() if test.get("batch_id") == batch_id]
    
    # Check if all tests are in a final state (not "Sending..." or "Sent")
    is_complete = all(r['status'] not in ['Sending...', 'Sent'] for r in all_tests)
    
    # Also consider the test complete if it's been running for more than 2 minutes
    if not is_complete:
        # Get the start time of the first test in the batch to check for overall timeout
        batch_start_times = [t.get("start_time") for t in all_tests if t.get("start_time")]
        if batch_start_times and (time.time() - min(batch_start_times) > 125):
            is_complete = True
            for test in all_tests:
                if test['status'] == 'Sent': test['status'] = 'Timed Out'
            
    if is_complete and all_tests:
        # After a grace period, clean up tests for this completed batch
        tests_to_remove = [tid for tid, test in active_tests.items() if test.get("batch_id") == batch_id]
        for tid in tests_to_remove:
            active_tests.pop(tid, None)

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

@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """A unified webhook handler for all incoming Bandwidth events."""
    data = request.get_json()
    for event in data:
        message_info = event.get("message", {})
        test_id_from_tag = message_info.get("tag")
        
        if test_id_from_tag in active_tests:
            test_info = active_tests[test_id_from_tag]
            event_type = event.get("type")
            
            if event_type == "message-delivered":
                start_time = test_info.get("start_time")
                if start_time:
                    test_info["latency"] = time.time() - start_time
                    test_info["status"] = "Delivered"
                if test_info.get("event"): # This is a single test
                    test_info.setdefault("events", {})["delivered"] = time.time()
                    test_info["event"].set()

            elif event_type == "message-failed":
                error_msg = f"Failed: {event.get('description', 'Unknown')}"
                test_info["status"] = error_msg
                if test_info.get("event"):
                    test_info["error"] = error_msg
                    test_info["event"].set()
            
            elif event_type == "message-sending" and not test_id_from_tag.startswith("bulk_"):
                 if "events" in test_info:
                    test_info["events"]["sending"] = time.time()
    return "OK", 200

# --- CORE LOGIC ---
def send_message(from_number, application_id, destination_number, message_type, text_content, test_id):
    """Sends a single SMS or MMS message and updates the global state."""
    api_url = f"https://messaging.bandwidth.com/api/v2/users/{BANDWIDTH_ACCOUNT_ID}/messages"
    auth = (BANDWIDTH_API_TOKEN, BANDWIDTH_API_SECRET)
    payload = {"to": [destination_number], "from": from_number, "text": text_content, "applicationId": application_id, "tag": test_id}
    if message_type == "mms":
        payload["media"] = [STATIC_MMS_IMAGE_URL]
    try:
        response = requests.post(api_url, auth=auth, json=payload, timeout=15)
        if test_id in active_tests:
            if response.status_code == 202:
                active_tests[test_id]["status"] = "Sent"
                # For single tests, we need to save the message_id for the results page
                if not test_id.startswith("bulk_"):
                    active_tests[test_id]["message_id"] = response.json().get("id")
                    active_tests[test_id].setdefault("events", {})["sent"] = active_tests[test_id]["start_time"]
            else:
                error_msg = f"API Error ({response.status_code}): {response.json().get('description', 'Unknown error')}"
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
