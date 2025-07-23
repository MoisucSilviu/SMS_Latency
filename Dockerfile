# Start with a standard, lightweight Python image
FROM python:3.11-slim

# Set a working directory inside the container
WORKDIR /app

# Install system dependencies, including Tesseract OCR
# This runs as root inside the build environment
RUN apt-get update && apt-get install -y tesseract-ocr

# Copy your requirements file and install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code into the container
COPY . .

# Set the command to run your application
# MODIFIED: Point directly to your main app.py file
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "app:app"]
