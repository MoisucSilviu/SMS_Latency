import os
import re
import sys
import time
import threading
import requests
import io
from flask import Flask, request, render_template, Response, redirect, url_for, jsonify
from functools import wraps
from dotenv import load_dotenv
from datetime import datetime
from PIL import Image
import pytesseract

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

# --- CONSTANTS ---
STATUS_SENDING = "Sending..."
STATUS_SENT = "Sent"
STATUS_DELIVERED = "Delivered"
STATUS_FAILED = "Failed"
STATUS_TIMED_OUT = "Timed Out"
STATUS_API_ERROR = "API Error"
STATUS_REQUEST_ERROR = "Request Error"

# --- GLOBAL STATE & APP SETUP ---
active_tests = {}
active_tests_lock = threading.Lock() # For thread safety
app = Flask(__name__)

# --- BASIC AUTHENTICATION ---
def check_auth(username, password):
    """Checks if the provided username and password are correct."""
    return username == APP_USERNAME and password == APP_PASSWORD
def authenticate():
    """Sends a 401 Unauthorized response that prompts for login."""
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
    with active_tests_lock:
        active_tests[test_id] = {"event": delivery_event, "events": {}}
    
    args = (from_number, application_id, destination_number, message_type, text_content, test_id)
    threading.Thread(target=send_message, args=args).start()
    
    timeout = 60 if message_type == "mms" else 120
    is_complete = delivery_event.wait(timeout=timeout)
    
    with active_tests_lock:
        result_data = active_tests.pop(test_id, {})
        
    return render_template('result_page.html', result_type='dlr', result_data=result_data, is_complete=is_complete, message_type=message_type)

@app.route("/run_bulk_test", methods=["POST"])
@requires_auth
def run_bulk_test():
    """Orchestrates the bulk performance test."""
    batch_id = f"batch_{time.time()}"
    from_numbers = [{"name": "TF", "number": TF_NUMBER, "appId": TF_APP_ID}, {"name": "10DLC", "number": TEN_DLC_NUMBER, "appId": TEN_DLC_APP_ID}]
    message_types = ["sms", "mms"]
    
    with active_tests_lock:
        active_tests[batch_id] = {"start_time": time.time(), "tests": {}}

    for dest_num, carrier_name in DESTINATION_NUMBERS:
        for from_data in from_numbers:
            for msg_type in message_types:
                test_id = f"bulk_{time.time()}_{len(active_tests[batch_id]['tests'])}"
                with active_tests_lock:
                    active_tests[test_id] = {
                        "batch_id": batch_id, "from_name": from_data["name"], "from_num": from_data["number"],
                        "to_num": dest_num, "carrier_name": carrier_name or 'N/A', "type": msg_type.upper(),
                        "status": STATUS_SENDING, "latency": None
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
    with active_tests_lock:
        batch = active_tests.get(batch_id)
        if not batch:
            return jsonify({"is_complete": True, "results": {"sms": {"tf": [], "dlc": []}, "mms": {"tf": [], "dlc": []}}})

        all_tests = list(batch.get("tests", {}).values())
        is_complete = all(r['status'] not in [STATUS_SENDING, STATUS_SENT] for r in all_tests)
        
        if not is_complete and (time.time() - batch.get("start_time", 0) > 125):
            is_complete = True
            for test in all_tests:
                if test['status'] == STATUS_SENT:
                    test['status'] = STATUS_TIMED_OUT
        
        if is_complete:
            tests_to_remove = [tid for tid, t in active_tests.items() if t.get("batch_id") == batch_id]
            for tid in tests_to_remove:
                active_tests.pop(tid, None)
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
    
    with active_tests_lock:
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
    with active_tests_lock:
        result = active_tests.get(analysis_id)
        if result and result.get("status") == "complete":
            active_tests.pop(analysis_id, None) # Clean up after fetching
            return jsonify(result)
        elif not result:
            return jsonify({"status": "complete", "error": "Test not found or already complete."})
    return jsonify({"status": "running"})

@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """Handles all incoming webhooks from Bandwidth."""
    data = request.get_json()
    for event in data:
        message_info = event.get("message", {})
        test_id_from_tag = message_info.get("tag")
        if not test_id_from_tag: continue
        
        with active_tests_lock:
            if test_id_from_tag in active_tests:
                test_info = active_tests[test_id_from_tag]
                event_type = event.get("type")
                current_time = time.time()
                
                if event_type == "message-delivered":
                    start_time = test_info.get("start_time") or test_info.get("events",{}).get("sent")
                    if start_time:
                        test_info["latency"] = current_time - start_time
                        test_info["status"] = STATUS_DELIVERED
                    if test_info.get("event"): # This is a single test
                        test_info.setdefault("events", {})["delivered"] = current_time
                        test_info["event"].set()

                elif event_type == "message-failed":
                    error_msg = f"Failed: {event.get('description')}"
                    test_info["status"] = STATUS_FAILED
                    if test_info.get("event"): # This is a single test
                        test_info["error"] = error_msg
                        test_info["event"].set()

                elif event_type == "message-sending" and test_info.get("events") is not None:
                     test_info["events"]["sending"] = time.time()
    return "OK", 200

# --- CORE LOGIC ---
def send_message(from_number, application_id, destination_number, message_type, text_content, test_id):
    """Sends a single SMS or MMS message and updates the global state."""
    api_url = f"https://messaging.bandwidth.com/api/v2/users/{BANDWIDTH_ACCOUNT_ID}/messages"
    auth = (BANDWIDTH_API_TOKEN, BANDWIDTH_API_SECRET)
    headers = {"Content-Type": "application/json"}
    payload = {"to": [destination_number], "from": from_number, "text": text_content, "applicationId": application_id, "tag": test_id}
    if message_type == "mms":
        payload["media"] = [STATIC_MMS_IMAGE_URL]
    
    try:
        response = requests.post(api_url, auth=auth, headers=headers, json=payload, timeout=15)
        with active_tests_lock:
            if test_id in active_tests:
                if response.status_code == 202:
                    active_tests[test_id]["start_time"] = time.time()
                    active_tests[test_id]["status"] = STATUS_SENT
                    if not test_id.startswith("bulk_"):
                        active_tests[test_id]["message_id"] = response.json().get("id")
                        active_tests[test_id].setdefault("events", {})["sent"] = active_tests[test_id]["start_time"]
                else:
                    error_msg = f"API Error ({response.status_code}): {response.text}"
                    active_tests[test_id]["status"] = STATUS_API_ERROR
                    if active_tests[test_id].get("event"):
                        active_tests[test_id]["error"] = error_msg
                        active_tests[test_id]["event"].set()
    except requests.exceptions.RequestException as e:
        with active_tests_lock:
            if test_id in active_tests:
                active_tests[test_id]["status"] = STATUS_REQUEST_ERROR
                if active_tests[test_id].get("event"):
                    active_tests[test_id]["error"] = str(e)
                    active_tests[test_id]["event"].set()

def perform_media_analysis(analysis_id, media_url):
    """Performs media analysis in the background."""
    checks, spam_checks, analysis, show_preview = [], [], [], False
    error = None
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
                spam_checks.append({"icon": "⚠️", "message": "OCR analysis failed."})
    except requests.exceptions.RequestException as e:
        error = f"Could not connect to URL: {e}"

    with active_tests_lock:
        active_tests[analysis_id] = {
            "status": "complete", "error": error, "url": media_url,
            "checks": checks, "spam_checks": spam_checks, "analysis": analysis, "show_preview": show_preview
        }

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    # This block is for local development testing
    print("Starting application for local development...")
    # It's recommended to run with "flask --app app --debug run" instead
    app.run(debug=True, port=5001)
