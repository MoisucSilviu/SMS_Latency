# gunicorn_starter.py
from gevent import monkey
monkey.patch_all()

# Ensure this matches your main script's filename
from app import app
