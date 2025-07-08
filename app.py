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

# --- GLOBAL & APP SETUP ---
active_tests = {}
app = Flask(__name__)

# --- AUTHENTICATION ---
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

# --- HTML & STYLES ---
HTML_HEADER = """
<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Bandwidth Support Dashboard</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"/>
    <style>
        body > main { padding: 2rem; max-width: 1200px; }
        .error { background-color: var(--pico-form-element-invalid-background-color); color: var(--pico-form-element-invalid-color); padding: 1rem; border-radius: var(--pico-border-radius); }
        .highlight { background-color: var(--pico-color-green-100); }
        .timeline { list-style-type: none; padding-left: 0; }
        .timeline li { padding-left: 2rem; border-left: 3px solid var(--pico-primary); position: relative; padding-bottom: 1.5rem; margin-left: 1rem; }
        .timeline li::before { content: '✓'; position: absolute; left: -12px; top: 0; background: var(--pico-primary); color: white; width: 24px; height: 24px; border-radius: 50%; text-align: center; line-height: 24px; }
        .sent { color: var(--pico-color-azure-600); }
        .loader { border: 4px solid #f3f3f3; border-top: 4px solid #3498db; border-radius: 50%; width: 30px; height: 30px; animation: spin 1s linear infinite; margin: 20px auto; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        pre { background-color: #f5f5f5; padding: 1rem; border-radius: var(--pico-border-radius); white-space: pre-wrap; word-wrap: break-word; }
        .grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); grid-gap: 2rem; }
        section[role="tabpanel"] { display: none; padding: 1.5rem 0; border-top: 1px solid var(--pico-muted-border-color);}
        section[role="tabpanel"][aria-hidden="false"] { display: block; }
        [role="tab"] { font-weight: bold; }
        [role="tab"][aria-selected="true"] { background-color: var(--pico-primary-background); }
    </style>
</head>
<body>
<main class="container">
    <hgroup><h1>Bandwidth Support Dashboard</h1><p>A unified interface for messaging and analysis tools.</p></hgroup>
    <div role="tablist" class="grid">
        <button role="tab" aria-selected="true" data-target="dlr-tester">DLR Tester</button>
        <button role="tab" data-target="bulk-tester">Bulk Tester</button>
        <button role="tab" data-target="mms-analyzer">MMS Analysis</button>
    </div>
    <section role="tabpanel" id="dlr-tester">
        <article><h2>Advanced Messaging DLR Tester</h2><form action="/run_test" method="post"><fieldset><legend>From Number Type</legend><label for="tfn"><input type="radio" id="tfn" name="from_number_type" value="tf" checked> Toll-Free</label><label for="10dlc"><input type="radio" id="10dlc" name="from_number_type" value="10dlc"> 10DLC</label></fieldset><label for="destination_number">Destination Phone Number</label><input type="text" id="destination_number" name="destination_number" placeholder="+15551234567" required><fieldset><legend>Message Type</legend><label for="sms"><input type="radio" id="sms" name="message_type" value="sms" checked> SMS</label><label for="mms"><input type="radio" id="mms" name="message_type" value="mms"> MMS</label></fieldset><label for="message_text">Text Message</label><textarea id="message_text" name="message_text" placeholder="Enter your text caption here..."></textarea><button type="submit">Run DLR Test</button></form></article>
    </section>
    <section role="tabpanel" id="bulk-tester" aria-hidden="true">
        <article><h2>Bulk Performance Tester</h2><p>This tool will send an SMS and an MMS from both your Toll-Free and 10DLC numbers to the following destinations:</p>{% if numbers %}<ul>{% for number, name in numbers %}<li>{{ number }} {% if name %}({{ name }}){% endif %}</li>{% endfor %}</ul>{% else %}<p><em>No destination numbers configured.</em></p>{% endif %}<form action="/run_bulk_test" method="post"><button type="submit" {% if not numbers %}disabled{% endif %}>Start Performance Test</button></form></article>
    </section>
    <section role="tabpanel" id="mms-analyzer" aria-hidden="true">
        <article><h2>MMS Media Analysis Tool</h2><p>Enter a media URL to check its technical details and compare against carrier limits.</p><form action="/run_analysis" method="post"><label for="media_url">Media URL</label><input type="text" id="media_url" name="media_url" placeholder="https://.../image.png" required><button type="submit">Analyze Media</button></form></article>
    </section>
</main>
<script>
    const tabs = document.querySelectorAll('[role="tab"]');
    const tabPanels = document.querySelectorAll('[role="tabpanel"]');
    tabs.forEach(tab => {
        tab.addEventListener('click', (e) => {
            tabs.forEach(t => t.setAttribute('aria-selected', 'false'));
            tabPanels.forEach(p => p.setAttribute('aria-hidden', 'true'));
            const targetId = e.target.getAttribute('data-target');
            e.target.setAttribute('aria-selected', 'true');
            document.getElementById(targetId).setAttribute('aria-hidden', 'false');
        });
    });
</script>
</body>
</html>
"""
HTML_DLR_RESULT = """ ... """ # For single test results
HTML_BULK_RESULTS_PAGE = """ ... """ # For bulk test results
HTML_ANALYSIS_RESULT = """ ... """ # For analysis results

# --- FLASK ROUTES ---
@app.route("/")
@requires_auth
def dashboard():
    return render_template_string(HTML_DASHBOARD, numbers=DESTINATION_NUMBERS)

@app.route("/health")
def health_check():
    return "OK", 200

# ✨ FIX: The full logic for this function has been restored.
@app.route("/run_analysis", methods=["POST"])
@requires_auth
def run_analysis():
    media_url = request.form["media_url"]
    checks, spam_checks, analysis, show_preview = [], [], [], False
    try:
        response = requests.get(media_url, allow_redirects=True, timeout=10)
        response.raise_for_status()
        checks.append({"icon": "✅", "message": f"URL is accessible (Status Code: 200)."})
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
            spam_checks.append({"icon": "✅" if aspect_ratio <= 3 else "⚠️", "message": "Standard aspect ratio." if aspect_ratio <= 3 else "Image is very tall/thin, may increase spam risk."})
            try:
                text_in_image = pytesseract.image_to_string(img)
                spam_checks.append({"icon": "✅" if len(text_in_image.strip()) < 50 else "⚠️", "message": "Image is not primarily text-based." if len(text_in_image.strip()) < 50 else "Image contains significant text, increasing spam risk."})
                if any(s in text_in_image for s in ['bit.ly', 't.co']):
                    spam_checks.append({"icon": "❌", "message": "Image text contains a URL shortener, a high spam risk."})
                else:
                    spam_checks.append({"icon": "✅", "message": "No URL shorteners detected in image text."})
            except Exception:
                spam_checks.append({"icon": "⚠️", "message": "Could not perform OCR text analysis."})
        return render_template_string(HTML_ANALYSIS_RESULT, url=media_url, checks=checks, spam_checks=spam_checks, analysis=analysis, show_preview=show_preview)
    except requests.exceptions.RequestException as e:
        return render_template_string(HTML_ANALYSIS_RESULT, url=media_url, error=f"Could not connect to URL. Error: {e}")

# ... (All other routes and core logic functions remain the same as the last fully functional version) ...

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    print("This script is intended to be run with a production WSGI server like Gunicorn.")
