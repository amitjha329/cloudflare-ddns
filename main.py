import requests
import time
import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template_string
import json

# Configuration (replace with your actual values or use environment variables)
CLOUDFLARE_API_TOKEN = os.getenv('CF_API_TOKEN', 'your_cloudflare_api_token')
ZONE_ID = os.getenv('CF_ZONE_ID', 'your_zone_id')
UPDATE_INTERVAL = int(os.getenv('CF_UPDATE_INTERVAL', 300))  # seconds
DB_PATH = os.getenv('DB_PATH', 'updates.db')

# Parse CF_RECORDS from environment variable
try:
    CF_RECORDS = json.loads(os.getenv('CF_RECORDS', '[]'))
    if not isinstance(CF_RECORDS, list):
        raise ValueError('CF_RECORDS is not a list')
except Exception as e:
    print(f"Error parsing CF_RECORDS: {e}")
    CF_RECORDS = []

HEADERS = {
    'Authorization': f'Bearer {CLOUDFLARE_API_TOKEN}',
    'Content-Type': 'application/json',
}

# Update DB schema to include record_name and record_id
# Initialize SQLite DB and create table if not exists
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS updates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        ip TEXT,
        record_name TEXT,
        record_id TEXT,
        status TEXT,
        response TEXT
    )''')
    conn.commit()
    conn.close()

def log_update(ip, record_name, record_id, status, response):
    # Try to pretty-print JSON response if possible
    try:
        resp_obj = json.loads(response)
        if isinstance(resp_obj, dict):
            # Extract key info if available
            if 'result' in resp_obj and isinstance(resp_obj['result'], dict):
                result = resp_obj['result']
                summary = f"name: {result.get('name')}, type: {result.get('type')}, content: {result.get('content')}, ttl: {result.get('ttl')}, proxied: {result.get('proxied')}, modified_on: {result.get('modified_on')}"
                response_str = summary
            else:
                response_str = json.dumps(resp_obj, indent=2)
        else:
            response_str = str(response)
    except Exception:
        response_str = str(response)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO updates (timestamp, ip, record_name, record_id, status, response) VALUES (?, ?, ?, ?, ?, ?)',
              (datetime.utcnow().isoformat(), ip, record_name, record_id, status, response_str))
    # Keep only last 50 logs
    c.execute('DELETE FROM updates WHERE id NOT IN (SELECT id FROM updates ORDER BY id DESC LIMIT 50)')
    conn.commit()
    conn.close()

# Flask app for log viewing
app = Flask(__name__)

@app.route('/')
def view_logs():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT timestamp, ip, record_name, record_id, status, response FROM updates ORDER BY id DESC LIMIT 50')
    logs = c.fetchall()
    conn.close()
    return render_template_string('''
    <html><head><title>Cloudflare DDNS Update Logs</title></head><body>
    <h2>Last 50 Cloudflare DDNS Updates</h2>
    <table border="1" cellpadding="5"><tr><th>Timestamp (UTC)</th><th>IP</th><th>Record Name</th><th>Record ID</th><th>Status</th><th>Response</th></tr>
    {% for log in logs %}
    <tr><td>{{log[0]}}</td><td>{{log[1]}}</td><td>{{log[2]}}</td><td>{{log[3]}}</td><td>{{log[4]}}</td><td><pre style="white-space:pre-wrap">{{log[5]}}</pre></td></tr>
    {% endfor %}
    </table></body></html>
    ''', logs=logs)

def get_public_ip():
    try:
        return requests.get('https://api.ipify.org').text.strip()
    except Exception as e:
        print(f"Error getting public IP: {e}")
        return None

def update_cloudflare_dns(ip):
    for record in CF_RECORDS:
        record_id = record.get('record_id')
        record_name = record.get('record_name')
        if not record_id or not record_name:
            print(f"Skipping invalid record: {record}")
            continue
        cf_api_url = f'https://api.cloudflare.com/client/v4/zones/{ZONE_ID}/dns_records/{record_id}'
        data = {
            'type': 'A',
            'name': record_name,
            'content': ip,
            'ttl': 1,  # Auto
            'proxied': False
        }
        try:
            resp = requests.put(cf_api_url, headers=HEADERS, json=data)
            status = 'success' if resp.status_code == 200 else 'fail'
            log_update(ip, record_name, record_id, status, resp.text)
            if resp.status_code == 200:
                print(f"Cloudflare DNS updated for {record_name} ({record_id}) to {ip}")
            else:
                print(f"Failed to update Cloudflare DNS for {record_name} ({record_id}): {resp.text}")
        except Exception as e:
            log_update(ip, record_name, record_id, 'error', str(e))
            print(f"Exception updating Cloudflare DNS for {record_name} ({record_id}): {e}")

def main():
    # Ensure the database exists and is initialized
    if not os.path.exists(DB_PATH):
        init_db()
    last_ip = None
    while True:
        ip = get_public_ip()
        if ip and ip != last_ip:
            update_cloudflare_dns(ip)
            last_ip = ip
        else:
            print(f"IP unchanged: {ip}")
        time.sleep(UPDATE_INTERVAL)

if __name__ == '__main__':
    import threading
    # Start the log viewer web server in a separate thread
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=8000), daemon=True).start()
    main()