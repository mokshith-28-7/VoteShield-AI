from flask import Flask, request, jsonify, render_template, session
from flask_socketio import SocketIO
from flask_cors import CORS
from pymongo import MongoClient
from dotenv import load_dotenv
from flask import redirect
import pytesseract
import base64
#import face_recognition
import re
import os
import cv2
import numpy as np
import random
from datetime import datetime, timedelta

# Load environment variables
load_dotenv()

otp_store = {}

app = Flask(__name__, template_folder='backend')
app.secret_key = os.getenv("SECRET_KEY", "fallback_secret")
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
voting_open = True 
CORS(app)

socketio = SocketIO(app, cors_allowed_origins="*")

app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SECURE'] = False  # True in production (HTTPS)

@app.after_request
def add_header(response):
    response.cache_control.no_store = True
    return response

# MongoDB Connection and Collections setup
mongo_uri = os.getenv("MONGO_URI")
client = MongoClient(mongo_uri)
db = client.get_database("VotingDB")

voters = db["voters"]
candidates_collection = db["candidates"]
votes_collection = db["votes"]
fraud_collection = db["fraud"]
settings = db["settings"]
users_col = db["users"]
elections_col = db["elections"]

votes_collection.create_index("voter_id", unique=True)
voters.create_index("username", unique=True)

settings.update_one({}, {"$set": {"voting_open": True}}, upsert=True)


# -------------------------------
# 🟢 Default Route
# -------------------------------

# -------------------------------
# 🟢 Admin Login
# -------------------------------
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "1234"
officers = {
    "officer1": "1234"
}

@app.route('/officer-login', methods=['POST'])
def officer_login():
    data = request.json
    username = data.get("username")
    password = data.get("password")

    if officers.get(username) == password:
        session["officer_logged_in"] = True   # 🔥 ADD THIS
        return jsonify({"success": True})
    else:
        return jsonify({"success": False})

# -------------------------------
# 🟢 Serve Pages
# -------------------------------

@app.route('/')
def index_page():
    return render_template('index.html')

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route("/admin-login", methods=["POST"])
def admin_login():
    data = request.json
    username = data.get("username")
    password = data.get("password")
    # Replace with real admin creds or db check
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        session["admin_logged_in"] = True
        return jsonify({"success": True})
    return jsonify({"success": False})

# -------------------------------
# 🟢 Add Candidate
# -------------------------------
@app.route('/add-candidate', methods=['POST'])
def add_candidate():
    socketio.emit('candidate_update', {"action": "added"})
    if not (session.get("officer_logged_in") or session.get("admin_logged_in")):
        return jsonify({"message": "Unauthorized"}), 403

    data = request.json
    name = data.get("name")
    party = data.get("party")
    socketio.emit("updateResults")

    if not name or not party:
        return jsonify({"message": "Missing data"}), 400

    try:
        candidates_collection.insert_one({"name": name, "party": party, "votes": 0})
        return jsonify({"success": True})
    except Exception:
        return jsonify({"success": False, "message": "DB error"})

# -------------------------------
# 🟢 Get Candidates
# -------------------------------
@app.route('/get-candidates', methods=['GET'])
def get_candidates():
    candidates = list(candidates_collection.find({}, {"_id": 0}))
    return jsonify(candidates)

@app.route('/delete-candidate', methods=['POST'])
def delete_candidate():
    socketio.emit('candidate_update', {"action": "deleted"})
    if not (session.get("officer_logged_in") or session.get("admin_logged_in")):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    data = request.json
    name = data.get("name")
    socketio.emit("updateResults")

    candidates_collection.delete_one({'name': name})

    return jsonify({'success': True})

# -------------------------------
# 🟢 Voters Management (New 🔥)
# -------------------------------
@app.route('/get-voters', methods=['GET'])
def get_voters():
    if not (session.get("officer_logged_in") or session.get("admin_logged_in")):
        return jsonify([]), 403
    v_list = list(voters.find({}, {"_id": 0, "face_encoding": 0}))
    return jsonify(v_list)

@app.route('/delete-voter', methods=['POST'])
def delete_voter():
    if not (session.get("officer_logged_in") or session.get("admin_logged_in")):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    data = request.json
    username = data.get("username")
    if not username:
        return jsonify({'success': False, 'message': 'Missing username'}), 400

    voters.delete_one({'username': username})
    # also remove any corresponding cast vote to allow re-voting if deleted
    votes_collection.delete_one({'voter_id': username})
    socketio.emit("updateResults")
    return jsonify({'success': True})


# -------------------------------
# 🟢 Verify Voter
# -------------------------------


@app.route('/verify-otp', methods=['POST'])
def verify_otp():
    data = request.json

    username = data['voterId']
    otp = data['otp']

    if username in otp_store and otp_store[username] == otp:
        del otp_store[username]   # 🔥 REMOVE OTP AFTER USE
        return jsonify({'success': True})
    else:
        return jsonify({'success': False})
    
@app.route('/verify-face', methods=['POST'])
def verify_face():
    return jsonify({
        "success": True,
        "match": 100
    })
    
@app.route('/scan-aadhaar', methods=['POST'])
def scan_aadhaar():
    try:
        if 'file' not in request.files:
            return jsonify({"error": "No file uploaded in this request"}), 400
        file = request.files['file']
        img_bytes = file.read()
        if not img_bytes:
            return jsonify({"error": "Empty Aadhaar card image file submitted"}), 400

        # 🔥 Convert image for OpenCV
        img = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return jsonify({"error": "Could not decode image file format."}), 400

        # 🔥 Preprocessing (VERY IMPORTANT)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.bilateralFilter(gray, 11, 17, 17)
        _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)

        # 🔥 OCR
        try:
            ocr_text = pytesseract.image_to_string(thresh)
        except Exception as ocr_err:
            print("Tesseract OCR exception captured:", ocr_err)
            ocr_text = ""  # fallback if Tesseract binary is not installed locally

        print("OCR TEXT:\n", ocr_text)

        # ----------------------------
        # ✅ NAME DETECTION (FIXED)
        # Aadhaar name is usually 2-3 capital words
        # ----------------------------
        name = ""
        name_match = re.findall(r'\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)+\b', ocr_text)

        if name_match:
            name = name_match[0]   # take first valid full name

        # ----------------------------
        # ✅ DOB DETECTION (FIXED)
        # Handles multiple formats
        # ----------------------------
        dob = ""
        dob_patterns = [
            r'\d{2}/\d{2}/\d{4}',
            r'\d{2}-\d{2}-\d{4}',
            r'Year of Birth\s*:\s*(\d{4})'
        ]

        for pattern in dob_patterns:
            match = re.search(pattern, ocr_text)
            if match:
                dob = match.group(0)
                break

        # ----------------------------
        # ✅ AADHAAR NUMBER (NEW 🔥)
        # ----------------------------
        aadhaar = ""
        aadhaar_match = re.search(r'\d{4}\s\d{4}\s\d{4}', ocr_text)

        if aadhaar_match:
            aadhaar = aadhaar_match.group(0).replace(" ", "")

        district = ""

        # try simple district detection (example words)
        district_match = re.search(r'District[:\-]?\s*([A-Za-z ]+)', ocr_text)
        if district_match:
            district = district_match.group(1)

        states = [
            "Andhra Pradesh","Telangana","Tamil Nadu","Karnataka",
            "Maharashtra","Delhi","Gujarat","Rajasthan","Uttar Pradesh"
        ]

        found_state = ""
        for s in states:
            if s.lower() in ocr_text.lower():
                found_state = s
                break

        # ✅ FINAL LOCATION FIX
        location = district.strip() if district else found_state.strip()

        return jsonify({
            "name": name,
            "dob": dob,
            "aadhaar": aadhaar,
            "district": district,
            "state": found_state,
            "location": location
        })
    except Exception as e:
        return jsonify({"error": f"Aadhaar scanning failed: {str(e)}"}), 500

# -------------------------------
# 🟢 Cast Vote
# -------------------------------

# -------------------------------
# 🟢 Results
# -------------------------------
@app.route('/results', methods=['GET'])
def results():
    candidates = list(candidates_collection.find({}, {"_id": 0}))

    total_votes = sum(c.get('votes', 0) for c in candidates)

    for c in candidates:
        c['votes'] = c.get('votes', 0)   # ✅ ADD THIS

        if total_votes > 0:
            c['percentage'] = round((c['votes'] / total_votes) * 100, 2)
        else:
            c['percentage'] = 0

    return jsonify(candidates)

@app.route('/cast-vote', methods=['POST'])
def cast_vote():
    global voting_open

    data = request.json
    voter_id = data.get('voter_id')
    candidate_name = data.get('candidate_name')

    # 🔐 SECURITY SHIELD: Verify session identity matches voter_id to block API fraud
    if session.get("voter_id") != voter_id:
        # Log unauthorized bypass attempt in fraud logs immediately
        fraud_collection.insert_one({
            "voter_id": voter_id or "Anonymous-API-Attacker",
            "risk_score": 100,
            "reason": "Session security bypass attempt detected (API spoofing).",
            "status": "blocked",
            "time": datetime.utcnow()
        })
        socketio.emit("fraudAlert")
        return jsonify({'success': False, 'message': 'Ballot security bypass detected. This incident has been logged.'}), 403

    voter = voters.find_one({'username': voter_id})

    if not voter:
        return jsonify({'success': False, 'message': 'Voter record not found.'})

    risk_score = 0

    # Rule 1: multiple votes
    if votes_collection.find_one({"voter_id": voter_id}):
        risk_score += 70

    # Rule 2: already voted
    if voter.get('has_voted'):
        risk_score += 50

    # Rule 3: random anomaly simulation
    if random.random() > 0.85:
        risk_score += 30

    # Save fraud log if risky
    if risk_score > 50:
        fraud_collection.insert_one({
            "voter_id": voter_id,
            "risk_score": risk_score,
            "reason": "High fraud risk indicators.",
            "status": "flagged",
            "time": datetime.utcnow()
        })
        socketio.emit("fraudAlert")
        
    # 🚫 BLOCK HIGH RISK USERS
    if risk_score >= 80:
        fraud_collection.insert_one({
            "voter_id": voter_id,
            "risk_score": risk_score,
            "reason": "Electoral fraud signature limit exceeded.",
            "status": "blocked",
            "time": datetime.utcnow()
        })
        socketio.emit("fraudAlert")
        return jsonify({
            'success': False,
            'message': 'Electoral AI blocked this ballot submission due to elevated fraud risk.'
        })

    if not voting_open:
        return jsonify({'success': False, 'message': 'Voting closed'})

    # 🚨 FRAUD CHECK FIRST
    existing_vote = votes_collection.find_one({"voter_id": voter_id})

    if existing_vote:
        fraud_collection.insert_one({
            "voter_id": voter_id,
            "risk_score": 100,
            "reason": "Multiple voting attempt",
            "status": "flagged",
            "time": datetime.utcnow()
        })
        socketio.emit("fraudAlert")
        return jsonify({'success': False, 'message': 'Fraud detected: Multiple voting attempt!'})

    # ALSO CHECK has_voted
    if voter.get('has_voted', False):
        fraud_collection.insert_one({
            "voter_id": voter_id,
            "risk_score": 100,
            "reason": "Already voted flag bypass attempt",
            "status": "flagged",
            "time": datetime.utcnow()
        })
        socketio.emit("fraudAlert")
        return jsonify({'success': False, 'message': 'Electoral record states you have already voted.'})

    candidate = candidates_collection.find_one({'name': candidate_name})
    if not candidate:
        return jsonify({'success': False, 'message': 'Candidate not found'})

    # ✅ SAFE TO VOTE NOW
    candidates_collection.update_one(
        {'name': candidate_name},
        {'$inc': {'votes': 1}}
    )

    socketio.emit('updateResults')

    voters.update_one(
        {'username': voter_id},
        {'$set': {'has_voted': True}}
    )

    votes_collection.insert_one({
        "voter_id": voter_id,
        "candidate": candidate_name
    })

    return jsonify({'success': True})


@app.route("/admin")
def admin_page():
    print("SESSION:", session)  # 🔍 DEBUG
    if not session.get("admin_logged_in"):
        return redirect('/login')   # better UX
    return render_template("admin.html")

# -------------------------------
# 🟢 Register Voter
# -------------------------------

@app.route('/register-user', methods=['POST'])
def register_user():
    data = request.json

    username = data.get('username')
    dob = data.get('dob')
    phone = data.get('phone')
    aadhaar = data.get('aadhaar')
    face_image = data.get('face_image')

    if not username or not dob or not aadhaar:
        return jsonify({'success': False, 'message': 'Missing required fields'})

    existing = voters.find_one({'username': username})
    if existing:
        return jsonify({'success': False, 'message': 'User already exists'})

    encoding = []

    voters.insert_one({
        'username': username,
        'dob': dob,
        'phone': phone,
        'aadhaar': aadhaar,
        'district': data.get("district", ""),
        'state': data.get("state", ""),
        'face_encoding': encoding,
        'has_voted': False,
        'created_at': datetime.utcnow()
    })

    return jsonify({'success': True})

@app.route('/register-face', methods=['POST'])
def register_face():
    return jsonify({"success": True})

@app.route('/login-user', methods=['POST'])
def login_user():
    data = request.json

    username = data['username']
    dob = data['dob']

    user = voters.find_one({'username': username, 'dob': dob})

    if not user:
        return jsonify({'success': False, 'message': 'Invalid details'})

    otp = str(random.randint(100000, 999999))
    otp_store[username] = otp

    print("OTP for", username, ":", otp)

    return jsonify({'success': True,"otp": otp})

@app.route('/admin-status')
def admin_status():
    return jsonify({
        "voting_open": voting_open
    })

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# Add election
@app.route("/add-election", methods=["POST"])
def add_election():
    if not session.get("admin_logged_in"):
        return jsonify({"success": False, "message": "Unauthorized"})
    data = request.json
    name = data.get("name")
    status = data.get("status")  # live, upcoming, scheduled

    if status not in ["live", "upcoming", "scheduled"]:
        return jsonify({"success": False, "message": "Invalid status"})
    elections_col.insert_one({
        "name": name,
        "status": status,
        "start_time": None,
        "end_time": None,
        "created_at": datetime.utcnow()
    })
    return jsonify({"success": True})

# Get elections
@app.route("/get-elections")
def get_elections():
    if not session.get("admin_logged_in"):
        return jsonify([])
    elections = list(elections_col.find({}, {"_id":0}))
    return jsonify(elections)

@app.route("/delete-election", methods=["POST"])
def delete_election():
    if not session.get("admin_logged_in"):
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    data = request.json
    name = data.get("name")
    if not name:
        return jsonify({"success": False, "message": "Missing name"}), 400
    elections_col.delete_one({"name": name})
    return jsonify({"success": True})

@app.route('/reset-voting', methods=['POST'])
def reset_voting():
    if not session.get("admin_logged_in"):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    candidates_collection.update_many({}, {'$set': {'votes': 0}})
    votes_collection.delete_many({})
    voters.update_many({}, {'$set': {'has_voted': False}})

    return jsonify({'success': True})

@app.route('/fraud-alerts')
def fraud_alerts():
    alerts = list(fraud_collection.find().sort("time", -1).limit(5))
    
    for a in alerts:
        a["_id"] = str(a["_id"])
    
    return jsonify(alerts)

# Set election timer
@app.route("/set-timer", methods=["POST"])
def set_timer():
    if not session.get("admin_logged_in"):
        return jsonify({"success": False})
    data = request.json
    election_name = data.get("name")
    duration_minutes = int(data.get("duration"))
    end_time = datetime.utcnow() + timedelta(minutes=duration_minutes)
    elections_col.update_one({"name":election_name}, {"$set":{"end_time": end_time}})
    return jsonify({"success": True, "message": f"Timer set for {duration_minutes} mins"})

# Get dashboard stats
@app.route("/admin-stats")
def admin_stats():
    if not session.get("admin_logged_in"):
        return jsonify({})
    total_voters = voters.count_documents({})
    total_candidates = candidates_collection.count_documents({})
    total_votes = votes_collection.count_documents({})
    recent_voters = list(voters.find().sort("created_at", -1).limit(5))
    
    # Sanitize recent voters to bypass ObjectId and datetime serialization errors
    sanitized_voters = []
    for v in recent_voters:
        sanitized_voters.append({
            "username": v.get("username"),
            "dob": v.get("dob"),
            "phone": v.get("phone"),
            "district": v.get("district"),
            "state": v.get("state"),
            "has_voted": v.get("has_voted"),
            "created_at": v["created_at"].isoformat() if v.get("created_at") else None
        })

    return jsonify({
        "total_voters": total_voters,
        "total_candidates": total_candidates,
        "total_votes": total_votes,
        "recent_voters": sanitized_voters
    })


@app.route('/stats')
def stats():
    global voting_open

    total_votes = votes_collection.count_documents({})
    total_voters = voters.count_documents({})
    fraud_count = fraud_collection.count_documents({"status": "flagged"})

    return jsonify({
        "total_votes": total_votes,
        "total_voters": total_voters,
        "fraud_count": fraud_count,
        "time_left": "OPEN" if voting_open else "CLOSED"
    })

@app.route('/winner')
def winner():
    winner_cursor = candidates_collection.find().sort("votes", -1).limit(1)
    winner_list = list(winner_cursor)

    if winner_list:
        win_cand = winner_list[0]
        return jsonify({
            "name": win_cand["name"],
            "votes": win_cand["votes"]
        })
    return jsonify({}) 

@app.route('/recent-activity')
def recent_activity():
    activities = list(votes_collection.find().sort("_id", -1).limit(10))

    result = []

    for a in activities:
        voter = voters.find_one({"username": a["voter_id"]})

        location = "Unknown"
        if voter:
            location = voter.get("district") or voter.get("state") or "Unknown"

        result.append({
            "voter_id": a["voter_id"],
            "time": str(a["_id"].generation_time),
            "district": location
        })

    return jsonify(result)

@app.route('/toggle-voting', methods=['POST'])
def toggle_voting():
    global voting_open

    if not session.get("admin_logged_in"):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403

    voting_open = not voting_open

    return jsonify({'success': True, 'status': voting_open})

# -------------------------------
# ▶ Run Server
# -------------------------------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
