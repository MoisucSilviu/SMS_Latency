import os
import requests
from flask import Flask, request, render_template_string, Response
from functools import wraps
from dotenv import load_dotenv

# Load environment variables from a .env file
load_dotenv()

# --- CONFIGURATION ---
APP_USERNAME = os.getenv("APP_USERNAME", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "password")

# Carrier-specific limits for analysis
CARRIER_LIMITS = {
    "AT&T": 1000,
    "T-Mobile": 1000,
    "Verizon": 1200, # More tolerant, but 1.2MB is a common cap
    "Sprint/Legacy": 2000,
    "Toll-Free": 525 
}

# --- FLASK APP SETUP ---
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

# --- HTML TEMPLATES & STYLES ---
HTML_HEADER = """
<!DOCTYPE html>
<html lang="en" data-theme="light">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Bandwidth Support Tools</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css"/>
    <style>
        body > main { padding: 2rem; }
        .error { background-color: var(--pico-form-element-invalid-background-color); color: var(--pico-form-element-invalid-color); padding: 1rem; border-radius: var(--pico-border-radius); }
        .success { color: var(--pico-color-green-600); }
        .warning { color: var(--pico-color-amber-700); }
        figure { max-width: 300px; margin-top: 1rem; }
        hgroup { margin-bottom: 2rem; }
    </style>
</head>
<body>
<main class="container">
    <nav><ul><li><strong>Bandwidth Tools</strong></li></ul></nav>
"""
HTML_FOOTER = """
</main>
</body>
</html>
"""
HTML_FORM = HTML_HEADER + """
    <article>
        <h2>MMS Media Analysis Tool</h2>
        <p>Enter a media URL to check its technical details and compare against carrier limits.</p>
        <form action="/inspect" method="post">
            <label for="media_url">Media URL</label>
            <input type="text" id="media_url" name="media_url" placeholder="https://.../image.png" required>
            <button type="submit">Analyze Media</button>
        </form>
    </article>
""" + HTML_FOOTER

HTML_RESULT = HTML_HEADER + """
    <article>
        <hgroup>
            <h2>Analysis Report</h2>
            <p><strong>URL:</strong> <a href="{{ url }}" target="_blank" style="word-break:break-all;">{{ url }}</a></p>
        </hgroup>
        {% if error %}
            <p class="error"><strong>Error:</strong> {{ error }}</p>
        {% else %}
            <div class="grid">
                <section>
                    <h4>Technical Details</h4>
                    <ul>
                    {% for check in checks %}
                        <li>{{ check.icon }} {{ check.message }}</li>
                    {% endfor %}
                    </ul>
                    {% if show_preview %}
                        <h4>Media Preview</h4>
                        <figure><img src="{{ url }}" alt="Media Preview"></figure>
                    {% endif %}
                </section>
                <section>
                    <h4>Carrier Compatibility</h4>
                    <figure><table>
                        <thead><tr><th>Carrier</th><th>Status</th><th>Note</th></tr></thead>
                        <tbody>
                        {% for carrier in analysis %}
                        <tr>
                            <td><strong>{{ carrier.name }}</strong></td>
                            <td><span class="{{'success' if carrier.status == 'OK' else 'error'}}">{{ carrier.status }}</span></td>
                            <td>{{ carrier.note }}</td>
                        </tr>
                        {% endfor %}
                        </tbody>
                    </table></figure>
                </section>
            </div>
        {% endif %}
        <a href="/" role="button" class="secondary">Analyze another URL</a>
    </article>
""" + HTML_FOOTER

# --- FLASK ROUTES ---
@app.route("/")
@requires_auth
def index():
    return render_template_string(HTML_FORM)

@app.route("/inspect", methods=["POST"])
@requires_auth
def inspect_media():
    media_url = request.form["media_url"]
    checks = []
    analysis = []
    show_preview = False
    
    try:
        response = requests.head(media_url, allow_redirects=True, timeout=10)
        
        if response.status_code != 200:
            error_message = f"URL is not accessible (Status Code: {response.status_code})."
            return render_template_string(HTML_RESULT, url=media_url, error=error_message)

        # Technical analysis
        checks.append({"icon": "✅", "message": f"URL is accessible (Status Code: 200)."})
        content_type = response.headers.get('Content-Type', 'N/A')
        supported_types = ['image/jpeg', 'image/png', 'image/gif']

        if any(supported_type in content_type for supported_type in supported_types):
            checks.append({"icon": "✅", "message": f"Content-Type '{content_type}' is well-supported."})
            show_preview = True
        else:
            checks.append({"icon": "⚠️", "message": f"Warning: Content-Type '{content_type}' may not be supported by all carriers."})

        # Carrier compatibility analysis
        content_length = response.headers.get('Content-Length')
        if content_length:
            size_in_kb = int(content_length) / 1024
            checks.append({"icon": "✅", "message": f"File size is {size_in_kb:.0f} KB."})
            for carrier, limit in CARRIER_LIMITS.items():
                if size_in_kb > limit:
                    analysis.append({
                        "name": carrier,
                        "status": "REJECT",
                        "note": f"File size ({size_in_kb:.0f}KB) exceeds the approximate limit of {limit}KB."
                    })
                else:
                    analysis.append({
                        "name": carrier,
                        "status": "OK",
                        "note": f"File size is within the approximate limit of {limit}KB."
                    })
        else:
            checks.append({"icon": "⚠️", "message": "Could not determine file size from headers."})
            for carrier, limit in CARRIER_LIMITS.items():
                analysis.append({
                    "name": carrier, "status": "Unknown", "note": "File size could not be determined."
                })

        return render_template_string(HTML_RESULT, url=media_url, checks=checks, analysis=analysis, show_preview=show_preview)

    except requests.exceptions.RequestException as e:
        return render_template_string(HTML_RESULT, url=media_url, error=f"Could not connect to the URL. Error: {e}")

# This block is for local development and will not be used by Gunicorn
if __name__ == "__main__":
    if not all([APP_USERNAME, APP_PASSWORD]):
        print("❌ FATAL ERROR: APP_USERNAME and APP_PASSWORD must be set as environment variables.")
        sys.exit(1)
        
    print("This script is intended to be run with a production WSGI server like Gunicorn.")
