import os
import re
import sys
import time
import requests
import io
import base64
from flask import Flask, request, render_template, Response, redirect, url_for, jsonify
from functools import wraps
from dotenv import load_dotenv
from datetime import datetime
from PIL import Image
import pytesseract

# ✨ FIX: Import gevent tools instead of standard threading
from gevent.event import Event
from gevent import spawn

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

def parse_destinations(dest_str):
    if not dest_str: return []
    return re.findall(r'(\+\d{11})\s*(?:\(([^)]+)\))?', dest_str)
DESTINATION_NUMBERS = parse_destinations(os.getenv("DESTINATION_NUMBERS", ""))
STATIC_MMS_IMAGE_URL = "https://i.imgur.com/e3j2F0u.png"
CARRIER_LIMITS = {"AT&T": 1000, "T-Mobile": 1000, "Verizon": 1200, "Toll-Free": 525}

# --- GLOBAL VARIABLES & APP SETUP ---
active_tests = {}
phone_simulator_messages = []
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
    return render_template('dashboard.html', numbers=DESTINATION_NUMBERS)

@app.route("/phone")
@requires_auth
def phone_simulator_page():
    return render_template('phone_simulator.html')

@app.route('/get-messages')
@requires_auth
def get_messages():
    return jsonify(phone_simulator_messages)

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
    
    # ✨ FIX: Use gevent's Event and spawn
    delivery_event = Event()
    active_tests[test_id] = {"event": delivery_event, "events": {}}
    
    args = (from_number, application_id, destination_number, message_type, text_content, test_id)
    spawn(send_message, *args)
    
    timeout = 60 if message_type == "mms" else 120
    is_complete = delivery_event.wait(timeout=timeout)
    
    result_data = active_tests.pop(test_id, {})
    events = result_data.get("events", {})

    if result_data.get("error"):
        return render_template('result_page.html', result_type='dlr', error=result_data["error"])
    if not is_complete and message_type == "mms" and events.get("sent"):
        return render_template('result_page.html', result_type='dlr', status="sent", message_id=result_data.get("message_id"))
    if not is_complete:
        return render_template('result_page.html', result_type='dlr', error=f"TIMEOUT: No final webhook was received after {timeout} seconds.")

    events["total_latency"] = 0
    if events.get("sent"): events["sent_str"] = datetime.fromtimestamp(events["sent"]).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    if events.get("sending"):
        events["sending_str"] = datetime.fromtimestamp(events["sending"]).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        events["sending_latency"] = events["sending"] - events.get("sent", 0)
    if events.get("delivered"):
        events["delivered_str"] = datetime.fromtimestamp(events["delivered"]).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        events["delivered_latency"] = events["delivered"] - events.get("sending", events.get("sent", 0))
        events["total_latency"] = events["delivered"] - events.get("sent", 0)
        
    # ✨ FIX: Pass the 'events' dictionary to the template
    return render_template('result_page.html', result_type='dlr', message_id=result_data.get("message_id"), events=events)

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
                test_id = f"bulk_{time.time()}_{len(active_tests)}"
                active_tests[test_id] = {
                    "batch_id": batch_id, "from_name": from_data["name"], "from_num": from_data["number"],
                    "to_num": dest_num, "carrier_name": carrier_name or 'N/A', "type": msg_type.upper(),
                    "status": "Sending...", "latency": None
                }
                args = (from_data["number"], from_data["appId"], dest_num, msg_type, f"{from_data['name']} {msg_type.upper()} Test", test_id)
                spawn(send_message, *args)
    return redirect(url_for('bulk_results_page', batch_id=batch_id))

@app.route("/bulk_results/<batch_id>")
@requires_auth
def bulk_results_page(batch_id):
    return render_template('result_page.html', result_type='bulk', batch_id=batch_id)

@app.route("/api/bulk_status/<batch_id>")
@requires_auth
def api_bulk_status(batch_id):
    all_tests = [test for test in active_tests.values() if test.get("batch_id") == batch_id]
    is_complete = all(r['status'] not in ['Sending...', 'Sent'] for r in all_tests)
    batch_start_time = active_tests.get(batch_id, {}).get("start_time", 0)
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
    analysis_id = f"analysis_{time.time()}"
    active_tests[analysis_id] = {"status": "running"}
    spawn(perform_media_analysis, analysis_id, media_url)
    return redirect(url_for('analysis_results_page', analysis_id=analysis_id))

@app.route("/analysis_results/<analysis_id>")
@requires_auth
def analysis_results_page(analysis_id):
    return render_template('result_page.html', result_type='analysis', analysis_id=analysis_id)

@app.route("/api/analysis_status/<analysis_id>")
@requires_auth
def api_analysis_status(analysis_id):
    result = active_tests.get(analysis_id)
    if result and result.get("status") == "complete":
        active_tests.pop(analysis_id, None)
        return jsonify(result)
    elif not result:
        return jsonify({"status": "complete", "error": "Test not found or already complete."})
    return jsonify({"status": "running"})

@app.route("/webhook", methods=["POST"])
def handle_webhook():
    # ... (function logic is unchanged)
    pass

# --- CORE LOGIC ---
def process_phone_simulator_webhook(event):
    # ... (function logic is unchanged)
    pass
def process_dlr_webhook(event):
    # ... (function logic is unchanged)
    pass
def send_message(from_number, application_id, destination_number, message_type, text_content, test_id):
    # ... (function logic is unchanged)
    pass
def perform_media_analysis(analysis_id, media_url):
    # ... (function logic is unchanged)
    pass

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    print("This script is intended to be run with a production WSGI server like Gunicorn.")
