import logging
import os
import threading

from flask import Flask, jsonify, render_template

from monitor import BettingMonitor

# Configure logging
logging.basicConfig(level=logging.INFO)

# Create Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "default_secret_key")

# Initialize the betting monitor
monitor = BettingMonitor()


@app.route("/")
def index():
    """Main page showing application status"""
    status = monitor.get_status()
    return render_template("index.html", status=status)


@app.route("/api/status")
def api_status():
    """API endpoint to get current status as JSON"""
    return jsonify(monitor.get_status())


@app.route("/api/logs")
def api_logs():
    """API endpoint to get recent logs"""
    return jsonify(monitor.get_recent_logs())


@app.route("/api/start")
def api_start():
    """API endpoint to start monitoring"""
    if not monitor.is_running():
        monitor.start()
        return jsonify({"status": "started", "message": "Monitoring started successfully"})
    return jsonify({"status": "already_running", "message": "Monitoring is already running"})


@app.route("/api/stop")
def api_stop():
    """API endpoint to stop monitoring"""
    if monitor.is_running():
        monitor.stop()
        return jsonify({"status": "stopped", "message": "Monitoring stopped successfully"})
    return jsonify({"status": "already_stopped", "message": "Monitoring is not running"})


@app.route("/api/test")
def api_test():
    """API endpoint to test the betting API connectivity"""
    result = monitor.test_api_connection()
    return jsonify(result)


# Start monitoring in background only in main reloader process
def start_background_monitoring():
    if not monitor.is_running():
        monitor.start()


# Avoid duplicate threads when Flask debug reloader imports the module twice
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
    monitor_thread = threading.Thread(target=start_background_monitoring, daemon=True)
    monitor_thread.start()
