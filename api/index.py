import os
import sys
import json
import uuid
import random
import hashlib
from datetime import datetime
from io import BytesIO
import sqlite3
import qrcode
from PIL import Image

# --- PAD FIX ---
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from flask import Flask, request, render_template, jsonify, send_file, abort

app = Flask(__name__,
            static_folder=os.path.join(BASE_DIR, 'static'),
            template_folder=os.path.join(BASE_DIR, 'templates'))

# --- DATABASE (Vercel /tmp) ---
DB_PATH = '/tmp/bingo.db'

def init_db():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS cards
                       (id TEXT PRIMARY KEY, data TEXT, card_hash TEXT,
                        registered INTEGER DEFAULT 0, player_name TEXT, player_email TEXT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS drawn_numbers
                       (number INTEGER, drawn_at TEXT DEFAULT (datetime('now')))''')
        conn.commit()
    except Exception as e:
        print("DB init error:", e)
    finally:
        conn.close()

# Roep aan bij import
init_db()

# --- CARD GENERATOR ---
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

def create_pdf(card_id, cols, base_url):
    buffer = BytesIO()
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        p = canvas.Canvas(buffer, pagesize=letter)
        w, h = letter
        p.setFont("Helvetica-Bold", 16)
        p.drawCentredString(w/2, h - inch, f"BINGO - {card_id}")

        for i, c in enumerate('BINGO'):
            p.setFont("Helvetica-Bold", 14)
            p.drawCentredString((i+1)*w/6, h - 1.5*inch, c)

        cell = w/6
        y = h - 2*inch
        for r in range(5):
            for ci, c in enumerate('BINGO'):
                x = w/12 + ci*cell
                val = cols[c][r]
                txt = "FREE" if val == "FREE" else str(val)
                p.setFont("Helvetica", 12)
                p.drawCentredString(x + cell/2, y - r*0.8*inch, txt)
                p.rect(x, y - (r+1)*0.8*inch, cell, 0.8*inch)

        qr = qrcode.QRCode(box_size=8, border=4)
        qr.add_data(f"{base_url}/verify/{card_id}")
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img_byte = BytesIO()
        img.save(img_byte, 'PNG')
        p.drawImage(BytesIO(img_byte.getvalue()), w - 2*inch, h - 3*inch, inch, inch)

        p.save()
        buffer.seek(0)
        return buffer.getvalue()
    except Exception as e:
        print("PDF error:", e)
        return None

# --- BINGO CHECK ---
def check_bingo(card_data, drawn):
    try:
        grid = json.loads(card_data)
        matrix = [[grid[c][r] for c in 'BINGO'] for r in range(5)]
        drawn_set = set(drawn) - {"FREE"}
        for row in matrix:
            if all(x in drawn_set or x == "FREE" for x in row): return True
        for c in range(5):
            if all(matrix[r][c] in drawn_set or matrix[r][c] == "FREE" for r in range(5)): return True
        if all(matrix[i][i] in drawn_set or matrix[i][i] == "FREE" for i in range(5)): return True
        if all(matrix[i][4-i] in drawn_set or matrix[i][4-i] == "FREE" for i in range(5)): return True
    except:
        pass
    return False

# --- ROUTES ---
@app.route('/')
def root():
    return app.send_static_file('admin.html')

@app.route('/index.html')
def player():
    return app.send_static_file('index.html')

@app.route('/generate_card')
def generate_card():
    base_url = request.host_url.rstrip('/')
    card_id, cols, card_hash = generate_bingo_card()
    pdf_data = create_pdf(card_id, cols, base_url)
    if not pdf_data:
        return "PDF generatie mislukt", 500

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("INSERT OR REPLACE INTO cards (id, data, card_hash) VALUES (?,?,?)",
                     (card_id, json.dumps(cols), card_hash))
        conn.commit()
    except Exception as e:
        print("DB insert error:", e)
    finally:
        conn.close()

    return send_file(BytesIO(pdf_data), download_name=f"{card_id}.pdf", mimetype='application/pdf')

@app.route('/register/<card_id>', methods=['GET', 'POST'])
def register(card_id):
    if request.method == 'GET':
        return f'''
        <form method="post" style="text-align:center;margin:3rem;">
          <h2>Registreer kaart {card_id}</h2>
          <input name="name" placeholder="Naam" required><br><br>
          <input name="email" placeholder="E-mail"><br><br>
          <button type="submit">Registreer</button>
        </form>
        '''
    name = request.form.get('name', '')
    email = request.form.get('email', '')
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("UPDATE cards SET registered=1, player_name=?, player_email=? WHERE id=?",
                     (name, email, card_id))
        conn.commit()
    except Exception as e:
        print("Register error:", e)
    finally:
        conn.close()
    return f'<h2>Geregistreerd! <a href="/">admin</a> | <a href="/verify/{card_id}">verify</a></h2>'

@app.route('/verify/<card_id>')
def verify(card_id):
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT data, card_hash FROM cards WHERE id=?", (card_id,)).fetchone()
        if not row:
            return "Kaart niet gevonden", 404
        data, stored_hash = row
        cols = json.loads(data)
        cur_hash = hashlib.sha256(f"{card_id}:{json.dumps(cols,sort_keys=True)}".encode()).hexdigest()
        valid = cur_hash == stored_hash
        drawn = [r[0] for r in conn.execute("SELECT number FROM drawn_numbers ORDER BY drawn_at")]
    except Exception as e:
        print("Verify error:", e)
        return "Fout bij ophalen kaart", 500
    finally:
        conn.close()
    return render_template('verify.html', card_id=card_id, columns=cols, drawn=drawn,
                           valid=valid, hash_stored=stored_hash, hash_current=cur_hash)

@app.route('/status')
def status():
    conn = sqlite3.connect(DB_PATH)
    try:
        drawn = [r[0] for r in conn.execute("SELECT number FROM drawn_numbers ORDER BY drawn_at")]
    except:
        drawn = []
    finally:
        conn.close()
    return jsonify({"drawn": drawn})

@app.route('/draw', methods=['POST'])
def draw():
    try:
        num = int(request.json['number'])
        if not 1 <= num <= 75:
            return jsonify({"error": "Nummer moet 1-75 zijn"}), 400
    except:
        return jsonify({"error": "Ongeldig nummer"}), 400

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("INSERT INTO drawn_numbers (number) VALUES (?)", (num,))
        drawn = [r[0] for r in conn.execute("SELECT number FROM drawn_numbers ORDER BY drawn_at")]
        winners = []
        for row in conn.execute("SELECT id, data FROM cards WHERE registered=1"):
            if check_bingo(row[1], drawn):
                winners.append(row[0])
        conn.commit()
    except Exception as e:
        print("Draw error:", e)
        winners = []
        drawn = []
    finally:
        conn.close()
    return jsonify({"drawn": drawn, "winners": winners})

@app.route('/report')
def report():
    conn = sqlite3.connect(DB_PATH)
    try:
        drawn = [r[0] for r in conn.execute("SELECT number FROM drawn_numbers ORDER BY drawn_at")]
        winners = []
        for row in conn.execute("SELECT id, player_name, data FROM cards WHERE registered=1"):
            if check_bingo(row[2], drawn):
                winners.append({"id": row[0], "name": row[1] or row[0]})
    except:
        drawn, winners = [], []
    finally:
        conn.close()

    buffer = BytesIO()
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter
        p = canvas.Canvas(buffer, pagesize=letter)
        p.drawString(100,800,f"Bingo Rapport - {datetime.now():%Y-%m-%d %H:%M}")
        p.drawString(100,780,f"Getrokken: {', '.join(map(str,drawn))}")
        p.drawString(100,760,f"Winnaars: {', '.join(w['name'] for w in winners)}")
        final_hash = hashlib.sha256(str(drawn + [w['id'] for w in winners]).encode()).hexdigest()
        p.drawString(100,720,f"Handtekening: {final_hash}")
        p.save()
        buffer.seek(0)
        return send_file(buffer, download_name="rapport.pdf", mimetype='application/pdf')
    except Exception as e:
        print("Report error:", e)
        return "Rapport generatie mislukt", 500

# --- VERCEL HANDLER (CRUCIAAL!) ---
@app.errorhandler(Exception)
def handle_error(e):
    print("Unhandled error:", e)
    return "Serverfout", 500

def handler(event, context):
    """Vercel serverless entry point"""
    from werkzeug.wsgi import get_environ
    environ = get_environ(event)
    response = app(environ, lambda s, h: None)
    return response

# Lokaal testen
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
