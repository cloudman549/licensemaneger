from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import json
import uuid
import datetime
import os
import getmac

app = Flask(__name__)
app.secret_key = 'super_secret_key'
DB_FILE = 'db.json'

# ---------------------------- Helper Functions ----------------------------
def load_db():
    if not os.path.exists(DB_FILE):
        with open(DB_FILE, 'w') as f:
            json.dump({"sellers": [], "licenses": []}, f)
    with open(DB_FILE, 'r') as f:
        return json.load(f)

def save_db(data):
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def get_mac_address():
    return getmac.get_mac_address()

# ----------------------------- Routes -------------------------------------

@app.route('/')
def home():
    return render_template("login.html")

# ----------------------------- Admin Login -----------------------------

@app.route('/admin/login', methods=['POST'])
def admin_login():
    username = request.form['username']
    password = request.form['password']
    if username == "QWEN456" and password == "QWEN456":
        session['admin'] = True
        return redirect('/admin')
    else:
        return render_template("login.html", message="Invalid Admin Credentials")

@app.route('/admin')
def admin_panel():
    if not session.get('admin'):
        return redirect('/')
    db = load_db()
    return render_template("admin_panel.html", sellers=db['sellers'])

@app.route('/admin/create_seller', methods=['POST'])
def create_seller():
    if not session.get('admin'):
        return redirect('/')
    username = request.form['username']
    password = request.form['password']
    db = load_db()
    for seller in db['sellers']:
        if seller['username'] == username:
            return render_template("admin_panel.html", sellers=db['sellers'], message="Seller already exists")
    db['sellers'].append({"username": username, "password": password, "active": True})
    save_db(db)
    return render_template("admin_panel.html", sellers=db['sellers'], message="Seller created successfully")

@app.route('/admin/delete_seller/<username>')
def delete_seller(username):
    if not session.get('admin'):
        return redirect('/')
    db = load_db()
    db['sellers'] = [s for s in db['sellers'] if s['username'] != username]
    save_db(db)
    return redirect('/admin')

@app.route('/admin/deactivate_seller/<username>')
def deactivate_seller(username):
    if not session.get('admin'):
        return redirect('/')
    db = load_db()
    for s in db['sellers']:
        if s['username'] == username:
            s['active'] = False
    save_db(db)
    return redirect('/admin')

@app.route('/admin/activate_seller/<username>')
def activate_seller(username):
    if not session.get('admin'):
        return redirect('/')
    db = load_db()
    for s in db['sellers']:
        if s['username'] == username:
            s['active'] = True
    save_db(db)
    return redirect('/admin')

# --------------------------- Seller Panel ----------------------------

@app.route('/seller/login', methods=['POST'])
def seller_login():
    username = request.form['username']
    password = request.form['password']
    db = load_db()
    for seller in db['sellers']:
        if seller['username'] == username and seller['password'] == password and seller['active']:
            session['seller'] = username
            return redirect('/seller')
    return render_template("login.html", message="Invalid seller credentials or deactivated.")

@app.route('/seller')
def seller_panel():
    if not session.get('seller'):
        return redirect('/')
    db = load_db()
    licenses = [lic for lic in db['licenses'] if lic['seller'] == session['seller']]
    message = request.args.get('message')
    return render_template("seller_panel.html", licenses=licenses, message=message)

@app.route('/seller/create_license', methods=['POST'])
def create_license():
    if not session.get('seller'):
        return redirect('/')

    requested_key = request.form.get('license_key', '').strip().upper()

    if not requested_key:
        db = load_db()
        licenses = [lic for lic in db['licenses'] if lic['seller'] == session['seller']]
        return render_template("seller_panel.html", licenses=licenses, message="Please enter a license key.")

    db = load_db()

    for lic in db['licenses']:
        if lic['key'] == requested_key:
            licenses = [lic for lic in db['licenses'] if lic['seller'] == session['seller']]
            return render_template("seller_panel.html", licenses=licenses, message=f"License key '{requested_key}' already exists. Please choose another.")

    mac = ""
    expiry = (datetime.datetime.now() + datetime.timedelta(days=30)).strftime('%Y-%m-%d')
    db['licenses'].append({
        "key": requested_key,
        "seller": session['seller'],
        "mac": mac,
        "expiry": expiry,
        "active": True,
        "plan": "Basic"
    })
    save_db(db)
    return redirect(url_for('seller_panel'))

@app.route('/seller/delete_license/<key>')
def delete_license(key):
    if not session.get('seller'):
        return redirect('/')
    db = load_db()
    db['licenses'] = [l for l in db['licenses'] if l['key'] != key]
    save_db(db)
    return redirect('/seller')

@app.route('/seller/reset_license/<key>')
def reset_license(key):
    if not session.get('seller'):
        return redirect('/')
    db = load_db()
    for l in db['licenses']:
        if l['key'] == key:
            l['mac'] = ""
    save_db(db)
    return redirect('/seller')

# --------------------------- User Panel ----------------------------

@app.route('/user', methods=['POST'])
def user_login():
    key = request.form['license_key']
    mac = get_mac_address()
    db = load_db()
    for lic in db['licenses']:
        if lic['key'] == key and lic['active']:
            if lic['mac'] == "" or lic['mac'] == mac:
                lic['mac'] = mac
                save_db(db)
                session['user'] = key
                return redirect('/user/dashboard')
            else:
                return render_template("login.html", message="License is already bound to another device.")
    return render_template("login.html", message="Invalid license key.")

@app.route('/user/dashboard')
def user_dashboard():
    if not session.get('user'):
        return redirect('/')
    return render_template("user_panel.html")

@app.route('/user/reset')
def user_reset():
    if not session.get('user'):
        return redirect('/')
    key = session['user']
    db = load_db()
    for l in db['licenses']:
        if l['key'] == key:
            l['mac'] = ""
    save_db(db)
    return render_template("user_panel.html", message="License reset successfully.")

# ----------------------- Updated License Validation API -----------------------

@app.route('/validate_license', methods=['POST'])
def validate_license():
    data = request.get_json()
    license_key = data.get('UserName')       # Match extension input
    mac_address = data.get('MacAddress')     # Match extension input

    db = load_db()

    for lic in db['licenses']:
        if lic['key'] == license_key:
            if not lic['active']:
                return jsonify({
                    "success": False,
                    "message": "License is deactivated"
                }), 400

            expiry_date = datetime.datetime.strptime(lic['expiry'], '%Y-%m-%d')
            days_left = (expiry_date - datetime.datetime.now()).days

            if days_left < 0:
                return jsonify({
                    "success": False,
                    "message": "License expired"
                }), 400

            if lic['mac'] == "" or lic['mac'] == mac_address:
                lic['mac'] = mac_address
                save_db(db)
                return jsonify({
                    "success": True,
                    "leftDays": days_left,
                    "plan": lic.get("plan", "Basic")
                }), 200
            else:
                return jsonify({
                    "success": False,
                    "message": "License is bound to another device"
                }), 400

    return jsonify({
        "success": False,
        "message": "License key not found"
    }), 404

# ---------------------------- Logout ----------------------------

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True)
