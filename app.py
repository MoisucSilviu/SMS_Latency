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

# --- BASIC AUTHENTICATION (THIS SECTION WAS MISSING) ---
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
# (Your large HTML string variables go here, I've omitted them for brevity)
HTML_HEADER = """..."""
HTML_NAVIGATION = """..."""
HTML_FOOTER = """..."""
HTML_DASHBOARD = """..."""
HTML_DLR_RESULT = """..."""
HTML_BULK_RESULTS_PAGE = """..."""
HTML_ANALYSIS_RESULT = """..."""

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

# ... (rest of your Flask routes like /run_bulk_test, /run_analysis, etc. go here) ...
# ...
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
