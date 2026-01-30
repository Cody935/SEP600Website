# app.py (updated with room codes, shared logs per room, etc.)
import sqlite3
from datetime import datetime
from flask import Flask, render_template, redirect, url_for, send_file, request, session, flash
import os
import pandas as pd
import io
import secrets

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

DB_FILE = 'smoke.db'
USERS_DB_FILE = 'users.db'

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            value INTEGER NOT NULL,
            status TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

    user_conn = sqlite3.connect(USERS_DB_FILE)
    user_cursor = user_conn.cursor()
    user_cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            room_code TEXT NOT NULL,
            UNIQUE(name, room_code)
        )
    ''')
    user_cursor.execute('''
        CREATE TABLE IF NOT EXISTS votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            vote_type TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    ''')
    user_cursor.execute('''
        CREATE TABLE IF NOT EXISTS dislike_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    ''')
    user_conn.commit()
    user_conn.close()

init_db()

LEVEL_MAP = {
    'green': {'value': 10, 'status': 'Good'},
    'yellow': {'value': 50, 'status': 'Smoky'},
    'red': {'value': 90, 'status': 'Danger'}
}

def get_status_color(value):
    if value < 30:
        return 'green'
    elif value <= 70:
        return 'yellow'
    else:
        return 'red'

def login_required(f):
    def wrap(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please log in first.')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    wrap.__name__ = f.__name__
    return wrap

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        name = request.form['name']
        room_code = request.form['code']

        conn = sqlite3.connect(USERS_DB_FILE)
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM users WHERE name = ? AND room_code = ?', (name, room_code))
        user = cursor.fetchone()

        if user:
            session['user_id'] = user[0]
            session['name'] = name
            session['room_code'] = room_code
            flash('Logged in successfully!')
            return redirect(url_for('index'))
        else:
            try:
                cursor.execute('INSERT INTO users (name, room_code) VALUES (?, ?)', (name, room_code))
                conn.commit()
                user_id = cursor.lastrowid
                session['user_id'] = user_id
                session['name'] = name
                session['room_code'] = room_code
                flash('Room joined/created and logged in!')
                return redirect(url_for('index'))
            except sqlite3.IntegrityError:
                flash('Name already taken in this room.')
            finally:
                conn.close()

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.')
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    room_code = session['room_code']

    # Latest shared air quality log for room
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT timestamp, value, status FROM logs WHERE room_code = ? ORDER BY id DESC LIMIT 1', (room_code,))
    latest_row = cursor.fetchone()
    conn.close()

    latest = None
    if latest_row:
        ts, val, stat = latest_row
        latest = {'timestamp': ts, 'value': val, 'status': stat, 'color': get_status_color(val)}

    # Vote counts for this room (assuming votes per user, but counts global per room - we can filter if needed)
    vote_conn = sqlite3.connect(USERS_DB_FILE)
    vote_cursor = vote_conn.cursor()
    vote_cursor.execute('''
        SELECT COUNT(*) FROM votes v JOIN users u ON v.user_id = u.id 
        WHERE u.room_code = ? AND v.vote_type = 'up'
    ''', (room_code,))
    up_votes = vote_cursor.fetchone()[0]
    vote_cursor.execute('''
        SELECT COUNT(*) FROM votes v JOIN users u ON v.user_id = u.id 
        WHERE u.room_code = ? AND v.vote_type = 'down'
    ''', (room_code,))
    down_votes = vote_cursor.fetchone()[0]
    vote_cursor.execute('''
        SELECT COUNT(*) FROM dislike_logs dl JOIN users u ON dl.user_id = u.id 
        WHERE u.room_code = ?
    ''', (room_code,))
    total_dislikes = vote_cursor.fetchone()[0]
    vote_conn.close()

    return render_template('index.html', latest=latest, up_votes=up_votes, down_votes=down_votes, total_dislikes=total_dislikes)

@app.route('/log/<level>')
@login_required
def log(level):
    if level not in LEVEL_MAP:
        return redirect(url_for('index'))
    
    room_code = session['room_code']
    data = LEVEL_MAP[level]
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO logs (room_code, timestamp, value, status) VALUES (?, ?, ?, ?)',
                   (room_code, timestamp, data['value'], data['status']))
    conn.commit()
    conn.close()
    
    return redirect(url_for('index'))

@app.route('/vote/<vote_type>')
@login_required
def vote(vote_type):
    if vote_type not in ['up', 'down']:
        return redirect(url_for('index'))
    
    user_id = session['user_id']
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    conn = sqlite3.connect(USERS_DB_FILE)
    cursor = conn.cursor()
    cursor.execute('INSERT INTO votes (user_id, vote_type, timestamp) VALUES (?, ?, ?)',
                   (user_id, vote_type, timestamp))
    
    if vote_type == 'down':
        message = 'Dislike detected, information logged.'
        cursor.execute('INSERT INTO dislike_logs (user_id, message, timestamp) VALUES (?, ?, ?)',
                       (user_id, message, timestamp))
    
    conn.commit()
    conn.close()
    
    return redirect(url_for('index'))

@app.route('/logs')
@login_required
def logs():
    room_code = session['room_code']
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT timestamp, value, status FROM logs WHERE room_code = ? ORDER BY id ASC', (room_code,))
    data = cursor.fetchall()
    conn.close()
    
    colored_logs = [(row[0], row[1], row[2], get_status_color(row[1])) for row in data]
    
    return render_template('logs.html', logs=colored_logs)

@app.route('/dislikes')
@login_required
def dislikes():
    room_code = session['room_code']
    
    conn = sqlite3.connect(USERS_DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT u.name, dl.timestamp, dl.message
        FROM dislike_logs dl
        JOIN users u ON dl.user_id = u.id
        WHERE u.room_code = ?
        ORDER BY dl.id DESC
    ''', (room_code,))
    data = cursor.fetchall()
    conn.close()
    
    dislikes_list = [(name, ts, msg) for name, ts, msg in data]
    
    return render_template('dislike_logs.html', dislikes=dislikes_list)

@app.route('/download')
@login_required
def download():
    room_code = session['room_code']
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT timestamp, value, status FROM logs WHERE room_code = ? ORDER BY id ASC', (room_code,))
    data = cursor.fetchall()
    conn.close()
    
    df = pd.DataFrame(data, columns=['Timestamp', 'Value', 'Status'])
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    
    return send_file(output, as_attachment=True, download_name=f'{room_code}_air_quality_log.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

if __name__ == '__main__':
    app.run(debug=True)