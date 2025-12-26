from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from pymongo import MongoClient
from bson import ObjectId
from datetime import datetime, timedelta
import getmac
from flask_cors import CORS
import base64
from io import BytesIO
from PIL import Image
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)
app.secret_key = os.getenv('SECRET_KEY', 'super_secret_key')

# MongoDB Setup
client = MongoClient("mongodb+srv://cloudman549:cloudman%40100@cluster0.7s7qba2.mongodb.net/license_db?retryWrites=true&w=majority&appName=Cluster0")
db = client["license_db"]
masters_col = db["masters"]
admins_col = db["admins"]
supers_col = db["supers"]
sellers_col = db["sellers"]
licenses_col = db["licenses"]
screenshots_col = db["screenshots"]

# Helper Functions
def get_mac_address():
    return getmac.get_mac_address()

def calculate_left_days(expiry_str):
    """Calculate days left from expiry string"""
    try:
        if not expiry_str:
            return -1
        expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d')
        now = datetime.now()
        days_left = (expiry_date - now).days
        return days_left
    except Exception as e:
        print(f'[Calculate Days Error]: {e}')
        return -1

def auto_delete_expired_licenses():
    try:
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
    except Exception as e:
        print(f'[Auto Delete Licenses Error]: {e}')

def auto_delete_old_screenshots():
    try:
        cutoff = datetime.now() - timedelta(hours=12)
        result = screenshots_col.delete_many({"upload_time": {"$lt": cutoff}})
        print(f"[DEBUG] Deleted {result.deleted_count} screenshots older than 12 hours")
    except Exception as e:
        print(f'[Auto Delete Screenshots Error]: {e}')

def check_and_deactivate_due_entities(collection, role, child_collections):
    try:
        now = datetime.now()
        entities = list(collection.find())
        for entity in entities:
            if entity.get("active", True):
                entity_id = entity['_id']
                total_billable = 0
                if role == "master":
                    admin_ids = [doc['_id'] for doc in admins_col.find({"parent_id": entity_id, "active": True})]
                    super_ids = [doc['_id'] for doc in supers_col.find({"parent_id": {"$in": admin_ids}, "active": True})]
                    seller_usernames = [doc["username"] for doc in sellers_col.find({"parent_id": {"$in": super_ids}, "active": True})]
                    for seller in seller_usernames:
                        paid_licenses = list(licenses_col.find({"seller": seller, "paid": True, "active": True}))
                        total_billable += len(paid_licenses)
                        all_licenses = list(licenses_col.find({"seller": seller}))
                        total_billable += sum(l.get("renew_count", 0) for l in all_licenses)
                    child_ids = admin_ids
                    next_child_ids = super_ids
                    seller_usernames_all = seller_usernames
                elif role == "admin":
                    admin_id = entity_id
                    super_ids = [doc['_id'] for doc in supers_col.find({"parent_id": admin_id, "active": True})]
                    seller_usernames = [doc["username"] for doc in sellers_col.find({"parent_id": {"$in": super_ids}, "active": True})]
                    for seller in seller_usernames:
                        paid_licenses = list(licenses_col.find({"seller": seller, "paid": True, "active": True}))
                        total_billable += len(paid_licenses)
                        all_licenses = list(licenses_col.find({"seller": seller}))
                        total_billable += sum(l.get("renew_count", 0) for l in all_licenses)
                    child_ids = super_ids
                    next_child_ids = []
                    seller_usernames_all = seller_usernames
                elif role == "super":
                    super_id = entity_id
                    seller_usernames = [doc["username"] for doc in sellers_col.find({"parent_id": super_id, "active": True})]
                    for seller in seller_usernames:
                        paid_licenses = list(licenses_col.find({"seller": seller, "paid": True, "active": True}))
                        total_billable += len(paid_licenses)
                        all_licenses = list(licenses_col.find({"seller": seller}))
                        total_billable += sum(l.get("renew_count", 0) for l in all_licenses)
                    child_ids = []
                    next_child_ids = []
                    seller_usernames_all = seller_usernames
                else:
                    continue
                accepted_due = entity.get("accepted_due", 0)
                pending_billable = max(total_billable - accepted_due, 0)
                if pending_billable > 0:
                    due_date = entity.get("due_date")
                    if due_date:
                        due_date = due_date if isinstance(due_date, datetime) else datetime.strptime(due_date, '%Y-%m-%d %H:%M:%S.%f')
                    if due_date and (now - due_date).total_seconds() > 86400:
                        collection.update_one({"_id": entity_id}, {"$set": {"active": False}})
                        for child_col_name, child_id_field in child_collections.items():
                            if child_id_field:
                                child_col = globals()[child_col_name]
                                child_col.update_many({child_id_field: {"$in": child_ids}}, {"$set": {"active": False}})
                            else:
                                child_col = globals()[child_col_name]
                                child_col.update_many({"parent_id": {"$in": next_child_ids}}, {"$set": {"active": False}})
                        licenses_col.update_many({"seller": {"$in": seller_usernames_all}}, {"$set": {"active": False}})
                        print(f"[DEBUG] Deactivated {role} {entity['username']} due to unpaid dues")
    except Exception as e:
        print(f'[Deactivate Due Error]: {e}')

@app.before_request
def before_request():
    try:
        auto_delete_expired_licenses()
        auto_delete_old_screenshots()
        check_and_deactivate_due_entities(masters_col, "master", {"admins_col": "parent_id", "supers_col": "parent_id", "sellers_col": ""})
        check_and_deactivate_due_entities(admins_col, "admin", {"supers_col": "parent_id", "sellers_col": ""})
        check_and_deactivate_due_entities(supers_col, "super", {"sellers_col": "parent_id"})
    except Exception as e:
        print(f'[Before Request Error]: {e}')

# Routes
@app.route('/')
def home():
    return render_template("login.html")

# Master Panel
@app.route('/master/login', methods=['POST'])
def master_login():
    username = request.form['username'].strip().upper()
    password = request.form['password'].strip()
    master = masters_col.find_one({"username": username, "password": password, "active": True})
    if master:
        session['role'] = 'master'
        session['master'] = username
        session['master_id'] = str(master['_id'])
        return redirect('/master')
    return render_template("login.html", message="Invalid Master Credentials or deactivated")

@app.route('/master')
def master_panel():
    if session.get('role') != 'master':
        return redirect('/')
    master_id = ObjectId(session['master_id'])
    admins = list(admins_col.find({"parent_id": master_id}).sort("username"))
    admin_stats = {}
    total_admins = len([a for a in admins if a.get('active', True)])
    total_supers = 0
    total_sellers = 0
    total_licenses = 0
    total_paid = 0
    now = datetime.now()
    for a in admins:
        a_id = a['_id']
        sups = list(supers_col.find({"parent_id": a_id, "active": True}))
        num_supers = len(sups)
        total_supers += num_supers
        num_sellers = 0
        num_licenses = 0
        num_billable = 0
        for su in sups:
            su_id = su['_id']
            sells = list(sellers_col.find({"parent_id": su_id, "active": True}))
            num_sellers += len(sells)
            for sel in sells:
                lics = list(licenses_col.find({"seller": sel["username"], "active": True}))
                num_licenses += len(lics)
                paid_count = len([l for l in lics if l.get("paid", False)])
                renew_total = sum(l.get("renew_count", 0) for l in lics)
                num_billable += paid_count + renew_total
        total_sellers += num_sellers
        total_licenses += num_licenses
        total_paid += num_billable
        accepted_due = a.get("accepted_due", 0)
        pending_billable = max(num_billable - accepted_due, 0)
        rate = a.get("rate", 0)
        due_amount = pending_billable * rate
        due_date = a.get("due_date")
        hours_left = None
        if pending_billable > 0 and due_date:
            due_date = due_date if isinstance(due_date, datetime) else datetime.strptime(due_date, '%Y-%m-%d %H:%M:%S.%f')
            hours_left = max(0, 24 - (now - due_date).total_seconds() / 3600)
        admin_stats[a["username"]] = {
            "num_supers": num_supers,
            "num_sellers": num_sellers,
            "num_licenses": num_licenses,
            "paid_licenses": num_billable,
            "rate": rate,
            "due_amount": due_amount,
            "hours_left": hours_left,
            "pending_billable": pending_billable
        }
    message = request.args.get("message")
    return render_template("master_panel.html", admins=admins, admin_stats=admin_stats,
                          total_admins=total_admins, total_supers=total_supers, total_sellers=total_sellers,
                          total_licenses=total_licenses, total_paid=total_paid, message=message)

@app.route('/master/create_admin', methods=['POST'])
def create_admin():
    if session.get('role') != 'master':
        return redirect('/')
    username = request.form['username'].strip().upper()
    password = request.form['password'].strip()
    rate = int(request.form.get('rate', 0))
    if admins_col.find_one({"username": username, "parent_id": ObjectId(session['master_id'])}):
        return redirect('/master?message=Admin already exists')
    expiry = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
    admins_col.insert_one({
        "username": username,
        "password": password,
        "parent_id": ObjectId(session['master_id']),
        "active": True,
        "accepted_due": 0,
        "rate": rate,
        "expiry": expiry
    })
    return redirect('/master?message=Admin created successfully')

@app.route('/master/delete_admin/<username>')
def delete_admin(username):
    if session.get('role') != 'master':
        return redirect('/')
    admin = admins_col.find_one({"username": username, "parent_id": ObjectId(session['master_id'])})
    if not admin:
        return redirect('/master?message=Admin not found')
    admin_id = admin['_id']
    super_ids = [doc['_id'] for doc in supers_col.find({"parent_id": admin_id})]
    seller_usernames = [doc["username"] for doc in sellers_col.find({"parent_id": {"$in": super_ids}})]
    licenses_col.delete_many({"seller": {"$in": seller_usernames}})
    sellers_col.delete_many({"parent_id": {"$in": super_ids}})
    supers_col.delete_many({"parent_id": admin_id})
    admins_col.delete_one({"_id": admin_id})
    return redirect('/master?message=Admin and subordinates deleted')

@app.route('/master/deactivate_admin/<username>')
def deactivate_admin(username):
    if session.get('role') != 'master':
        return redirect('/')
    admin = admins_col.find_one({"username": username, "parent_id": ObjectId(session['master_id'])})
    if not admin:
        return redirect('/master?message=Admin not found')
    admin_id = admin['_id']
    super_ids = [doc['_id'] for doc in supers_col.find({"parent_id": admin_id})]
    seller_usernames = [doc["username"] for doc in sellers_col.find({"parent_id": {"$in": super_ids}})]
    admins_col.update_one({"_id": admin_id}, {"$set": {"active": False}})
    supers_col.update_many({"parent_id": admin_id}, {"$set": {"active": False}})
    sellers_col.update_many({"parent_id": {"$in": super_ids}}, {"$set": {"active": False}})
    licenses_col.update_many({"seller": {"$in": seller_usernames}}, {"$set": {"active": False}})
    return redirect('/master?message=Admin and subordinates deactivated')

@app.route('/master/activate_admin/<username>')
def activate_admin(username):
    if session.get('role') != 'master':
        return redirect('/')
    admins_col.update_one({"username": username, "parent_id": ObjectId(session['master_id'])}, {"$set": {"active": True}})
    return redirect('/master?message=Admin activated')

@app.route('/master/change_password/<username>', methods=['POST'])
def change_admin_password(username):
    if session.get('role') != 'master':
        return redirect('/')
    new_password = request.form.get('new_password').strip()
    admins_col.update_one({"username": username, "parent_id": ObjectId(session['master_id'])}, {"$set": {"password": new_password}})
    return redirect('/master?message=Password updated')

@app.route('/master/update_rate/<username>', methods=['POST'])
def update_admin_rate(username):
    if session.get('role') != 'master':
        return redirect('/')
    new_rate = int(request.form.get('new_rate', 0))
    admins_col.update_one({"username": username, "parent_id": ObjectId(session['master_id'])}, {"$set": {"rate": new_rate}})
    return redirect('/master?message=Rate updated')

@app.route('/master/accept_due/<username>', methods=['POST'])
def accept_admin_due(username):
    if session.get('role') != 'master':
        return redirect('/')
    admin = admins_col.find_one({"username": username, "parent_id": ObjectId(session['master_id'])})
    if not admin:
        return redirect('/master?message=Admin not found')
    admin_id = admin['_id']
    super_ids = [doc['_id'] for doc in supers_col.find({"parent_id": admin_id, "active": True})]
    seller_usernames = [doc["username"] for doc in sellers_col.find({"parent_id": {"$in": super_ids}, "active": True})]
    total_billable = 0
    for seller in seller_usernames:
        paid_licenses = list(licenses_col.find({"seller": seller, "paid": True, "active": True}))
        total_billable += len(paid_licenses)
        all_licenses = list(licenses_col.find({"seller": seller}))
        total_billable += sum(l.get("renew_count", 0) for l in all_licenses)
    accepted_due = admin.get("accepted_due", 0)
    pending_billable = max(total_billable - accepted_due, 0)
    if pending_billable > 0:
        admins_col.update_one({"username": username, "parent_id": ObjectId(session['master_id'])},
                              {"$inc": {"accepted_due": pending_billable}, "$unset": {"due_date": ""}})
        return redirect(f"/master?message=Accepted {pending_billable} due(s) for {username}")
    return redirect(f"/master?message=No dues to accept for {username}")

@app.route('/master/view_admin/<admin_username>')
def view_admin(admin_username):
    if session.get('role') != 'master':
        return redirect('/')
    admin = admins_col.find_one({"username": admin_username, "parent_id": ObjectId(session['master_id'])})
    if not admin:
        return redirect('/master?message=Admin not found')
    a_id = admin['_id']
    sups = list(supers_col.find({"parent_id": a_id}).sort("username"))
    super_details = {}
    for su in sups:
        su_id = su['_id']
        sells = list(sellers_col.find({"parent_id": su_id}).sort("username"))
        sell_details = {}
        for sel in sells:
            lics = list(licenses_col.find({"seller": sel["username"]}).sort("key"))
            sell_details[sel["username"]] = {
                "active": sel.get("active", True),
                "licenses": lics
            }
        super_details[su["username"]] = {
            "active": su.get("active", True),
            "sellers": sell_details
        }
    return render_template("view_admin.html", admin_username=admin_username, supers=sups, super_details=super_details)

# Admin Panel
@app.route('/admin/login', methods=['POST'])
def admin_login():
    username = request.form['username'].strip().upper()
    password = request.form['password'].strip()
    admin = admins_col.find_one({"username": username, "password": password, "active": True})
    if admin:
        session['role'] = 'admin'
        session['admin'] = username
        session['admin_id'] = str(admin['_id'])
        session['master_id'] = str(admin['parent_id'])
        return redirect('/admin')
    return render_template("login.html", message="Invalid Admin Credentials or deactivated")

@app.route('/admin')
def admin_panel():
    if session.get('role') != 'admin':
        return redirect('/')
    admin_id = ObjectId(session['admin_id'])
    supers = list(supers_col.find({"parent_id": admin_id}).sort("username"))
    super_stats = {}
    total_supers = len([s for s in supers if s.get('active', True)])
    total_sellers = 0
    total_licenses = 0
    total_paid = 0
    now = datetime.now()
    for s in supers:
        s_id = s['_id']
        sells = list(sellers_col.find({"parent_id": s_id, "active": True}))
        num_sellers = len(sells)
        total_sellers += num_sellers
        num_licenses = 0
        num_billable = 0
        for sel in sells:
            lics = list(licenses_col.find({"seller": sel["username"], "active": True}))
            num_licenses += len(lics)
            paid_count = len([l for l in lics if l.get("paid", False)])
            renew_total = sum(l.get("renew_count", 0) for l in lics)
            num_billable += paid_count + renew_total
        total_licenses += num_licenses
        total_paid += num_billable
        accepted_due = s.get("accepted_due", 0)
        pending_billable = max(num_billable - accepted_due, 0)
        rate = s.get("rate", 0)
        due_amount = pending_billable * rate
        due_date = s.get("due_date")
        hours_left = None
        if pending_billable > 0 and due_date:
            due_date = due_date if isinstance(due_date, datetime) else datetime.strptime(due_date, '%Y-%m-%d %H:%M:%S.%f')
            hours_left = max(0, 24 - (now - due_date).total_seconds() / 3600)
        super_stats[s["username"]] = {
            "num_sellers": num_sellers,
            "num_licenses": num_licenses,
            "paid_licenses": num_billable,
            "rate": rate,
            "due_amount": due_amount,
            "hours_left": hours_left,
            "pending_billable": pending_billable
        }
    message = request.args.get("message")
    return render_template("admin_panel.html", supers=supers, super_stats=super_stats,
                          total_supers=total_supers, total_sellers=total_sellers,
                          total_licenses=total_licenses, total_paid=total_paid, message=message)

@app.route('/admin/create_super', methods=['POST'])
def create_super():
    if session.get('role') != 'admin':
        return redirect('/')
    username = request.form['username'].strip().upper()
    password = request.form['password'].strip()
    rate = int(request.form.get('rate', 0))
    if supers_col.find_one({"username": username, "parent_id": ObjectId(session['admin_id'])}):
        return redirect('/admin?message=Super already exists')
    expiry = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
    supers_col.insert_one({
        "username": username,
        "password": password,
        "parent_id": ObjectId(session['admin_id']),
        "active": True,
        "accepted_due": 0,
        "rate": rate,
        "expiry": expiry
    })
    return redirect('/admin?message=Super created successfully')

@app.route('/admin/delete_super/<username>')
def delete_super(username):
    if session.get('role') != 'admin':
        return redirect('/')
    super_user = supers_col.find_one({"username": username, "parent_id": ObjectId(session['admin_id'])})
    if not super_user:
        return redirect('/admin?message=Super not found')
    super_id = super_user['_id']
    seller_usernames = [doc["username"] for doc in sellers_col.find({"parent_id": super_id})]
    licenses_col.delete_many({"seller": {"$in": seller_usernames}})
    sellers_col.delete_many({"parent_id": super_id})
    supers_col.delete_one({"_id": super_id})
    return redirect('/admin?message=Super and subordinates deleted')

@app.route('/admin/deactivate_super/<username>')
def deactivate_super(username):
    if session.get('role') != 'admin':
        return redirect('/')
    super_user = supers_col.find_one({"username": username, "parent_id": ObjectId(session['admin_id'])})
    if not super_user:
        return redirect('/admin?message=Super not found')
    super_id = super_user['_id']
    seller_usernames = [doc["username"] for doc in sellers_col.find({"parent_id": super_id})]
    supers_col.update_one({"_id": super_id}, {"$set": {"active": False}})
    sellers_col.update_many({"parent_id": super_id}, {"$set": {"active": False}})
    licenses_col.update_many({"seller": {"$in": seller_usernames}}, {"$set": {"active": False}})
    return redirect('/admin?message=Super and subordinates deactivated')

@app.route('/admin/activate_super/<username>')
def activate_super(username):
    if session.get('role') != 'admin':
        return redirect('/')
    supers_col.update_one({"username": username, "parent_id": ObjectId(session['admin_id'])}, {"$set": {"active": True}})
    return redirect('/admin?message=Super activated')

@app.route('/admin/change_password/<username>', methods=['POST'])
def change_super_password(username):
    if session.get('role') != 'admin':
        return redirect('/')
    new_password = request.form.get('new_password').strip()
    supers_col.update_one({"username": username, "parent_id": ObjectId(session['admin_id'])}, {"$set": {"password": new_password}})
    return redirect('/admin?message=Password updated')

@app.route('/admin/update_rate/<username>', methods=['POST'])
def update_super_rate(username):
    if session.get('role') != 'admin':
        return redirect('/')
    new_rate = int(request.form.get('new_rate', 0))
    supers_col.update_one({"username": username, "parent_id": ObjectId(session['admin_id'])}, {"$set": {"rate": new_rate}})
    return redirect('/admin?message=Rate updated')

@app.route('/admin/accept_due/<username>', methods=['POST'])
def accept_super_due(username):
    if session.get('role') != 'admin':
        return redirect('/')
    super_user = supers_col.find_one({"username": username, "parent_id": ObjectId(session['admin_id'])})
    if not super_user:
        return redirect('/admin?message=Super not found')
    super_id = super_user['_id']
    seller_usernames = [doc["username"] for doc in sellers_col.find({"parent_id": super_id, "active": True})]
    total_billable = 0
    for seller in seller_usernames:
        paid_licenses = list(licenses_col.find({"seller": seller, "paid": True, "active": True}))
        total_billable += len(paid_licenses)
        all_licenses = list(licenses_col.find({"seller": seller}))
        total_billable += sum(l.get("renew_count", 0) for l in all_licenses)
    accepted_due = super_user.get("accepted_due", 0)
    pending_billable = max(total_billable - accepted_due, 0)
    if pending_billable > 0:
        supers_col.update_one({"username": username, "parent_id": ObjectId(session['admin_id'])},
                              {"$inc": {"accepted_due": pending_billable}, "$unset": {"due_date": ""}})
        return redirect(f"/admin?message=Accepted {pending_billable} due(s) for {username}")
    return redirect(f"/admin?message=No dues to accept for {username}")

@app.route('/admin/view_super/<super_username>')
def view_super(super_username):
    if session.get('role') != 'admin':
        return redirect('/')
    super_user = supers_col.find_one({"username": super_username, "parent_id": ObjectId(session['admin_id'])})
    if not super_user:
        return redirect('/admin?message=Super not found')
    su_id = super_user['_id']
    sells = list(sellers_col.find({"parent_id": su_id}).sort("username"))
    seller_details = {}
    for sel in sells:
        lics = list(licenses_col.find({"seller": sel["username"]}).sort("key"))
        seller_details[sel["username"]] = {
            "active": sel.get("active", True),
            "licenses": lics
        }
    return render_template("view_super.html", super_username=super_username, sellers=sells, seller_details=seller_details)

# Super Panel
@app.route('/super/login', methods=['POST'])
def super_login():
    username = request.form['username'].strip().upper()
    password = request.form['password'].strip()
    super_user = supers_col.find_one({"username": username, "password": password, "active": True})
    if super_user:
        session['role'] = 'super'
        session['super'] = username
        session['super_id'] = str(super_user['_id'])
        session['admin_id'] = str(super_user['parent_id'])
        session['master_id'] = str(admins_col.find_one({"_id": ObjectId(session['admin_id'])})['parent_id'])
        return redirect('/super')
    return render_template("login.html", message="Invalid Super Credentials or deactivated")

@app.route('/super')
def super_panel():
    if session.get('role') != 'super':
        return redirect('/')
    super_id = ObjectId(session['super_id'])
    sellers = list(sellers_col.find({"parent_id": super_id}).sort("username"))
    seller_stats = {}
    total_sellers = len([s for s in sellers if s.get('active', True)])
    total_licenses = 0
    total_paid = 0
    now = datetime.now()
    for s in sellers:
        s_id = s['_id']
        lics = list(licenses_col.find({"seller": s["username"], "active": True}))
        num_licenses = len(lics)
        total_licenses += num_licenses
        paid_count = len([l for l in lics if l.get("paid", False)])
        renew_total = sum(l.get("renew_count", 0) for l in lics)
        num_billable = paid_count + renew_total
        total_paid += num_billable
        accepted_due = s.get("accepted_due", 0)
        pending_billable = max(num_billable - accepted_due, 0)
        rate = s.get("rate", 0)
        due_amount = pending_billable * rate
        due_date = s.get("due_date")
        hours_left = None
        if pending_billable > 0:
            if not due_date:
                due_date = now
                sellers_col.update_one({"_id": s_id}, {"$set": {"due_date": due_date}})
            hours_left = max(0, 24 - (now - due_date).total_seconds() / 3600)
        seller_stats[s["username"]] = {
            "num_licenses": num_licenses,
            "paid_licenses": num_billable,
            "rate": rate,
            "due_amount": due_amount,
            "hours_left": hours_left,
            "pending_billable": pending_billable
        }
    message = request.args.get("message")
    return render_template("super_panel.html", sellers=sellers, seller_stats=seller_stats,
                          total_sellers=total_sellers, total_licenses=total_licenses,
                          total_paid=total_paid, message=message)

@app.route('/super/create_seller', methods=['POST'])
def create_seller():
    if session.get('role') != 'super':
        return redirect('/')
    username = request.form['username'].strip().upper()
    password = request.form['password'].strip()
    rate = int(request.form.get('rate', 0))
    if sellers_col.find_one({"username": username, "parent_id": ObjectId(session['super_id'])}):
        return redirect('/super?message=Seller already exists')
    expiry = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
    sellers_col.insert_one({
        "username": username,
        "password": password,
        "parent_id": ObjectId(session['super_id']),
        "active": True,
        "accepted_due": 0,
        "rate": rate,
        "expiry": expiry
    })
    return redirect('/super?message=Seller created successfully')

@app.route('/super/delete_seller/<username>')
def delete_seller(username):
    if session.get('role') != 'super':
        return redirect('/')
    seller = sellers_col.find_one({"username": username, "parent_id": ObjectId(session['super_id'])})
    if not seller:
        return redirect('/super?message=Seller not found')
    licenses_col.delete_many({"seller": username})
    sellers_col.delete_one({"username": username})
    return redirect('/super?message=Seller and licenses deleted')

@app.route('/super/deactivate_seller/<username>')
def deactivate_seller(username):
    if session.get('role') != 'super':
        return redirect('/')
    seller = sellers_col.find_one({"username": username, "parent_id": ObjectId(session['super_id'])})
    if not seller:
        return redirect('/super?message=Seller not found')
    sellers_col.update_one({"username": username}, {"$set": {"active": False}})
    licenses_col.update_many({"seller": username}, {"$set": {"active": False}})
    return redirect('/super?message=Seller and keys deactivated')

@app.route('/super/activate_seller/<username>')
def activate_seller(username):
    if session.get('role') != 'super':
        return redirect('/')
    sellers_col.update_one({"username": username, "parent_id": ObjectId(session['super_id'])}, {"$set": {"active": True}})
    return redirect('/super?message=Seller activated')

@app.route('/super/change_password/<username>', methods=['POST'])
def change_seller_password(username):
    if session.get('role') != 'super':
        return redirect('/')
    new_password = request.form.get('new_password').strip()
    sellers_col.update_one({"username": username, "parent_id": ObjectId(session['super_id'])}, {"$set": {"password": new_password}})
    return redirect('/super?message=Password updated')

@app.route('/super/update_rate/<username>', methods=['POST'])
def update_seller_rate(username):
    if session.get('role') != 'super':
        return redirect('/')
    new_rate = int(request.form.get('new_rate', 0))
    sellers_col.update_one({"username": username, "parent_id": ObjectId(session['super_id'])}, {"$set": {"rate": new_rate}})
    return redirect('/super?message=Rate updated')

@app.route('/super/accept_due/<username>', methods=['POST'])
def accept_seller_due(username):
    if session.get('role') != 'super':
        return redirect('/')
    seller = sellers_col.find_one({"username": username, "parent_id": ObjectId(session['super_id'])})
    if not seller:
        return redirect('/super?message=Seller not found')
    total_billable = 0
    paid_licenses = list(licenses_col.find({"seller": username, "paid": True, "active": True}))
    total_billable += len(paid_licenses)
    all_licenses = list(licenses_col.find({"seller": username}))
    total_billable += sum(l.get("renew_count", 0) for l in all_licenses)
    accepted_due = seller.get("accepted_due", 0)
    pending_billable = max(total_billable - accepted_due, 0)
    if pending_billable > 0:
        sellers_col.update_one({"username": username, "parent_id": ObjectId(session['super_id'])},
                              {"$inc": {"accepted_due": pending_billable}, "$unset": {"due_date": ""}})
        return redirect(f"/super?message=Accepted {pending_billable} due(s) for {username}")
    return redirect(f"/super?message=No dues to accept for {username}")

# FIX #2: CORRECT ROUTE FOR MARK PAID
@app.route('/super/mark_paid/<key>')
def mark_license_paid(key):
    if session.get('role') != 'super':
        return redirect('/')
    
    print(f"[DEBUG] Attempting to mark license {key} as paid")
    
    license = licenses_col.find_one({"key": key})
    if not license:
        print(f"[DEBUG] License {key} not found")
        return redirect('/super?message=License not found')
    
    seller = sellers_col.find_one({"username": license["seller"], "parent_id": ObjectId(session['super_id'])})
    if not seller:
        print(f"[DEBUG] Seller not found for license {key}")
        return redirect('/super?message=Seller not found for this license')
    
    # Set due_date if not present
    due_date = seller.get("due_date")
    if not due_date:
        due_date = datetime.now()
        sellers_col.update_one({"username": license["seller"]}, {"$set": {"due_date": due_date}})
        print(f"[DEBUG] Set due_date for seller {license['seller']}")
    
    # Mark license as paid
    licenses_col.update_one({"key": key}, {"$set": {"paid": True}})
    print(f"[DEBUG] License {key} marked as paid successfully")
    
    return redirect(f"/super/view_seller/{license['seller']}?message=License marked as paid")

@app.route('/super/view_seller/<seller_username>')
def view_seller(seller_username):
    if session.get('role') != 'super':
        return redirect('/')
    seller = sellers_col.find_one({"username": seller_username, "parent_id": ObjectId(session['super_id'])})
    if not seller:
        return redirect('/super?message=Seller not found')
    licenses = list(licenses_col.find({"seller": seller_username}).sort("key"))
    message = request.args.get("message")
    return render_template("view_seller.html", seller_username=seller_username, licenses=licenses, message=message, seller=seller)

# Seller Panel
@app.route('/seller/login', methods=['POST'])
def seller_login():
    username = request.form['username'].strip().upper()
    password = request.form['password'].strip()
    print(f"[DEBUG] Seller login attempt: username={username}")

    seller = sellers_col.find_one({"username": username, "password": password, "active": True})
    print(f"[DEBUG] Seller found: {seller is not None}")

    if not seller:
        print(f"[DEBUG] Seller login failed for {username}: Invalid credentials or deactivated")
        return render_template("login.html", message="Invalid Seller Credentials or deactivated")

    # Check if parent_id exists in seller document
    if 'parent_id' not in seller:
        print(f"[DEBUG] Seller login failed for {username}: Missing parent_id in seller document")
        return render_template("login.html", message="Seller configuration error: Missing parent_id")

    session['role'] = 'seller'
    session['seller'] = username
    session['seller_id'] = str(seller['_id'])
    session['super_id'] = str(seller['parent_id'])

    # Verify Super document and its parent_id
    super_doc = supers_col.find_one({"_id": ObjectId(session['super_id'])})
    if not super_doc or 'parent_id' not in super_doc:
        print(f"[DEBUG] Seller login failed for {username}: Invalid or missing Super parent_id")
        return render_template("login.html", message="Seller configuration error: Invalid Super hierarchy")

    session['admin_id'] = str(super_doc['parent_id'])

    # Verify Admin document and its parent_id
    admin_doc = admins_col.find_one({"_id": ObjectId(session['admin_id'])})
    if not admin_doc or 'parent_id' not in admin_doc:
        print(f"[DEBUG] Seller login failed for {username}: Invalid or missing Admin parent_id")
        return render_template("login.html", message="Seller configuration error: Invalid Admin hierarchy")

    session['master_id'] = str(admin_doc['parent_id'])

    print(f"[DEBUG] Seller login successful for {username}")
    return redirect('/seller')

@app.route('/seller')
def seller_panel():
    if session.get('role') != 'seller':
        return redirect('/')
    licenses = list(licenses_col.find({"seller": session['seller']}).sort("key"))
    license_stats = {}
    total_licenses = len([l for l in licenses if l.get('active', True)])
    total_paid = 0
    now = datetime.now()
    seller = sellers_col.find_one({"username": session['seller']})
    
    for l in licenses:
        paid_count = 1 if l.get("paid", False) and l.get("active", True) else 0
        renew_count = l.get("renew_count", 0)
        total_billable = paid_count + renew_count
        total_paid += total_billable
        expiry = l.get("expiry")
        hours_left = None
        if expiry:
            expiry_date = datetime.strptime(expiry, '%Y-%m-%d')
            hours_left = max(0, (expiry_date - now).total_seconds() / 3600)
        license_stats[l["key"]] = {
            "paid_licenses": total_billable,
            "hours_left": hours_left
        }
    
    accepted_due = seller.get("accepted_due", 0)
    pending_billable = max(total_paid - accepted_due, 0)
    rate = seller.get("rate", 0)
    due_amount = pending_billable * rate
    due_date = seller.get("due_date")
    hours_left_seller = None
    if pending_billable > 0 and due_date:
        due_date = due_date if isinstance(due_date, datetime) else datetime.strptime(due_date, '%Y-%m-%d %H:%M:%S.%f')
        hours_left_seller = max(0, 24 - (now - due_date).total_seconds() / 3600)
    
    message = request.args.get('message')
    return render_template("seller_panel.html", licenses=licenses, license_stats=license_stats,
                          total_licenses=total_licenses, total_paid=total_paid, message=message,
                          due_amount=due_amount, hours_left_seller=hours_left_seller, now=now)

@app.route('/seller/create_license', methods=['POST'])
def create_license():
    if session.get('role') != 'seller':
        return redirect('/')
    requested_key = request.form.get('license_key', '').strip().upper()
    rate = int(request.form.get('rate', 0))
    if not requested_key or licenses_col.find_one({"key": requested_key}):
        return redirect('/seller?message=License key already exists or empty.')
    expiry = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
    licenses_col.insert_one({
        "key": requested_key,
        "seller": session['seller'],
        "parent_id": ObjectId(session['seller_id']),
        "mac": "",
        "expiry": expiry,
        "active": True,
        "plan": "Basic",
        "paid": False,
        "created_at": datetime.now(),
        "rate": rate,
        "renew_count": 0
    })
    return redirect('/seller?message=License created successfully')

@app.route('/seller/delete_license/<key>')
def delete_license(key):
    if session.get('role') != 'seller':
        return redirect('/')
    licenses_col.delete_one({"key": key, "seller": session['seller']})
    return redirect('/seller?message=License deleted')

@app.route('/seller/reset_license/<key>')
def reset_license(key):
    if session.get('role') != 'seller':
        return redirect('/')
    licenses_col.update_one({"key": key, "seller": session['seller']}, {"$set": {"mac": ""}})
    return redirect('/seller?message=License reset successfully')

@app.route('/seller/renew_license/<key>')
def renew_license(key):
    if session.get('role') != 'seller':
        return redirect('/')
    new_expiry = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
    seller = sellers_col.find_one({"username": session['seller']})
    due_date = seller.get("due_date")
    if not due_date:
        due_date = datetime.now()
        sellers_col.update_one({"username": session['seller']}, {"$set": {"due_date": due_date}})
    licenses_col.update_one({"key": key, "seller": session['seller']},
                           {"$set": {"expiry": new_expiry}, "$inc": {"renew_count": 1}})
    return redirect('/seller?message=License renewed')

@app.route('/seller/activate_license/<key>')
def activate_license(key):
    if session.get('role') != 'seller':
        return redirect('/')
    licenses_col.update_one({"key": key, "seller": session['seller']}, {"$set": {"active": True}})
    return redirect('/seller?message=License activated successfully')

@app.route('/seller/deactivate_license/<key>')
def deactivate_license(key):
    if session.get('role') != 'seller':
        return redirect('/')
    licenses_col.update_one({"key": key, "seller": session['seller']}, {"$set": {"active": False}})
    return redirect('/seller?message=License deactivated successfully')

# User Panel
@app.route('/user', methods=['POST'])
def user_login():
    key = request.form['license_key'].strip().upper()
    mac = get_mac_address()
    lic = licenses_col.find_one({"key": key, "active": True})
    if not lic:
        return render_template("login.html", message="Invalid license key or deactivated.")
    if not lic.get("paid"):
        return render_template("login.html", message="License key is unpaid. Contact seller.")
    session['role'] = 'user'
    session['user'] = key
    if lic["mac"] == "" or lic["mac"] == mac:
        licenses_col.update_one({"key": key}, {"$set": {"mac": mac}})
        return redirect('/user/dashboard')
    return redirect('/user/dashboard?message=This license is bound to another device. Reset it if this is your system.')

@app.route('/user/dashboard')
def user_dashboard():
    if session.get('role') != 'user':
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
    if session.get('role') != 'user':
        return redirect('/')
    licenses_col.update_one({"key": session['user']}, {"$set": {"mac": ""}})
    return redirect('/user/dashboard?message=License reset successfully')

# API Routes
@app.route('/validate_license', methods=['POST'])
def validate_license():
    try:
        data = request.get_json()
        license_key = data.get('UserName', '').strip().upper()
        mac_address = data.get('MacAddress', '').strip()
        
        print(f"[DEBUG] Validate License - Key: {license_key}, MAC: {mac_address}")
        
        if not license_key or not mac_address:
            return jsonify({"success": False, "message": "Missing license key or MAC address"}), 400
        
        lic = licenses_col.find_one({"key": license_key})
        if not lic:
            print(f"[DEBUG] License {license_key} not found")
            return jsonify({"success": False, "message": "License key not found"}), 404
        
        if not lic.get("active", True):
            print(f"[DEBUG] License {license_key} is deactivated")
            return jsonify({"success": False, "message": "License is deactivated"}), 400
        
        if not lic.get("paid", False):
            print(f"[DEBUG] License {license_key} is unpaid")
            return jsonify({"success": False, "message": "License is unpaid"}), 400
        
        expiry_str = lic.get("expiry")
        days_left = calculate_left_days(expiry_str)
        
        if days_left < 0:
            print(f"[DEBUG] License {license_key} expired")
            return jsonify({"success": False, "message": "License expired"}), 400
        
        stored_mac = lic.get("mac", "")
        if stored_mac == "" or stored_mac == mac_address:
            licenses_col.update_one({"key": license_key}, {"$set": {"mac": mac_address}})
            plan = lic.get("plan", "Basic")
            print(f"[DEBUG] License {license_key} validated successfully - Days left: {days_left}, Plan: {plan}")
            return jsonify({
                "success": True, 
                "leftDays": days_left, 
                "plan": plan
            }), 200
        else:
            print(f"[DEBUG] License {license_key} bound to another device")
            return jsonify({"success": False, "message": "License bound to another device"}), 400
            
    except Exception as e:
        print(f"[ERROR] Validate License Exception: {e}")
        return jsonify({"success": False, "message": "Internal server error"}), 500

# FIX #1: CORRECTED /license/check ENDPOINT
@app.route('/license/check', methods=['POST'])
def license_check():
    try:
        data = request.get_json() or {}
        
        # Support multiple field names
        license_key = (data.get('licenseKey') or 
                      data.get('UserName') or 
                      data.get('Key') or 
                      data.get('license_key') or '').strip().upper()
        
        device_id = (data.get('deviceId') or 
                    data.get('MacAddress') or 
                    data.get('Machine') or 
                    data.get('mac_address') or '').strip()
        
        print(f"[DEBUG] License Check - Key: {license_key}, Device: {device_id}")
        print(f"[DEBUG] Raw request data: {data}")
        
        if not license_key or not device_id:
            return jsonify({
                'success': False, 
                'message': 'Missing key or device ID.'
            }), 400
        
        lic = licenses_col.find_one({"key": license_key})
        if not lic:
            print(f"[DEBUG] License {license_key} not found in database")
            return jsonify({
                'success': False, 
                'message': 'License key not found.'
            }), 404
        
        if not lic.get('active', True):
            print(f"[DEBUG] License {license_key} is deactivated")
            return jsonify({
                'success': False, 
                'message': 'License is deactivated.'
            }), 400
        
        if not lic.get('paid', False):
            print(f"[DEBUG] License {license_key} is unpaid")
            return jsonify({
                'success': False, 
                'message': 'License is unpaid.'
            }), 400
        
        expiry_str = lic.get('expiry')
        days_left = calculate_left_days(expiry_str)
        
        if days_left < 0:
            print(f"[DEBUG] License {license_key} expired")
            return jsonify({
                'success': False, 
                'message': 'License expired.'
            }), 400
        
        stored_mac = lic.get('mac', '')
        if stored_mac == '' or stored_mac == device_id:
            # Bind or verify device
            licenses_col.update_one(
                {"key": license_key}, 
                {"$set": {"mac": device_id}}
            )
            
            plan = lic.get('plan', 'Basic')
            mode = 'UPSTREAM-VALID' if plan == 'Basic' else f'PREMIUM-{plan}'
            
            print(f"[DEBUG] License {license_key} validated - Days: {days_left}, Plan: {plan}")
            
            return jsonify({
                'success': True,
                'leftDays': days_left,
                'mode': mode,
                'plan': plan,
                'message': f'License OK for {plan} plan.',
                'raw': {
                    'device': device_id, 
                    'expiry': expiry_str
                }
            }), 200
        else:
            print(f"[DEBUG] License {license_key} bound to different device: {stored_mac}")
            return jsonify({
                'success': False, 
                'message': 'License bound to another device.'
            }), 400
            
    except Exception as e:
        print(f'[ERROR] License Check Exception: {e}')
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False, 
            'message': 'Internal error.'
        }), 500

# Screenshot API
@app.route('/api/upload_screenshot', methods=['POST'])
def upload_screenshot():
    print("[DEBUG] Upload screenshot request received")
    if 'screenshot' not in request.files:
        print("[DEBUG] No file part")
        return jsonify({"success": False, "message": "No file part"}), 400
    file = request.files['screenshot']
    if file.filename == '':
        print("[DEBUG] No selected file")
        return jsonify({"success": False, "message": "No selected file"}), 400
    file_data = file.read()
    base64_image = base64.b64encode(file_data).decode('utf-8')
    filename = file.filename
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    screenshot_data = {
        "filename": filename,
        "image_data": base64_image,
        "upload_time": datetime.now(),
        "upload_date": today,
        "size": len(file_data)
    }
    if session.get('seller'):
        screenshot_data["seller"] = session['seller']
        screenshot_data["parent_id"] = ObjectId(session['seller_id'])
    screenshots_col.insert_one(screenshot_data)
    print(f"[DEBUG] Stored screenshot: {filename}")
    return jsonify({"success": True, "message": "Screenshot uploaded"}), 200

@app.route('/api/today_screenshots')
def get_today_screenshots():
    if session.get('role') not in ['master', 'admin', 'super']:
        print("[DEBUG] No authorized session, returning 403")
        return jsonify({"success": False, "message": "Authorized access required"}), 403
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)
    query = {"upload_date": {"$gte": today, "$lt": tomorrow}}
    if session.get('role') == 'master':
        admin_ids = [doc['_id'] for doc in admins_col.find({"parent_id": ObjectId(session['master_id'])})]
        super_ids = [doc['_id'] for doc in supers_col.find({"parent_id": {"$in": admin_ids}})]
        seller_usernames = [doc["username"] for doc in sellers_col.find({"parent_id": {"$in": super_ids}})]
        query["seller"] = {"$in": seller_usernames}
    elif session.get('role') == 'admin':
        super_ids = [doc['_id'] for doc in supers_col.find({"parent_id": ObjectId(session['admin_id'])})]
        seller_usernames = [doc["username"] for doc in sellers_col.find({"parent_id": {"$in": super_ids}})]
        query["seller"] = {"$in": seller_usernames}
    elif session.get('role') == 'super':
        seller_usernames = [doc["username"] for doc in sellers_col.find({"parent_id": ObjectId(session['super_id'])})]
        query["seller"] = {"$in": seller_usernames}
    screenshots = list(screenshots_col.find(query).sort("upload_time", -1))
    print(f"[DEBUG] Found {len(screenshots)} screenshots for today")
    screenshot_list = []
    for s in screenshots:
        screenshot_list.append({
            "id": str(s["_id"]),
            "filename": s["filename"],
            "upload_time": s["upload_time"].strftime('%Y-%m-%d %H:%M:%S'),
            "image_data": f"data:image/png;base64,{s['image_data']}"
        })
    return jsonify({"success": True, "screenshots": screenshot_list})

@app.route('/api/download_today_screenshots')
def download_today_screenshots():
    if session.get('role') not in ['master', 'admin', 'super']:
        print("[DEBUG] No authorized session for PDF download, returning 403")
        return jsonify({"success": False, "message": "Authorized access required"}), 403
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)
    query = {"upload_date": {"$gte": today, "$lt": tomorrow}}
    if session.get('role') == 'master':
        admin_ids = [doc['_id'] for doc in admins_col.find({"parent_id": ObjectId(session['master_id'])})]
        super_ids = [doc['_id'] for doc in supers_col.find({"parent_id": {"$in": admin_ids}})]
        seller_usernames = [doc["username"] for doc in sellers_col.find({"parent_id": {"$in": super_ids}})]
        query["seller"] = {"$in": seller_usernames}
    elif session.get('role') == 'admin':
        super_ids = [doc['_id'] for doc in supers_col.find({"parent_id": ObjectId(session['admin_id'])})]
        seller_usernames = [doc["username"] for doc in sellers_col.find({"parent_id": {"$in": super_ids}})]
        query["seller"] = {"$in": seller_usernames}
    elif session.get('role') == 'super':
        seller_usernames = [doc["username"] for doc in sellers_col.find({"parent_id": ObjectId(session['super_id'])})]
        query["seller"] = {"$in": seller_usernames}
    screenshots = list(screenshots_col.find(query).sort("upload_time", -1))
    if not screenshots:
        print("[DEBUG] No screenshots found for PDF download")
        return jsonify({"success": False, "message": "No screenshots today"}), 404
    pdf_buffer = BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=letter)
    page_width, page_height = letter
    margin = 1
    img_spacing = 1
    images_per_row = 4
    max_img_width = (page_width - (images_per_row + 1) * margin) / images_per_row
    aspect = 0.75
    if screenshots:
        try:
            img_data = base64.b64decode(screenshots[0]['image_data'])
            img_io = BytesIO(img_data)
            img = Image.open(img_io)
            aspect = img.size[1] / float(img.size[0])
        except:
            aspect = 0.75
    max_img_height = max_img_width * aspect
    rows_per_page = int((page_height - 2 * margin) / (max_img_height + img_spacing))
    img_index = 0
    while img_index < len(screenshots):
        for row in range(rows_per_page):
            if img_index >= len(screenshots):
                break
            for col in range(images_per_row):
                if img_index >= len(screenshots):
                    break
                s = screenshots[img_index]
                try:
                    img_data = base64.b64decode(s['image_data'])
                    img_io = BytesIO(img_data)
                    img = Image.open(img_io)
                    img_reader = ImageReader(img_io)
                    img_width, img_height = img.size
                    aspect = img_height / float(img_width)
                    draw_width = max_img_width
                    draw_height = draw_width * aspect
                    if draw_height > max_img_height:
                        draw_height = max_img_height
                        draw_width = draw_height / aspect
                    x_position = margin + col * (max_img_width + img_spacing)
                    y_position = page_height - margin - row * (max_img_height + img_spacing) - draw_height
                    c.drawImage(img_reader, x_position, y_position, width=draw_width, height=draw_height)
                except Exception as e:
                    print(f"[DEBUG] Error adding image to PDF: {e}")
                    continue
                img_index += 1
        if img_index < len(screenshots):
            c.showPage()
    c.save()
    pdf_buffer.seek(0)
    print("[DEBUG] Sending PDF with", len(screenshots), "screenshots")
    return send_file(pdf_buffer, as_attachment=True, download_name=f"screenshots_{datetime.now().strftime('%Y%m%d')}.pdf", mimetype='application/pdf')

# Logout
@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

if __name__ == '__main__':
    app.run(debug=True, port=5000)