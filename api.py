#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simple API для проверки лицензий
Может работать на том же хосте что и бот
"""

from flask import Flask, request, jsonify
import json
import os
from datetime import datetime

app = Flask(__name__)

LICENSE_DB = "licenses.json"


def load_licenses():
    """Загрузить базу лицензий"""
    if os.path.exists(LICENSE_DB):
        try:
            with open(LICENSE_DB, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {"users": {}, "keys": {}, "transactions": []}


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
    
    # Загружаем базу
    db = load_licenses()
    
    # Проверяем ключ
    if key not in db["keys"]:
        return jsonify({"valid": False, "reason": "Ключ не найден"}), 404
    
    lic = db["keys"][key]
    
    # Проверка срока действия
    try:
        expires_at = datetime.fromisoformat(lic["expires_at"])
        if datetime.now() > expires_at:
            return jsonify({"valid": False, "reason": "Лицензия истекла"}), 403
    except:
        return jsonify({"valid": False, "reason": "Invalid expiration date"}), 500
    
    # Проверка HWID
    if lic.get("activated"):
        if lic.get("hwid") != hwid:
            return jsonify({
                "valid": False, 
                "reason": "Ключ привязан к другому устройству"
            }), 403
    else:
        # Первая активация - обновляем HWID
        lic["hwid"] = hwid
        lic["activated"] = True
        lic["activated_at"] = datetime.now().isoformat()
        
        # Сохраняем
        try:
            with open(LICENSE_DB, 'w', encoding='utf-8') as f:
                json.dump(db, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving: {e}")
    
    # Вычисляем оставшиеся дни
    days_left = (expires_at - datetime.now()).days
    
    return jsonify({
        "valid": True,
        "plan": lic["plan"],
        "expires_at": lic["expires_at"],
        "days_left": max(0, days_left)
    }), 200


@app.route('/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({"status": "ok"}), 200


if __name__ == '__main__':
    # Для локальной разработки
    app.run(host='0.0.0.0', port=5000, debug=True)
