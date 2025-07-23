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
from psycopg2 import pool

# Load environment variables from a .env file
load_dotenv()

# --- CONFIGURATION ---
DATABASE_URL = os.getenv("DATABASE_URL")
# ... (rest of your configuration variables)
BANDWIDTH_ACCOUNT_ID = os.getenv("BANDWIDTH_ACCOUNT_ID")
BANDWIDTH_API_TOKEN = os.getenv("BANDWIDTH_API_TOKEN")
BANDWIDTH_API_SECRET = os.getenv("BANDWIDTH_API_SECRET")
# ... etc.

# --- DATABASE SETUP (PostgreSQL Pool) ---
try:
    db_pool = pool.SimpleConnectionPool(1, 10, dsn=DATABASE_URL)
    print("Database connection pool created successfully.")
except Exception as e:
    print(f"Error creating database connection pool: {e}")
    db_pool = None

def get_db_conn():
    if db_pool:
        return db_pool.getconn()
    return None

def put_db_conn(conn):
    if db_pool:
        db_pool.putconn(conn)

# Create tables on startup
def create_tables():
    conn = get_db_conn()
    if not conn: return
    try:
        with conn.cursor() as cur:
            # Main table for the DLR Tester state
            cur.execute("""
                CREATE TABLE IF NOT EXISTS latency_tests (
                    test_id VARCHAR(255) PRIMARY KEY,
                    message_id VARCHAR(255),
                    status VARCHAR(255),
                    start_time FLOAT,
                    sending_time FLOAT,
                    delivered_time FLOAT,
                    error_message TEXT
                );
            """)
            # You might want a cleanup job for old tests
        conn.commit()
        print("Table 'latency_tests' is ready.")
    except Exception as e:
        print(f"Error creating table: {e}")
    finally:
        put_db_conn(conn)

# Call on app startup
create_tables()

app = Flask(__name__)
# ... (rest of your app setup, auth, HTML templates, etc.)

# --- FLASK ROUTES ---
@app.route("/run_test", methods=["POST"])
@requires_auth
def run_latency_test():
    # ... (get form data)
    test_id = f"single_{time.time()}"
    
    # 1. Insert the initial test record into the database
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO latency_tests (test_id, status) VALUES (%s, %s)",
                (test_id, 'Sending...')
            )
        conn.commit()
    finally:
        put_db_conn(conn)

    # 2. Send the message in a background thread
    args = (from_number, application_id, destination_number, message_type, text_content, test_id)
    threading.Thread(target=send_message, args=args).start()

    # 3. Poll the database for the result
    timeout = 60
    start_poll_time = time.time()
    events = {}
    message_id = None
    error = None

    while time.time() - start_poll_time < timeout:
        conn = get_db_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT status, start_time, sending_time, delivered_time, message_id, error_message FROM latency_tests WHERE test_id = %s", (test_id,))
                record = cur.fetchone()
            if record:
                status, start_t, sending_t, delivered_t, msg_id, err_msg = record
                message_id = msg_id
                error = err_msg
                if delivered_t or err_msg:
                    events = {
                        "sent": start_t,
                        "sending": sending_t,
                        "delivered": delivered_t
                    }
                    break # Exit loop if test is complete or failed
        finally:
            put_db_conn(conn)
        time.sleep(1) # Wait 1 second before checking again

    # 4. Clean up the test from the database
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM latency_tests WHERE test_id = %s", (test_id,))
        conn.commit()
    finally:
        put_db_conn(conn)

    # 5. Render the result page
    if not events.get("delivered") and not error:
        return render_template_string(HTML_DLR_RESULT, error=f"TIMEOUT: No final handset DLR was received after {timeout} seconds.")
    if error:
        return render_template_string(HTML_DLR_RESULT, error=error)

    # Calculate latencies and render the final page
    # ... (same latency calculation and rendering logic as before) ...
    return render_template_string(HTML_DLR_RESULT, message_id=message_id, events=events)


@app.route("/report-delivery", methods=["POST"])
def report_delivery():
    data = request.get_json()
    test_id = data.get("messageId")
    timestamp_ms = data.get("timestamp")

    if not test_id or not timestamp_ms:
        return jsonify({"error": "Missing data"}), 400

    delivery_time_seconds = timestamp_ms / 1000.0
    
    # Update the test record in the database
    conn = get_db_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE latency_tests SET status = %s, delivered_time = %s WHERE test_id = %s",
                ('Delivered (Handset)', delivery_time_seconds, test_id)
            )
        conn.commit()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        print(f"Error in /report-delivery: {e}")
        return jsonify({"error": "database update failed"}), 500
    finally:
        put_db_conn(conn)


def send_message(from_number, application_id, destination_number, message_type, text_content, test_id):
    # ... (same as before, but it now updates the database instead of active_tests)
    # On success:
    # UPDATE latency_tests SET status = 'Sent', start_time = %s, message_id = %s WHERE test_id = %s
    # On failure:
    # UPDATE latency_tests SET status = 'Failed', error_message = %s WHERE test_id = %s
    pass # You would replace pass with the updated database logic
