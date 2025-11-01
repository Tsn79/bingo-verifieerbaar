import os
import sys
import json
from flask import Flask, request, render_template, jsonify, send_file, Response
import sqlite3
import uuid
import random
import hashlib
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from io import BytesIO
import qrcode
from PIL import Image

# Voeg root toe voor imports in Vercel
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__, 
            static_folder='../static',      # Fix: Relatief naar root
            template_folder='../templates')  # Fix: Relatief naar root

# --- DATABASE (SQLite in /tmp voor Vercel serverless) ---
DB_PATH = '/tmp/bingo.db'  # Vercel: Gebruik temp dir, anders persistentie issues

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS cards
                 (id TEXT PRIMARY KEY, data TEXT, card_hash TEXT, registered INTEGER DEFAULT 0,
                  player_name TEXT, player_email TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS drawn_numbers
                 (number INTEGER, drawn_at TEXT DEFAULT (datetime('now')))''')
    conn.commit()
    conn.close()

init_db()

# --- CARD GENERATOR (rest hetzelfde als voorheen) ---
def generate_bingo_card():
    card_id = str(uuid.uuid4())[:8].upper()
    cols = {
        'B': sorted(random.sample(range(1, 16), 5)),
        'I': sorted(random.sample(range(16, 31), 5)),
        'N': sorted(random.sample(range(31, 46), 5)),
        'G': sorted(random.sample(range(46, 61), 5)),
        'O': sorted(random.sample(range(61, 76), 5)),
    }
    cols['N'][2] = "FREE"
    data_str = f"{card_id}:{json.dumps(cols, sort_keys=True)}"
    card_hash = hashlib.sha256(data_str.encode()).hexdigest()
    return card_id, cols, card_hash

def create_pdf(card_id, cols):
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    w, h = letter
    p.setFont("Helvetica-Bold", 16)
    p.drawCentredString(w/2, h - inch, f"BINGO - ID: {card_id}")

    # Headers
    for i, col in enumerate(['B','I','N','G','O']):
        p.setFont("Helvetica-Bold", 14)
        p.drawCentredString((i+1)*w/6, h - 1.5*inch, col)

    # Grid
    cell = w / 6
    y = h - 2*inch
    for r in range(5):
        for c_idx, col in enumerate(['B','I','N','G','O']):
            x = w/12 + c_idx * cell
            val = cols[col][r]
            txt = "FREE" if val == "FREE" else str(val)
            p.setFont("Helvetica", 12)
            p.drawCentredString(x + cell/2, y - r*0.8*inch, txt)
            p.rect(x, y - (r+1)*0.8*inch, cell, 0.8*inch)

    # QR Code (update met je Vercel URL)
    qr = qrcode.QRCode(box_size=8, border=4)
    qr.add_data(f"https://jouw-app.vercel.app/verify/{card_id}")  # ← Vervang 'jouw-app' met je echte URL
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img_byte = BytesIO()
    img.save(img_byte, 'PNG')
    p.drawImage(BytesIO(img_byte.getvalue()), w - 2*inch, h - 3*inch, inch, inch)

    p.save()
    buffer.seek(0)
    return buffer.getvalue()

# --- BINGO CHECK (onveranderd) ---
def check_bingo(card_data, drawn):
    grid = json.loads(card_data)
    matrix = [[grid[c][r] for c in 'BINGO'] for r in range(5)]
    drawn_set = set(drawn) - {"FREE"}
    for row in matrix:
        if all(x in drawn_set or x == "FREE" for x in row): return True
    for c in range(5):
        if all(matrix[r][c] in drawn_set or matrix[r][c] == "FREE" for r in range(5)): return True
    if all(matrix[i][i] in drawn_set or matrix[i][i] == "FREE" for i in range(5)): return True
    if all(matrix[i][4-i] in drawn_set or matrix[i][4-i] == "FREE" for i in range(5)): return True
    return False

# --- ROUTES (onveranderd, maar met DB_PATH) ---
@app.route('/')
def home():
    return render_template('admin.html')

@app.route('/generate_card')
def generate_card():
    card_id, cols, card_hash = generate_bingo_card()
    pdf = create_pdf(card_id, cols)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO cards (id, data, card_hash) VALUES (?, ?, ?)",
                 (card_id, json.dumps(cols), card_hash))
    conn.commit()
    conn.close()
    return send_file(BytesIO(pdf), download_name=f"{card_id}.pdf", mimetype='application/pdf')

@app.route('/register/<card_id>', methods=['GET', 'POST'])
def register(card_id):
    if request.method == 'GET':
        return '''
        <form method="post" style="text-align:center;margin-top:50px;">
          <h2>Registreer kaart {}</h2>
          <input name="name" placeholder="Naam" required><br><br>
          <input name="email" placeholder="Email" type="email"><br><br>
          <button type="submit">Registreer</button>
        </form>
        '''.format(card_id)
    name = request.form['name']
    email = request.form.get('email', '')
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE cards SET registered=1, player_name=?, player_email=? WHERE id=?", 
                 (name, email, card_id))
    conn.commit()
    conn.close()
    return "<h2>Geregistreerd! Ga naar <a href='/'>admin</a> of <a href='/verify/{}'>verify</a></h2>".format(card_id)

@app.route('/verify/<card_id>')
def verify(card_id):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT data, card_hash FROM cards WHERE id=?", (card_id,)).fetchone()
    if not row: return "Kaart niet gevonden", 404
    card_data, stored_hash = row
    cols = json.loads(card_data)
    current_hash = hashlib.sha256(f"{card_id}:{json.dumps(cols, sort_keys=True)}".encode()).hexdigest()
    valid = current_hash == stored_hash
    drawn = [r[0] for r in conn.execute("SELECT number FROM drawn_numbers ORDER BY drawn_at")]
    conn.close()
    return render_template('verify.html', card_id=card_id, columns=cols, drawn=drawn,
                           valid=valid, hash_stored=stored_hash, hash_current=current_hash)

@app.route('/status')
def status():
    conn = sqlite3.connect(DB_PATH)
    drawn = [r[0] for r in conn.execute("SELECT number FROM drawn_numbers ORDER BY drawn_at")]
    conn.close()
    return jsonify({"drawn": drawn})

@app.route('/draw', methods=['POST'])
def draw():
    number = int(request.json['number'])
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT INTO drawn_numbers (number) VALUES (?)", (number,))
    drawn = [r[0] for r in conn.execute("SELECT number FROM drawn_numbers ORDER BY drawn_at")]
    winners = []
    for row in conn.execute("SELECT id, data FROM cards WHERE registered=1"):
        if check_bingo(row[1], drawn):
            winners.append(row[0])
    conn.commit()
    conn.close()
    return jsonify({"drawn": drawn, "winners": winners})

@app.route('/report')
def report():
    conn = sqlite3.connect(DB_PATH)
    drawn = [r[0] for r in conn.execute("SELECT number FROM drawn_numbers ORDER BY drawn_at")]
    winners = []
    for row in conn.execute("SELECT id, player_name, data FROM cards WHERE registered=1"):
        if check_bingo(row[2], drawn):
            winners.append({"id": row[0], "name": row[1]})
    conn.close()
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    p.drawString(100, 800, f"Bingo Rapport - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    p.drawString(100, 780, f"Getrokken: {', '.join(map(str, drawn))}")
    p.drawString(100, 760, f"Winnaars: {', '.join([w['name'] or w['id'] for w in winners])}")
    final_hash = hashlib.sha256(str(drawn + [w['id'] for w in winners]).encode()).hexdigest()
    p.drawString(100, 720, f"Digitale handtekening: {final_hash}")
    p.save()
    buffer.seek(0)
    return send_file(buffer, download_name="bingo_rapport.pdf", mimetype='application/pdf')

# --- VERCEL HANDLER (belangrijk voor serverless) ---
def handler(req):
    # Simuleer Flask request
    from werkzeug.wrappers import Request
    req_obj = Request.from_environ(req)
    return app(req_obj.environ, lambda status, headers: None)

if __name__ == '__main__':
    app.run(debug=True)
