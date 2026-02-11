#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API для проверки лицензий (упрощенная версия с sqlite3)
"""

from flask import Flask, request, jsonify
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)

DB_FILE = "licenses.db"


def get_db():
    """Получить соединение с БД"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


@app.route('/verify', methods=['POST'])
def verify_license():
    """Проверить лицензию"""
    data = request.get_json()
    
    if not data:
        return jsonify({"valid": False, "reason": "No data provided"}), 400
    
    key = data.get("key")
    hwid = data.get("hwid")
    
    if not key or not hwid:
        return jsonify({"valid": False, "reason": "Missing key or hwid"}), 400
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Проверяем ключ
    cursor.execute("SELECT * FROM license_keys WHERE key = ?", (key,))
    row = cursor.fetchone()
    
    if not row:
        conn.close()
        return jsonify({"valid": False, "reason": "Ключ не найден"}), 404
    
    # Проверка срока действия
    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
        if datetime.now() > expires_at:
            conn.close()
            return jsonify({"valid": False, "reason": "Лицензия истекла"}), 403
    except:
        conn.close()
        return jsonify({"valid": False, "reason": "Invalid expiration date"}), 500
    
    # Проверка HWID
    if row["activated"]:
        if row["hwid"] != hwid:
            conn.close()
            return jsonify({
                "valid": False, 
                "reason": "Ключ привязан к другому устройству"
            }), 403
    else:
        # Первая активация - обновляем HWID
        cursor.execute("""
            UPDATE license_keys 
            SET activated = 1, hwid = ?, activated_at = CURRENT_TIMESTAMP
            WHERE key = ?
        """, (hwid, key))
        conn.commit()
    
    # Вычисляем оставшиеся дни
    days_left = (expires_at - datetime.now()).days
    
    conn.close()
    
    return jsonify({
        "valid": True,
        "plan": row["plan"],
        "expires_at": row["expires_at"],
        "days_left": max(0, days_left)
    }), 200


@app.route('/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({"status": "ok", "database": "sqlite"}), 200


@app.route('/', methods=['GET'])
def index():
    """Главная страница - показывает что API работает"""
    return """
    <html>
        <head><title>Timecyc Editor License API</title></head>
        <body style="font-family: Arial; padding: 50px; text-align: center;">
            <h1>🔑 Timecyc Editor License API</h1>
            <p>API работает!</p>
            <p>Скопируйте этот URL и вставьте в timecyc_editor_protected.py:</p>
            <code style="background: #f0f0f0; padding: 10px; display: block; margin: 20px;">
                """ + request.url_root.rstrip('/') + """
            </code>
            <p><small>Endpoints: /verify, /health</small></p>
        </body>
    </html>
    """


if __name__ == '__main__':
    if not os.path.exists(DB_FILE):
        print(f"Warning: Database {DB_FILE} not found!")
        print("Please run bot_simple.py first to initialize the database")
    
    app.run(host='0.0.0.0', port=5000, debug=True)
