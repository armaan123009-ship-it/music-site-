from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, Response, stream_with_context
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from ytmusicapi import YTMusic
import yt_dlp
import bcrypt
import json
import os
import requests

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-in-production'  # CHANGE THIS!

# ---------- DATABASE SETUP ----------
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# ---------- USER MODEL ----------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    # Store liked songs as JSON string
    liked_songs = db.Column(db.Text, default='[]')
    # Store user playlists as JSON string
    playlists = db.Column(db.Text, default='[{"id":"default","name":"My First Playlist","songs":[]}]')
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    def set_password(self, password):
        self.password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    def check_password(self, password):
        return bcrypt.checkpw(password.encode('utf-8'), self.password_hash.encode('utf-8'))

    def get_liked_songs(self):
        return json.loads(self.liked_songs)

    def set_liked_songs(self, songs):
        self.liked_songs = json.dumps(songs)

    def get_playlists(self):
        return json.loads(self.playlists)

    def set_playlists(self, playlists):
        self.playlists = json.dumps(playlists)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Create database tables (run once)
with app.app_context():
    db.create_all()

# ---------- YT MUSIC SETUP ----------
yt = YTMusic("browser.json")  # make sure this file exists

def format_song(item):
    """Robust formatter for ytmusicapi responses"""
    if not item:
        return None
    try:
        video_id = item.get('videoId') or item.get('id')
        title = item.get('title', 'Unknown')
        artists = item.get('artists', [])
        if artists and isinstance(artists, list):
            artist = artists[0].get('name', 'Various Artists')
        elif isinstance(artists, str):
            artist = artists
        else:
            artist = 'Unknown Artist'
        thumbs = item.get('thumbnails', [])
        thumb_url = thumbs[-1]['url'] if thumbs else ''
        duration = item.get('duration', '0:00')
        return {
            "id": video_id,
            "title": title,
            "artist": artist,
            "image": thumb_url,
            "duration": duration
        }
    except Exception as e:
        print("Format error:", e)
        return None

# ---------- AUTH ROUTES ----------
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/login", methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            flash('Logged in successfully!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password', 'error')
    return render_template("login.html")

@app.route("/register", methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        # Check if user exists
        if User.query.filter_by(username=username).first():
            flash('Username already taken', 'error')
            return redirect(url_for('register'))
        if User.query.filter_by(email=email).first():
            flash('Email already registered', 'error')
            return redirect(url_for('register'))
        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        flash('Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template("register.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('index'))

@app.route("/profile")
@login_required
def profile():
    return render_template("profile.html", user=current_user)

# ---------- API ENDPOINTS (with user data) ----------
@app.route("/api/user_data")
def user_data():
    """Return current user's liked songs and playlists (if logged in)"""
    if current_user.is_authenticated:
        return jsonify({
            "logged_in": True,
            "username": current_user.username,
            "liked_songs": current_user.get_liked_songs(),
            "playlists": current_user.get_playlists()
        })
    else:
        return jsonify({"logged_in": False})

@app.route("/api/like", methods=['POST'])
@login_required
def like_song():
    data = request.json
    song = data.get('song')
    action = data.get('action')  # 'add' or 'remove'
    liked = current_user.get_liked_songs()
    if action == 'add':
        if not any(s['id'] == song['id'] for s in liked):
            liked.append(song)
    elif action == 'remove':
        liked = [s for s in liked if s['id'] != song['id']]
    current_user.set_liked_songs(liked)
    db.session.commit()
    return jsonify({"success": True})

@app.route("/api/playlists", methods=['GET', 'POST', 'PUT', 'DELETE'])
@login_required
def manage_playlists():
    playlists = current_user.get_playlists()
    if request.method == 'GET':
        return jsonify([{"id": p["id"], "name": p["name"], "count": len(p["songs"])} for p in playlists])
    elif request.method == 'POST':  # create
        data = request.json
        new_id = str(int(playlists[-1]["id"])+1 if playlists else 1)
        new_playlist = {"id": new_id, "name": data['name'], "songs": []}
        playlists.append(new_playlist)
        current_user.set_playlists(playlists)
        db.session.commit()
        return jsonify({"id": new_id, "name": data['name'], "count": 0})
    elif request.method == 'PUT':  # add song to playlist
        data = request.json
        playlist_id = data.get('playlistId')
        song = data.get('song')
        for pl in playlists:
            if pl['id'] == playlist_id:
                if not any(s['id'] == song['id'] for s in pl['songs']):
                    pl['songs'].append(song)
                break
        current_user.set_playlists(playlists)
        db.session.commit()
        return jsonify({"success": True})
    elif request.method == 'DELETE':  # remove song from playlist
        data = request.json
        playlist_id = data.get('playlistId')
        song_id = data.get('songId')
        for pl in playlists:
            if pl['id'] == playlist_id:
                pl['songs'] = [s for s in pl['songs'] if s['id'] != song_id]
                break
        current_user.set_playlists(playlists)
        db.session.commit()
        return jsonify({"success": True})

# ---------- YT MUSIC API ENDPOINTS (unchanged) ----------
@app.route("/api/trending")
def trending():
    try:
        charts = yt.get_charts()
        trending_videos = charts.get('trending', {}).get('videos', [])
        if not trending_videos:
            trending_videos = yt.search("trending music", filter="videos")[:20]
        songs = [format_song(v) for v in trending_videos if format_song(v)]
        return jsonify(songs)
    except Exception as e:
        print("Trending error:", e)
        return jsonify([])

@app.route("/api/charts")
def charts():
    try:
        charts = yt.get_charts()
        top_moves = charts.get('topMoves', {}).get('videos', [])
        if not top_moves:
            top_moves = charts.get('top_moves', {}).get('videos', [])
        if not top_moves:
            top_moves = yt.search("top hits", filter="videos")[:20]
        songs = [format_song(v) for v in top_moves if format_song(v)]
        return jsonify(songs)
    except Exception as e:
        print("Charts error:", e)
        return jsonify([])

@app.route("/api/genre_playlists")
def genre_playlists():
    genres = [
        {"name": "Rock", "id": "PLRAV69dZi1kmkKfO-cF3bMfL_OlypLTyC"},
        {"name": "Pop", "id": "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"},
        {"name": "Hip Hop", "id": "PLFgquLnL59ak7A6WvwUC98jGahKbL8hVX"},
        {"name": "Electronic", "id": "PLFgquLnL59alW3xmYiWRaoz0VfaA5VSb-"},
        {"name": "Jazz", "id": "PL7A5D734F7DBF51CE"},
        {"name": "Classical", "id": "PLB03F9C3F3B6B8F5C"},
    ]
    return jsonify(genres)

@app.route("/api/playlist/<playlist_id>")
def get_playlist(playlist_id):
    try:
        pl = yt.get_playlist(playlist_id, limit=50)
        songs = []
        for track in pl.get('tracks', []):
            formatted = format_song(track)
            if formatted:
                songs.append(formatted)
        return jsonify({"title": pl.get('title', 'Playlist'), "songs": songs})
    except Exception as e:
        print("Playlist error:", e)
        return jsonify({"title": "Playlist", "songs": []})

@app.route("/api/search")
def search():
    q = request.args.get('q', '')
    if not q:
        return jsonify([])
    try:
        results = yt.search(q, filter="videos")[:30]
        songs = [format_song(r) for r in results if format_song(r)]
        return jsonify(songs)
    except Exception as e:
        print("Search error:", e)
        return jsonify([])

@app.route("/api/home")
def home():
    try:
        home = yt.get_home(limit=8)
        sections = []
        for section in home:
            title = section.get('title', '')
            contents = section.get('contents', [])
            items = []
            for item in contents[:12]:
                if 'videoId' in item:
                    items.append(format_song(item))
                elif 'browseId' in item:
                    items.append({
                        "type": "playlist",
                        "id": item['browseId'],
                        "title": item['title'],
                        "image": item['thumbnails'][-1]['url'] if item.get('thumbnails') else "",
                        "count": item.get('videoCount', 0)
                    })
            if items:
                sections.append({"title": title, "items": items})
        return jsonify(sections)
    except Exception as e:
        print("Home error:", e)
        return jsonify([])

import os

@app.route("/stream/<video_id>")
def stream(video_id):
    base_path = os.path.dirname(os.path.abspath(__file__))
    cookie_path = os.path.join(base_path, 'cookies.txt')

    # Clean, simple config without contradictory headers
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'extractor_args': {'youtube': {'player_client': ['web', 'default']}}
    }
    # ⬆️ ⬆️ ⬆️

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(f"https://music.youtube.com/watch?v={video_id}", download=False)
            except Exception as e1:
                print("YTM extraction failed, trying standard YT...", str(e1))
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            return jsonify({"url": info['url']})
    except Exception as e:
        print("Detailed Stream error:", str(e))
        return jsonify({"error": "Stream blocked. Please try a different song or update cookies."}), 400

@app.route("/proxy")
def proxy():
    url = request.args.get('url')
    if not url:
        return "No URL", 400
        
    headers = {}
    if request.headers.get("Range"):
        headers["Range"] = request.headers.get("Range")
        
    try:
        r = requests.get(url, headers=headers, stream=True)
        def generate():
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
                    
        response = Response(stream_with_context(generate()), status=r.status_code)
        for key, value in r.headers.items():
            if key.lower() not in ['content-encoding', 'transfer-encoding', 'connection']:
                response.headers[key] = value
        return response
    except Exception as e:
        print("Proxy error:", str(e))
        return str(e), 500

# ---------- ADMIN: VIEW ALL REGISTERED USERS ----------
# ... (all your other routes and API endpoints)

# ---------- ADMIN: VIEW ALL REGISTERED USERS ----------
@app.route("/admin/users")
@login_required
def admin_users():
    if current_user.username != 'admin':
        flash("You do not have permission to view this page.", "error")
        return redirect(url_for('index'))
    users = User.query.all()
    return render_template("admin_users.html", users=users)

if __name__ == "__main__":
    app.run(debug=True)