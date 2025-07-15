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
    """Renders the main dashboard with all tools."""
    return render_template('dashboard.html', numbers=DESTINATION_NUMBERS)

@app.route("/phone")
@requires_auth
def phone_simulator_page():
    """Renders the phone simulator page."""
    return render_template('phone_simulator.html')

@app.route("/get-messages")
@requires_auth
def get_messages():
    """Provides the list of stored messages for the phone simulator."""
    return jsonify(phone_simulator_messages)
    
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
    return render_template('result_page.html', result_type='dlr', message_id=result_data.get("message_id"), events=events)

@app.route("/run_bulk_test", methods=["POST"])
@requires_auth
def run_bulk_test():
    """Orchestrates the bulk performance test."""
    batch_id = f"batch_{time.time()}"
    active_tests[batch_id] = {"start_time": time.time(), "tests": {}}
    from_numbers = [{"name": "TF", "number": TF_NUMBER, "appId": TF_APP_ID}, {"name": "10DLC", "number": TEN_DLC_NUMBER, "appId": TEN_DLC_APP_ID}]
    message_types = ["sms", "mms"]
    for dest_num, carrier_name in DESTINATION_NUMBERS:
        for from_data in from_numbers:
            for msg_type in message_types:
                test_id = f"bulk_{time.time()}_{len(active_tests[batch_id]['tests'])}"
                active_tests[test_id] = {
                    "batch_id": batch_id, "from_name": from_data["name"], "from_num": from_data["number"],
                    "to_num": dest_num, "carrier_name": carrier_name or 'N/A', "type": msg_type.upper(),
                    "status": "Sending...", "latency": None
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
    """Handles the MMS Media Analysis form submission."""
    media_url = request.form["media_url"]
    analysis_id = f"analysis_{time.time()}"
    active_tests[analysis_id] = {"status": "running"}
    threading.Thread(target=perform_media_analysis, args=(analysis_id, media_url)).start()
    return redirect(url_for('analysis_results_page', analysis_id=analysis_id))

@app.route("/analysis_results/<analysis_id>")
@requires_auth
def analysis_results_page(analysis_id):
    """Renders the interactive results page for the MMS Analysis tool."""
    return render_template('result_page.html', result_type='analysis', analysis_id=analysis_id)

@app.route("/api/analysis_status/<analysis_id>")
@requires_auth
def api_analysis_status(analysis_id):
    """API endpoint for JavaScript to poll for analysis status."""
    result = active_tests.get(analysis_id)
    if result and result.get("status") == "complete":
        active_tests.pop(analysis_id, None)
        return jsonify(result)
    elif not result:
        return jsonify({"status": "complete", "error": "Test not found or already complete."})
    return jsonify({"status": "running"})

@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """A unified webhook handler for all incoming Bandwidth events."""
    data = request.get_json()
    for event in data:
        event_type = event.get("type")
        if event_type == "message-received":
            process_phone_simulator_webhook(event)
        else: # DLRs for our testing tools
            process_dlr_webhook(event)
    return "OK", 200

# --- CORE LOGIC ---
def process_phone_simulator_webhook(event):
    """Processes incoming messages for the phone simulator."""
    message_details = event['message']
    image_content_base64 = None
    if message_details.get('media') and BANDWIDTH_API_TOKEN and BANDWIDTH_API_SECRET:
        try:
            response = requests.get(message_details['media'][0], auth=(BANDWIDTH_API_TOKEN, BANDWIDTH_API_SECRET))
            if response.status_code == 200:
                image_content_base64 = base64.b64encode(response.content).decode('utf-8')
        except Exception as e:
            print(f"Error downloading media for simulator: {e}")
    message = {'from': message_details['from'], 'text': message_details.get('text', ''), 'media_content': image_content_base64}
    phone_simulator_messages.append(message)
    if len(phone_simulator_messages) > 20:
        phone_simulator_messages.pop(0)

def process_dlr_webhook(event):
    """Processes DLR events for the testing tools."""
    message_info = event.get("message", {})
    test_id_from_tag = message_info.get("tag")
    if not test_id_from_tag: return
    
    with app.app_context(): # Ensure we have context for thread-safe operations
        if test_id_from_tag in active_tests:
            test_info = active_tests[test_id_from_tag]
            event_type = event.get("type")
            if event_type == "message-delivered":
                start_time = test_info.get("start_time") or test_info.get("events",{}).get("sent")
                if start_time:
                    test_info["latency"] = time.time() - start_time
                    test_info["status"] = "Delivered"
                if test_info.get("event"): # This is a single test
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

def send_message(from_number, application_id, destination_number, message_type, text_content, test_id):
    """Sends a single SMS or MMS message and updates the global state."""
    api_url = f"https://messaging.bandwidth.com/api/v2/users/{BANDWIDTH_ACCOUNT_ID}/messages"
    auth = (BANDWIDTH_API_TOKEN, BANDWIDTH_API_SECRET)
    payload = {"to": [destination_number], "from": from_number, "text": text_content, "applicationId": application_id, "tag": test_id}
    if message_type == "mms":
        payload["media"] = [STATIC_MMS_IMAGE_URL]
    try:
        response = requests.post(api_url, auth=auth, json=payload, timeout=15)
        response_data = response.json()
        with app.app_context():
            if test_id in active_tests:
                if response.status_code == 202:
                    active_tests[test_id]["start_time"] = time.time()
                    active_tests[test_id]["status"] = "Sent"
                    if not test_id.startswith("bulk_"):
                        active_tests[test_id]["message_id"] = response_data.get("id")
                        active_tests[test_id].setdefault("events", {})["sent"] = active_tests[test_id]["start_time"]
                else:
                    error_msg = f"API Error ({response.status_code}): {response_data.get('description', 'Unknown error')}"
                    active_tests[test_id]["status"] = error_msg
                    if active_tests[test_id].get("event"):
                        active_tests[test_id]["error"] = error_msg
                        active_tests[test_id]["event"].set()
    except Exception as e:
        with app.app_context():
            if test_id in active_tests:
                active_tests[test_id]["status"] = "Request Error"
                if active_tests[test_id].get("event"):
                    active_tests[test_id]["error"] = str(e)
                    active_tests[test_id]["event"].set()

def perform_media_analysis(analysis_id, media_url):
    """Performs media analysis in the background."""
    checks, spam_checks, analysis, show_preview = [], [], [], False; error = None
    try:
        response = requests.get(media_url, allow_redirects=True, timeout=10)
        response.raise_for_status()
        checks.append({"icon": "✅", "message": f"URL is accessible."})
        content_type = response.headers.get('Content-Type', 'N/A')
        if any(t in content_type for t in ['image/jpeg', 'image/png', 'image/gif']):
            checks.append({"icon": "✅", "message": f"Content-Type '{content_type}' is supported."}); show_preview = True
        else:
            checks.append({"icon": "⚠️", "message": f"Warning: Content-Type '{content_type}' may not be supported."})
        size_in_kb = len(response.content) / 1024
        checks.append({"icon": "✅", "message": f"File size is {size_in_kb:.0f} KB."})
        for carrier, limit in CARRIER_LIMITS.items():
            status, note = ("OK", f"Within ~{limit}KB limit.") if size_in_kb <= limit else ("REJECT", f"Exceeds ~{limit}KB limit.")
            analysis.append({"name": carrier, "status": status, "note": note})
        if show_preview:
            img = Image.open(io.BytesIO(response.content)); width, height = img.size
            aspect_ratio = height / width if width > 0 else 0
            spam_checks.append({"icon": "✅" if aspect_ratio <= 3 else "⚠️", "message": "Standard aspect ratio." if aspect_ratio <= 3 else "Image is very tall/thin."})
            try:
                text_in_image = pytesseract.image_to_string(img)
                spam_checks.append({"icon": "✅" if len(text_in_image.strip()) < 50 else "⚠️", "message": "Low text density." if len(text_in_image.strip()) < 50 else "High text density."})
                if any(s in text_in_image for s in ['bit.ly', 't.co']):
                    spam_checks.append({"icon": "❌", "message": "Contains URL shortener."})
            except Exception:
                spam_checks.append({"icon": "⚠️", "message": "Could not perform OCR analysis."})
    except requests.exceptions.RequestException as e:
        error = f"Could not connect to URL: {e}"
    
    with app.app_context():
        active_tests[analysis_id] = {"status": "complete", "error": error, "url": media_url, "checks": checks, "spam_checks": spam_checks, "analysis": analysis, "show_preview": show_preview}

# This block is for local development
if __name__ == "__main__":
    print("This script is intended to be run with a production WSGI server like Gunicorn.")
