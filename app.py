from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from pymongo import MongoClient
from datetime import datetime, timedelta
import getmac
from flask_cors import CORS


app = Flask(__name__)
CORS(app)
app.secret_key = 'super_secret_key'

# ------------------ MongoDB Setup ------------------
client = MongoClient("mongodb+srv://cloudman549:cloudman%40100@cluster0.7s7qba2.mongodb.net/license_db?retryWrites=true&w=majority&appName=Cluster0")
db = client["license_db"]
sellers_col = db["sellers"]
licenses_col = db["licenses"]

# ------------------ Helper ------------------
def get_mac_address():
    return getmac.get_mac_address()

def auto_delete_expired_licenses():
    now = datetime.now()
    all_licenses = list(licenses_col.find())
    for lic in all_licenses:
        if not lic.get("paid"):
            created_at = lic.get("created_at")
            if created_at and (now - created_at).total_seconds() > 86400:
                licenses_col.delete_one({"key": lic["key"]})
        else:
            expiry = lic.get("expiry")
            if expiry:
                expiry_date = datetime.strptime(expiry, '%Y-%m-%d')
                if (now - expiry_date).days > 2:
                    licenses_col.delete_one({"key": lic["key"]})

@app.before_request
def before_request():
    auto_delete_expired_licenses()

# ------------------ Routes ------------------
@app.route('/')
def home():
    return render_template("login.html")

# ------------------ Admin ------------------
@app.route('/admin/login', methods=['POST'])
def admin_login():
    username = request.form['username']
    password = request.form['password']
    if username == "QWEN456" and password == "QWEN456":
        session['admin'] = True
        return redirect('/admin')
    return render_template("login.html", message="Invalid Admin Credentials")

@app.route('/admin')
def admin_panel():
    if not session.get('admin'):
        return redirect('/')
    sellers = list(sellers_col.find())
    seller_stats = {}
    for s in sellers:
        keys = list(licenses_col.find({"seller": s["username"]}))
        paid = len([k for k in keys if k.get("paid")])
        unpaid = len([k for k in keys if not k.get("paid")])
        accepted = s.get("accepted_due", 0)
        due = max(paid - accepted, 0)
        seller_stats[s["username"]] = {
            "total": len(keys),
            "paid": paid,
            "unpaid": unpaid,
            "due": due
        }
    message = request.args.get("message")
    return render_template("admin_panel.html", sellers=sellers, seller_stats=seller_stats, message=message)

@app.route('/admin/create_seller', methods=['POST'])
def create_seller():
    if not session.get('admin'):
        return redirect('/')
    username = request.form['username']
    password = request.form['password']
    if sellers_col.find_one({"username": username}):
        return redirect('/admin?message=Seller already exists')
    sellers_col.insert_one({"username": username, "password": password, "active": True, "accepted_due": 0})
    return redirect('/admin?message=Seller created successfully')

@app.route('/admin/delete_seller/<username>')
def delete_seller(username):
    if not session.get('admin'):
        return redirect('/')
    sellers_col.delete_one({"username": username})
    licenses_col.delete_many({"seller": username})
    return redirect('/admin?message=Seller and licenses deleted')

@app.route('/admin/deactivate_seller/<username>')
def deactivate_seller(username):
    if not session.get('admin'):
        return redirect('/')
    sellers_col.update_one({"username": username}, {"$set": {"active": False}})
    licenses_col.update_many({"seller": username}, {"$set": {"active": False}})
    return redirect('/admin?message=Seller and keys deactivated')

@app.route('/admin/activate_seller/<username>')
def activate_seller(username):
    if not session.get('admin'):
        return redirect('/')
    sellers_col.update_one({"username": username}, {"$set": {"active": True}})
    return redirect('/admin')

@app.route('/admin/change_password/<username>', methods=['POST'])
def change_seller_password(username):
    if not session.get('admin'):
        return redirect('/')
    new_password = request.form.get('new_password')
    sellers_col.update_one({"username": username}, {"$set": {"password": new_password}})
    return redirect('/admin?message=Password updated')

@app.route('/admin/accept_due/<username>', methods=['POST'])
def accept_due(username):
    if not session.get('admin'):
        return redirect('/')
    accepted_raw = request.form.get('accept_count')
    if not accepted_raw or not accepted_raw.isdigit():
        return redirect(f"/admin?message=Invalid due amount for {username}")
    accepted_count = int(accepted_raw)
    seller = sellers_col.find_one({"username": username})
    current_accepted = seller.get("accepted_due", 0)
    paid_keys = list(licenses_col.find({"seller": username, "paid": True}))
    max_accept = len(paid_keys) - current_accepted
    accepted_count = min(accepted_count, max_accept)
    sellers_col.update_one({"username": username}, {"$inc": {"accepted_due": accepted_count}})
    return redirect(f"/admin?message=Accepted {accepted_count} due(s) for {username}")

# ------------------ Seller Panel ------------------
@app.route('/seller/login', methods=['POST'])
def seller_login():
    username = request.form['username']
    password = request.form['password']
    seller = sellers_col.find_one({"username": username, "password": password, "active": True})
    if seller:
        session['seller'] = username
        return redirect('/seller')
    return render_template("login.html", message="Invalid seller credentials or deactivated.")

@app.route('/seller')
def seller_panel():
    if not session.get('seller'):
        return redirect('/')
    seller = sellers_col.find_one({"username": session['seller']})
    accepted_due = seller.get("accepted_due", 0)
    licenses = list(licenses_col.find({"seller": session['seller']}))
    paid = len([l for l in licenses if l.get("paid")])
    tokens_due = max(paid - accepted_due, 0)
    message = request.args.get('message')
    now = datetime.now()
    return render_template("seller_panel.html", licenses=licenses, message=message, now=now, tokens_due=tokens_due)

@app.route('/seller/create_license', methods=['POST'])
def create_license():
    if not session.get('seller'):
        return redirect('/')
    requested_key = request.form.get('license_key', '').strip().upper()
    if not requested_key or licenses_col.find_one({"key": requested_key}):
        return redirect('/seller?message=License key already exists or empty.')
    expiry = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
    licenses_col.insert_one({
        "key": requested_key,
        "seller": session['seller'],
        "mac": "",
        "expiry": expiry,
        "active": True,
        "plan": "Basic",
        "paid": False,
        "created_at": datetime.now()
    })
    return redirect('/seller')

@app.route('/seller/delete_license/<key>')
def delete_license(key):
    if not session.get('seller'):
        return redirect('/')
    licenses_col.delete_one({"key": key})
    return redirect('/seller')

@app.route('/seller/reset_license/<key>')
def reset_license(key):
    if not session.get('seller'):
        return redirect('/')
    licenses_col.update_one({"key": key}, {"$set": {"mac": ""}})
    return redirect('/seller?message=License reset successfully.')

@app.route('/seller/mark_paid/<key>')
def mark_license_paid(key):
    if not session.get('seller'):
        return redirect('/')
    licenses_col.update_one({"key": key}, {"$set": {"paid": True}})
    return redirect('/seller')

@app.route('/seller/renew_license/<key>')
def renew_license(key):
    if not session.get('seller'):
        return redirect('/')
    new_expiry = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
    licenses_col.update_one({"key": key}, {"$set": {"expiry": new_expiry}})
    return redirect('/seller')

# ------------------ User Panel ------------------
@app.route('/user', methods=['POST'])
def user_login():
    key = request.form['license_key']
    mac = get_mac_address()
    lic = licenses_col.find_one({"key": key, "active": True})

    if not lic:
        return render_template("login.html", message="Invalid license key or deactivated.")

    if not lic.get("paid"):
        return render_template("login.html", message="License key is unpaid. Contact seller.")

    session['user'] = key

    if lic["mac"] == "" or lic["mac"] == mac:
        licenses_col.update_one({"key": key}, {"$set": {"mac": mac}})
        return redirect('/user/dashboard')

    return redirect('/user/dashboard?message=This license is bound to another device. Reset it if this is your system.')

@app.route('/user/dashboard')
def user_dashboard():
    if not session.get('user'):
        return redirect('/')
    message = request.args.get('message')
    lic = licenses_col.find_one({"key": session['user']})
    if lic:
        expiry_date = datetime.strptime(lic["expiry"], '%Y-%m-%d')
        days_left = (expiry_date - datetime.now()).days
        return render_template("user_panel.html", message=message, license_key=lic['key'], days_left=days_left)
    return render_template("user_panel.html", message=message)

@app.route('/user/reset')
def user_reset():
    if not session.get('user'):
        return redirect('/')
    licenses_col.update_one({"key": session['user']}, {"$set": {"mac": ""}})
    return redirect('/user/dashboard?message=License reset successfully.')

# ------------------ API ------------------

@app.route('/api/app/login/', methods=['POST'])
def api_app_login():
    data = request.get_json()
    license_key = data.get('UserName')
    mac_address = data.get('MacAddress')

    lic = licenses_col.find_one({"key": license_key})
    
    if not lic:
        return jsonify({"success": "false", "message": "License key not found"}), 404
    if not lic["active"]:
        return jsonify({"success": "false", "message": "License is deactivated"}), 403
    if not lic["paid"]:
        return jsonify({"success": "false", "message": "License is unpaid. Contact seller."}), 402
    
    expiry_date = datetime.strptime(lic["expiry"], '%Y-%m-%d')
    days_left = (expiry_date - datetime.now()).days

    if days_left < 0:
        return jsonify({"success": "false", "message": "License expired"}), 401

    if lic["mac"] == "" or lic["mac"] == mac_address:
        licenses_col.update_one({"key": license_key}, {"$set": {"mac": mac_address}})
        return jsonify({
            "success": "true",
            "message": "",
            "leftDays": days_left,
            "tokens": "unlimited",
            "appVersion": "2025.06.14",
            "ipList": "",
            "ShortMessage": "",
            "News": "",
            "KeyType": "PREMIUM",
            "paid": "PAID",
            "DllVersion": "2.0.2",
            "serverip": "127.0.0.1",
            "ChromeDriver": "137.0.0",
            "SaltKey": "abc123xyz",
            "ChromeVersion": "137.0.0",
            "UpdateURL": "http://localhost:5000/update",
            "userid": "your@email.com",
            "apikey": "test-key-1234"
        })
    else:
        return jsonify({
            "success": "false",
            "message": "License bound to another device"
        }), 409


@app.route('/validate_license', methods=['POST'])
def validate_license():
    data = request.get_json()
    license_key = data.get('UserName')
    mac_address = data.get('MacAddress')
    lic = licenses_col.find_one({"key": license_key})
    if not lic:
        return jsonify({"success": False, "message": "License key not found"}), 404
    if not lic["active"]:
        return jsonify({"success": False, "message": "License is deactivated"}), 400
    if not lic["paid"]:
        return jsonify({"success": False, "message": "License is unpaid"}), 400
    expiry_date = datetime.strptime(lic["expiry"], '%Y-%m-%d')
    days_left = (expiry_date - datetime.now()).days
    if days_left < 0:
        return jsonify({"success": False, "message": "License expired"}), 400
    if lic["mac"] == "" or lic["mac"] == mac_address:
        licenses_col.update_one({"key": license_key}, {"$set": {"mac": mac_address}})
        return jsonify({"success": True, "leftDays": days_left, "plan": lic.get("plan", "Basic")}), 200
    return jsonify({"success": False, "message": "License bound to another device"}), 400

# ------------------ Logout ------------------
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True)
