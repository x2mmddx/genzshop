import os
import uuid
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for
from flask_cors import CORS
from urllib.request import Request, urlopen
import json
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data.db')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')

app = Flask(__name__, static_url_path='', static_folder=BASE_DIR)
CORS(app, supports_credentials=True)

# Session / Auth config
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'change-this-in-production')
app.config.update(
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_HTTPONLY=True,
)

ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL')
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL')


def send_discord(content):
    try:
        url = DISCORD_WEBHOOK_URL
        if not url:
            return False
        data = json.dumps({"content": content}).encode('utf-8')
        headers = {"Content-Type": "application/json"}
        req = Request(url, data=data, headers=headers)
        with urlopen(req, timeout=10) as resp:
            _ = resp.read()
        return True
    except Exception:
        try:
            print("[discord] send failed", flush=True)
        except Exception:
            pass
        return False

# --- Database helpers ---

def get_db():
    conn = getattr(app, '_db_conn', None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        app._db_conn = conn
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            image TEXT,
            model_image TEXT,
            desc TEXT,
            color TEXT,
            sizes TEXT,
            season TEXT,
            type TEXT,
            status TEXT DEFAULT 'in'
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cart_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cart_id TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            size TEXT,
            color TEXT,
            quantity INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product TEXT,
            color TEXT,
            size TEXT,
            amount INTEGER DEFAULT 1,
            name TEXT,
            phone TEXT,
            gov TEXT,
            city TEXT,
            address TEXT,
            price REAL,
            shipping REAL,
            total REAL,
            addition TEXT,
            date TEXT
        )
        """
    )
    # Attempt to add columns if upgrading existing DB
    try:
        cur.execute('ALTER TABLE customers ADD COLUMN amount INTEGER DEFAULT 1')
    except Exception:
        pass
    try:
        cur.execute('ALTER TABLE products ADD COLUMN sizes TEXT')
    except Exception:
        pass
    try:
        cur.execute('ALTER TABLE products ADD COLUMN model_image TEXT')
    except Exception:
        pass
    conn.commit()


# Initialize DB on startup
init_db()

# Ensure uploads directory exists
try:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
except Exception:
    pass


# --- Cookie management (ensure cart_id) ---
@app.after_request
def ensure_cart_cookie(response):
    if 'cart_id' not in request.cookies:
        response.set_cookie(
            'cart_id', str(uuid.uuid4()), httponly=False, samesite='Lax'
        )
    return response


# --- Auth endpoints (simple) ---


@app.get('/api/me')
def api_me():
    return jsonify(session.get('user') or None)


# --- Static files (serve current directory) ---
@app.route('/')
def root():
    # Serve index.html if present, otherwise list static root
    index_path = os.path.join(BASE_DIR, 'index.html')
    if os.path.exists(index_path):
        return send_from_directory(BASE_DIR, 'index.html')
    return jsonify({"ok": True, "message": "API server running"})

@app.get('/api/test/discord')
def test_discord():
    ok = send_discord("Ping from server ✅")
    return jsonify({"ok": ok}), (200 if ok else 500)

@app.post('/api/upload')
def upload_files():
    try:
        files = request.files.getlist('files') or []
        saved_urls = []
        for f in files:
            try:
                ext = ''
                fn = f.filename or ''
                if '.' in fn:
                    ext = '.' + fn.rsplit('.', 1)[-1].lower()
                new_name = f"{uuid.uuid4().hex}{ext}"
                target_path = os.path.join(UPLOAD_DIR, new_name)
                f.save(target_path)
                saved_urls.append(f"/uploads/{new_name}")
            except Exception:
                continue
        return jsonify({"urls": saved_urls})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get('/uploads/<path:filename>')
def serve_upload(filename):
    try:
        return send_from_directory(UPLOAD_DIR, filename)
    except Exception as e:
        return jsonify({"error": str(e)}), 404

# Protect admin.html and customers.html by session
def _require_admin():
    return bool(session.get('admin'))


@app.get('/admin.html')
def serve_admin():
    if not _require_admin():
        return redirect(url_for('serve_login'))
    return send_from_directory(BASE_DIR, 'admin.html')


@app.get('/customers.html')
def serve_customers():
    if not _require_admin():
        return redirect(url_for('serve_login'))
    return send_from_directory(BASE_DIR, 'customers.html')


# --- Simple username/password auth (no Google) ---
@app.get('/login')
def serve_login():
    return send_from_directory(BASE_DIR, 'login.html')


@app.post('/login')
def do_login():
    data = request.get_json(silent=True) or {}
    if not data:
        data = request.form.to_dict()
    username = data.get('username') or ''
    password = data.get('password') or ''

    admin_user = os.environ.get('ADMIN_USER', 'admin')
    admin_pass = os.environ.get('ADMIN_PASS', 'admin123')

    if username == admin_user and password == admin_pass:
        session.clear()
        session['admin'] = True
        session['user'] = {'email': ADMIN_EMAIL or '', 'name': 'Admin'}
        return jsonify({"ok": True, "redirect": "/admin.html"})
    return jsonify({"error": "Invalid credentials"}), 401


@app.post('/logout')
def logout():
    session.clear()
    return jsonify({"ok": True})


# --- Products API ---
@app.get('/api/products')
def list_products():
    try:
        rows = get_db().execute('SELECT * FROM products').fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get('/api/products/<int:pid>')
def get_product(pid):
    try:
        row = get_db().execute('SELECT * FROM products WHERE id = ?', (pid,)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404
        return jsonify(dict(row))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post('/api/products')
def create_product():
    data = request.get_json(silent=True) or {}
    name = data.get('name')
    price = data.get('price')
    image = data.get('image') or ''
    model_image = data.get('model_image') or ''
    desc = data.get('desc') or ''
    color = data.get('color') or ''
    sizes = data.get('sizes') or ''
    season = data.get('season') or ''
    ptype = data.get('type') or ''
    status = data.get('status') or 'in'

    if name is None or price is None:
        return jsonify({"error": "name and price required"}), 400

    try:
        cur = get_db().cursor()
        cur.execute(
            'INSERT INTO products (name, price, image, model_image, desc, color, sizes, season, type, status) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (name, price, image, model_image, desc, color, sizes, season, ptype, status)
        )
        get_db().commit()
        return jsonify({"id": cur.lastrowid}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.delete('/api/products/<int:pid>')
def delete_product(pid):
    try:
        cur = get_db().cursor()
        cur.execute('DELETE FROM products WHERE id = ?', (pid,))
        get_db().commit()
        if cur.rowcount == 0:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- Cart API ---
@app.get('/api/cart')
def get_cart():
    try:
        cart_id = request.cookies.get('cart_id')
        rows = get_db().execute(
            '''
            SELECT cart_items.*, products.name, products.price, products.image
            FROM cart_items
            JOIN products ON products.id = cart_items.product_id
            WHERE cart_items.cart_id = ?
            ORDER BY cart_items.id DESC
            ''',
            (cart_id,)
        ).fetchall()
        # Normalize image to primary image (first URL) if multiple are comma-separated
        normalized = []
        for r in rows:
            d = dict(r)
            img = d.get('image') or ''
            try:
                if isinstance(img, str):
                    if ',' in img:
                        d['image'] = img.split(',')[0].strip()
                    else:
                        d['image'] = img.strip()
            except Exception:
                pass
            normalized.append(d)
        return jsonify(normalized)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post('/api/cart')
def add_to_cart():
    data = request.get_json(silent=True) or {}
    cart_id = request.cookies.get('cart_id')
    product_id = data.get('product_id')
    size = data.get('size') or ''
    color = data.get('color') or ''
    quantity = data.get('quantity')

    if not product_id:
        return jsonify({"error": "product_id required"}), 400

    try:
        qty = int(quantity) if isinstance(quantity, (int, float, str)) and str(quantity).isdigit() else 1
        if qty <= 0:
            qty = 1
    except Exception:
        qty = 1

    try:
        cur = get_db().cursor()
        cur.execute(
            'INSERT INTO cart_items (cart_id, product_id, size, color, quantity) VALUES (?,?,?,?,?)',
            (cart_id, product_id, size, color, qty)
        )
        get_db().commit()
        return jsonify({"id": cur.lastrowid}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.delete('/api/cart/<int:item_id>')
def delete_cart_item(item_id):
    try:
        cart_id = request.cookies.get('cart_id')
        cur = get_db().cursor()
        cur.execute('DELETE FROM cart_items WHERE id = ? AND cart_id = ?', (item_id, cart_id))
        get_db().commit()
        if cur.rowcount == 0:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.patch('/api/cart/<int:item_id>')
def update_cart_item(item_id):
    try:
        cart_id = request.cookies.get('cart_id')
        data = request.get_json(silent=True) or {}
        qty = data.get('quantity')
        if qty is None:
            return jsonify({"error": "quantity required"}), 400
        try:
            qty = int(qty)
        except Exception:
            return jsonify({"error": "quantity must be integer"}), 400

        cur = get_db().cursor()
        if qty <= 0:
            cur.execute('DELETE FROM cart_items WHERE id = ? AND cart_id = ?', (item_id, cart_id))
        else:
            cur.execute('UPDATE cart_items SET quantity = ? WHERE id = ? AND cart_id = ?', (qty, item_id, cart_id))
        get_db().commit()
        if cur.rowcount == 0:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- Orders API ---
@app.post('/api/orders')
def create_order():
    data = request.get_json(silent=True) or {}
    product = data.get('product') or ''
    color = data.get('color') or ''
    size = data.get('size') or ''
    amount = int(data.get('amount') or 1)
    name = data.get('name') or ''
    phone = data.get('phone') or ''
    gov = data.get('gov') or ''
    city = data.get('city') or ''
    address = data.get('address') or ''
    price = data.get('price') or 0
    shipping = data.get('shipping') or 0
    # Compute total based on amount when possible
    try:
        total = float(price) * max(amount, 1) + float(shipping or 0)
    except Exception:
        total = data.get('total') or 0
    addition = data.get('addition') or ''
    date_val = data.get('date') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    try:
        cur = get_db().cursor()
        cur.execute(
            '''
            INSERT INTO customers (product, color, size, amount, name, phone, gov, city, address, price, shipping, total, addition, date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''',
            (product, color, size, amount, name, phone, gov, city, address, price, shipping, total, addition, date_val)
        )
        get_db().commit()
        oid = cur.lastrowid
        try:
            msg = (
                f"New order #{oid}: {product} x{amount} | Total={float(total or 0)} | "
                f"Name={name} | Phone={phone} | {gov}/{city}"
            )
            send_discord(msg)
        except Exception:
            pass
        return jsonify({"id": oid}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get('/api/orders')
def list_orders():
    try:
        rows = get_db().execute('SELECT * FROM customers ORDER BY id DESC').fetchall()
        return jsonify([dict(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.delete('/api/orders/<int:oid>')
def delete_order(oid):
    try:
        cur = get_db().cursor()
        cur.execute('DELETE FROM customers WHERE id = ?', (oid,))
        get_db().commit()
        if cur.rowcount == 0:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.post('/api/orders/checkout')
def checkout_order():
    """
    Create customer rows for every item in the current cart, then clear the cart.
    Expects JSON with: name, phone, gov, city, address, addition (optional), shipping (optional), date (optional)
    """
    data = request.get_json(silent=True) or {}
    name = data.get('name') or ''
    phone = data.get('phone') or ''
    gov = data.get('gov') or ''
    city = data.get('city') or ''
    address = data.get('address') or ''
    addition = data.get('addition') or ''
    shipping = data.get('shipping') or 0
    date_val = data.get('date') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if not (name and phone and gov and city and address):
        return jsonify({"error": "Missing required fields"}), 400

    cart_id = request.cookies.get('cart_id')
    if not cart_id:
        return jsonify({"error": "No cart"}), 400

    try:
        conn = get_db()
        cur = conn.cursor()
        # Load cart items with product info
        items = conn.execute(
            '''
            SELECT cart_items.*, products.name AS product_name, products.price AS product_price
            FROM cart_items
            JOIN products ON products.id = cart_items.product_id
            WHERE cart_items.cart_id = ?
            ORDER BY cart_items.id ASC
            ''',
            (cart_id,)
        ).fetchall()

        if not items:
            return jsonify({"error": "Cart is empty"}), 400

        created_ids = []
        for it in items:
            qty = int(it['quantity'] or 1)
            unit_price = float(it['product_price'] or 0)
            line_total = unit_price * max(qty, 1) + float(shipping or 0)
            cur.execute(
                '''
                INSERT INTO customers (product, color, size, amount, name, phone, gov, city, address, price, shipping, total, addition, date)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ''',
                (
                    it['product_name'],
                    it['color'] or '',
                    it['size'] or '',
                    max(qty, 1),
                    name,
                    phone,
                    gov,
                    city,
                    address,
                    unit_price,
                    float(shipping or 0),
                    line_total,
                    addition,
                    date_val,
                ),
            )
            created_ids.append(cur.lastrowid)

        # Clear cart
        cur.execute('DELETE FROM cart_items WHERE cart_id = ?', (cart_id,))
        conn.commit()
        try:
            total_sum = 0.0
            count = 0
            for it in items:
                qty_i = int(it['quantity'] or 1)
                unit_i = float(it['product_price'] or 0)
                total_sum += unit_i * max(qty_i, 1)
                count += max(qty_i, 1)
            msg = (
                f"New checkout: items={count} | Total={total_sum + float(shipping or 0)} | "
                f"Name={name} | Phone={phone} | {gov}/{city}"
            )
            send_discord(msg)
        except Exception:
            pass
        return jsonify({"ok": True, "created": created_ids})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    app.run(host='0.0.0.0', port=port, debug=True)
