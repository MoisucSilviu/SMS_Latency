# gunicorn_starter.py

# Apply the gevent patch first, before any other imports
from gevent import monkey
monkey.patch_all()

# Now, import your Flask app instance
from web_tester import app

# This file doesn't need a main block,
# Gunicorn will use the 'app' variable.
