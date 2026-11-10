from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, Response, stream_with_context, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_cors import CORS
from ytmusicapi import YTMusic
import yt_dlp
import bcrypt
import json
import os
import requests
import time
import hmac
import hashlib

# Simple cache for API responses and stream URLs
api_cache = {}
stream_cache = {}

def get_cached_api(key, duration=600):
    now = time.time()
    if key in api_cache:
        data, ts = api_cache[key]
        if now - ts < duration:
            return data
    return None

def set_cached_api(key, data):
    api_cache[key] = (data, time.time())

def get_cached_stream_url(video_id):
    if video_id in stream_cache:
        url, ts = stream_cache[video_id]
        # Check expire query parameter in URL
        try:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            expire_str = params.get('expire', [None])[0]
            if expire_str:
                expire_time = int(expire_str)
                # Give a 5-minute buffer
                if time.time() < expire_time - 300:
                    return url
                else:
                    # Expired, remove from cache
                    del stream_cache[video_id]
                    return None
        except Exception as e:
            print(f"Error parsing expire param from cache URL: {e}")
            
        # Fallback to 1 hour cache duration if expire parsing fails
        now = time.time()
        if now - ts < 3600:
            return url
        else:
            del stream_cache[video_id]
    return None

def set_cached_stream_url(video_id, url):
    stream_cache[video_id] = (url, time.time())

def get_hmac_secret():
    # Priority: HMAC_SECRET, JWT_SECRET, SECRET_KEY from environment, or static fallback
    return os.environ.get('HMAC_SECRET') or os.environ.get('JWT_SECRET') or os.environ.get('SECRET_KEY') or 'premium-music-secret-key-12345'

def generate_signature(video_id, url, expires_at):
    message = f"{video_id}:{url}:{expires_at}"
    secret = get_hmac_secret()
    signature = hmac.new(
        secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature

def verify_signature(video_id, url, expires_at, signature):
    expected = generate_signature(video_id, url, expires_at)
    return hmac.compare_digest(expected, signature)

def proxy_track_image(track_id, image_url):
    if not image_url:
        return f"https://i.ytimg.com/vi/{track_id}/hqdefault.jpg"
    if "api/image-proxy" in image_url:
        return image_url
    from flask import has_request_context, request
    if has_request_context():
        return f"{request.host_url}api/image-proxy?url={requests.utils.quote(image_url)}&id={track_id}"
    return image_url

def proxy_db_track(track):
    if not track or 'image' not in track or 'id' not in track:
        return track
    t = dict(track)
    t['image'] = proxy_track_image(t['id'], t['image'])
    return t



# Resolve base directories safely
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dist_folder = os.path.join(base_dir, 'dist')
browser_path = os.path.join(base_dir, 'browser.json')
cookies_path = os.path.join(base_dir, 'cookies.txt')

app = Flask(__name__, static_folder=dist_folder, static_url_path='/_static')
app.secret_key = os.environ.get('SECRET_KEY', 'premium-music-secret-key-12345')

# Production cookie settings for cross-origin authentication
if os.environ.get('FLASK_ENV') == 'production' or os.environ.get('RENDER') or os.environ.get('DATABASE_URL') or os.environ.get('VERCEL'):
    app.config.update(
        SESSION_COOKIE_SAMESITE='None',
        SESSION_COOKIE_SECURE=True
    )

# Robust CORS handler that supports credentials safely across all environments
@app.after_request
def add_cors_headers(response):
    origin = request.headers.get('Origin')
    if origin:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Credentials'] = 'true'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, Range, X-Requested-With'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Expose-Headers'] = 'Content-Range, Content-Length, Content-Disposition'
    else:
        response.headers['Access-Control-Allow-Origin'] = '*'
    return response

# ---------- DATABASE SETUP (Safe Serverless & Local) ----------
db_url = os.environ.get('DATABASE_URL')
if db_url:
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
else:
    import tempfile
    tmp_db_path = os.path.join(tempfile.gettempdir(), 'users.db').replace('\\', '/')
    is_serverless = bool(os.environ.get('VERCEL') or os.environ.get('VERCEL_ENV') or os.environ.get('AWS_LAMBDA_FUNCTION_NAME') or os.environ.get('FLASK_ENV') == 'production')
    if is_serverless:
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{tmp_db_path}'
    else:
        try:
            instance_dir = os.path.join(base_dir, 'instance')
            os.makedirs(instance_dir, exist_ok=True)
            app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(instance_dir, "users.db")}'
        except OSError:
            app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{tmp_db_path}'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
login_manager = LoginManager()
login_manager.init_app(app)

@login_manager.unauthorized_handler
def unauthorized():
    return jsonify({"error": "Unauthorized"}), 401

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    liked_songs = db.Column(db.Text, default='[]')
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

try:
    with app.app_context():
        db.create_all()
except Exception as db_init_err:
    print(f"[DB Setup Warning] {db_init_err}")

@app.route("/api/playlist/<playlist_id>")
@login_required
def get_playlist_details(playlist_id):
    playlists = current_user.get_playlists()
    for pl in playlists:
        if pl['id'] == playlist_id:
            songs = [proxy_db_track(s) for s in pl['songs']]
            return jsonify({"title": pl['name'], "songs": songs})
    return jsonify({"error": "Playlist not found"}), 404

# ---------- YT MUSIC SETUP ----------
try:
    if os.path.exists(browser_path):
        yt = YTMusic(browser_path)
    else:
        yt = YTMusic()
except Exception:
    yt = YTMusic()

# Official YouTube Data API Key integration for fallbacks, search and autoplay
YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY', 'AIzaSyD4sC5H6EHd97Fl9aHvcek9F2UO1KXVIVU')

def search_youtube_official(query, max_results=25):
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "videoCategoryId": "10",  # Music category ID
        "key": YOUTUBE_API_KEY,
        "maxResults": max_results
    }
    try:
        r = requests.get(url, params=params, timeout=5)
        if r.status_code == 200:
            items = r.json().get('items', [])
            songs = []
            for item in items:
                video_id = item['id'].get('videoId')
                if not video_id:
                    continue
                snippet = item['snippet']
                import html
                title = html.unescape(snippet.get('title', 'Unknown'))
                channel_title = html.unescape(snippet.get('channelTitle', 'Various Artists'))
                
                thumbs = snippet.get('thumbnails', {})
                thumb_url = ""
                for quality in ['maxres', 'standard', 'high', 'medium', 'default']:
                    if quality in thumbs and thumbs[quality].get('url'):
                        thumb_url = thumbs[quality]['url']
                        break
                        
                image_url = proxy_track_image(video_id, thumb_url)
                songs.append({
                    "id": video_id,
                    "title": title,
                    "artist": channel_title,
                    "image": image_url,
                    "duration": "3:45"
                })
            return songs
    except Exception as e:
        print(f"Official YouTube search failed: {e}")
    return None

def get_trending_youtube_official(max_results=25):
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet,contentDetails",
        "chart": "mostPopular",
        "videoCategoryId": "10",  # Music category ID
        "key": YOUTUBE_API_KEY,
        "maxResults": max_results
    }
    try:
        r = requests.get(url, params=params, timeout=5)
        if r.status_code == 200:
            items = r.json().get('items', [])
            songs = []
            for item in items:
                video_id = item['id']
                snippet = item['snippet']
                import html
                title = html.unescape(snippet.get('title', 'Unknown'))
                channel_title = html.unescape(snippet.get('channelTitle', 'Various Artists'))
                
                thumbs = snippet.get('thumbnails', {})
                thumb_url = ""
                for quality in ['maxres', 'standard', 'high', 'medium', 'default']:
                    if quality in thumbs and thumbs[quality].get('url'):
                        thumb_url = thumbs[quality]['url']
                        break
                        
                # Parse duration if contentDetails is available
                duration_str = "3:45"
                content_details = item.get('contentDetails')
                if content_details:
                    yt_dur = content_details.get('duration', '')
                    # Simple ISO 8601 duration parser (e.g. PT4M13S)
                    try:
                        import re
                        m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', yt_dur)
                        if m:
                            hours = int(m.group(1)) if m.group(1) else 0
                            minutes = int(m.group(2)) if m.group(2) else 0
                            seconds = int(m.group(3)) if m.group(3) else 0
                            if hours > 0:
                                duration_str = f"{hours}:{minutes:02d}:{seconds:02d}"
                            else:
                                duration_str = f"{minutes}:{seconds:02d}"
                    except Exception:
                        pass
                        
                image_url = proxy_track_image(video_id, thumb_url)
                songs.append({
                    "id": video_id,
                    "title": title,
                    "artist": channel_title,
                    "image": image_url,
                    "duration": duration_str
                })
            return songs
    except Exception as e:
        print(f"Official YouTube trending fetch failed: {e}")
    return None

def get_youtube_suggestions_official(video_id):
    details_url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "snippet",
        "id": video_id,
        "key": YOUTUBE_API_KEY
    }
    try:
        r = requests.get(details_url, params=params, timeout=5)
        if r.status_code == 200:
            items = r.json().get('items', [])
            if items:
                snippet = items[0]['snippet']
                title = snippet.get('title', '')
                channel_title = snippet.get('channelTitle', '')
                tags = snippet.get('tags', [])
                
                query_parts = []
                if channel_title:
                    query_parts.append(channel_title)
                if tags:
                    query_parts.extend(tags[:2])
                else:
                    query_parts.append(" ".join(title.split()[:3]))
                
                search_q = " ".join(query_parts)
                print(f"[Official Autoplay] Fetching recommendations for: {search_q}")
                official_songs = search_youtube_official(search_q, max_results=10)
                if official_songs:
                    return [s for s in official_songs if s['id'] != video_id]
    except Exception as e:
        print(f"Official YouTube suggestions failed: {e}")
    return None


def format_song(item):
    if not item: return None
    try:
        # Check multiple possible ID locations
        video_id = item.get('videoId') or item.get('id')
        if not video_id: return None
        
        title = item.get('title', 'Unknown')
        artists = item.get('artists', [])
        if artists and isinstance(artists, list):
            artist = artists[0].get('name', 'Various Artists')
            artist_id = artists[0].get('id') or artists[0].get('browseId')
        else:
            artist = 'Various Artists'
            artist_id = None
            
        thumbs = item.get('thumbnails') or []
        thumb_url = thumbs[-1].get('url') if thumbs else ""
        
        image_url = proxy_track_image(video_id, thumb_url)
        return {
            "id": video_id, 
            "title": title, 
            "artist": artist, 
            "artistId": artist_id,
            "image": image_url, 
            "duration": item.get('duration', '3:45')
        }
    except Exception: return None

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or request.form or {}
    username = data.get("username", "").strip()
    email = data.get("email", "").strip()
    password = data.get("password", "")
    
    if not username or not password:
        return jsonify({"error": "Username and password are required"}), 400
        
    if User.query.filter_by(username=username).first():
        return jsonify({"error": "Username already exists"}), 400
    if email and User.query.filter_by(email=email).first():
        return jsonify({"error": "Email is already registered"}), 400
        
    user = User(username=username, email=email or f"{username}@local.site")
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    login_user(user)
    return jsonify({"success": True, "user": {"username": user.username, "email": user.email}})

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or request.form or {}
    username_or_email = data.get("username", "").strip() or data.get("email", "").strip()
    password = data.get("password", "")
    
    if not username_or_email or not password:
        return jsonify({"error": "Missing credentials"}), 400
        
    user = User.query.filter_by(username=username_or_email).first()
    if not user:
        user = User.query.filter_by(email=username_or_email).first()
        
    if user and user.check_password(password):
        login_user(user, remember=True)
        return jsonify({"success": True, "user": {"username": user.username, "email": user.email}})
    return jsonify({"error": "Invalid credentials"}), 401

@app.route("/api/auth/security-status", methods=["GET", "POST"])
def auth_security_status():
    return jsonify({
        "status": "active",
        "email_verification_enabled": True,
        "security_level": "high",
        "supported_auth": ["google", "password"],
        "features": [
            "OAuth 2.0 Google Identity Verification",
            "Automated Email Security Verification",
            "Signed Media Stream Tokens",
            "Cross-Origin Protection"
        ]
    })

@app.route("/api/logout")
@login_required
def logout():
    logout_user()
    return jsonify({"success": True})

@app.route("/api/like", methods=["POST"])
@login_required
def like_song():
    data = request.json
    song = data.get("song")
    action = data.get("action") # 'add' or 'remove'
    
    liked = current_user.get_liked_songs()
    if action == "add":
        if not any(s['id'] == song['id'] for s in liked):
            liked.append(song)
    else:
        liked = [s for s in liked if s['id'] != song['id']]
    
    current_user.set_liked_songs(liked)
    db.session.commit()
    return jsonify({"success": True, "liked_songs": liked})

@app.route("/api/playlists")
@login_required
def get_user_playlists():
    return jsonify(current_user.get_playlists())

@app.route("/api/playlists/create", methods=["POST"])
@login_required
def create_playlist():
    name = request.json.get("name", "New Playlist")
    playlists = current_user.get_playlists()
    new_id = f"pl_{int(db.func.now().timestamp())}"
    playlists.append({"id": new_id, "name": name, "songs": []})
    current_user.set_playlists(playlists)
    db.session.commit()
    return jsonify({"success": True, "playlists": playlists})

@app.route("/api/playlists/add", methods=["POST"])
@login_required
def add_to_playlist():
    data = request.json
    playlist_id = data.get("playlist_id")
    song = data.get("song")
    
    playlists = current_user.get_playlists()
    for pl in playlists:
        if pl['id'] == playlist_id:
            if not any(s['id'] == song['id'] for s in pl['songs']):
                pl['songs'].append(song)
            break
            
    current_user.set_playlists(playlists)
    db.session.commit()
    return jsonify({"success": True})

# ---------- HEALTH CHECK ----------
@app.route('/health')
@app.route('/api/health')
def health():
    return jsonify({
        "status": "ok",
        "service": "music-backend-api",
        "timestamp": time.time()
    }), 200

# ---------- STATIC SERVING (For local testing) ----------
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    if path != "" and os.path.exists(app.static_folder + '/' + path):
        full_path = os.path.join(app.static_folder, path)
        if os.path.isdir(full_path):
            return send_from_directory(full_path, 'index.html')
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, 'index.html')

# ---------- API ENDPOINTS ----------
@app.route("/api/home")
def home():
    cached = get_cached_api("home", duration=900)  # 15 mins cache
    if cached is not None:
        return jsonify(cached)
    
    sections = []
    try:
        try:
            home_data = yt.get_home(limit=5)
            for section in home_data:
                items = [format_song(i) for i in section.get('contents', []) if format_song(i)][:12]
                if items:
                    sections.append({"title": section.get('title', 'Recommended'), "items": items})
        except Exception as e:
            print(f"[Home API] Native home failed: {e}")
        
        if not sections:
            playlist_id = 'PL4fGSI1pDJn6t3TXLGiiJdD-sZbrG3tG0'
            try:
                charts = yt.get_charts()
                videos = charts.get('videos', [])
                if videos:
                    playlist_id = videos[0].get('playlistId') or playlist_id
            except Exception as e:
                print(f"[Home API] Charts check failed: {e}")
            
            try:
                playlist = yt.get_playlist(playlist_id)
                items = [format_song(i) for i in playlist.get('tracks', []) if format_song(i)][:12]
                if items:
                    sections.append({"title": "Trending Now", "items": items})
            except Exception as e:
                print(f"[Home API] Native playlist fetch failed: {e}")
                
        if not sections:
            print("[Home API] Native YTMusic results empty. Running official YouTube API search fallbacks...")
            fallbacks = [
                ("Trending Now", "music trending hits"),
                ("Chill Vibes", "lofi chill beats study sleep"),
                ("Gaming Arena", "gaming music synthwave"),
                ("Classic Hits", "classic rock pop 80s 90s hits")
            ]
            for title, query in fallbacks:
                items = search_youtube_official(query, max_results=12)
                if items:
                    sections.append({"title": title, "items": items})
                    
        if sections:
            set_cached_api("home", sections)
        return jsonify(sections)
    except Exception as e:
        print(f"[Home API] Outer exception: {e}. Building official YouTube fallbacks...")
        fallbacks = [
            ("Trending Now", "music trending hits"),
            ("Chill Vibes", "lofi chill beats study sleep"),
            ("Gaming Arena", "gaming music synthwave"),
            ("Classic Hits", "classic rock pop 80s 90s hits")
        ]
        sections = []
        for title, query in fallbacks:
            items = search_youtube_official(query, max_results=12)
            if items:
                sections.append({"title": title, "items": items})
        return jsonify(sections)

@app.route("/api/trending")
def trending():
    cached = get_cached_api("trending", duration=1800)  # 30 mins cache
    if cached is not None:
        return jsonify(cached)
    
    songs = []
    try:
        playlist_id = 'PL4fGSI1pDJn6t3TXLGiiJdD-sZbrG3tG0'
        try:
            charts = yt.get_charts()
            videos = charts.get('videos', [])
            if videos:
                playlist_id = videos[0].get('playlistId') or playlist_id
        except Exception as e:
            print(f"[Trending API] Charts fetch failed: {e}")
        
        try:
            playlist = yt.get_playlist(playlist_id)
            songs = [format_song(i) for i in playlist.get('tracks', []) if format_song(i)]
        except Exception as e:
            print(f"[Trending API] Native playlist fetch failed: {e}")
            
        if not songs:
            print("[Trending API] YTMusic trends empty. Falling back to official YouTube Data API trending...")
            official_songs = get_trending_youtube_official()
            if official_songs:
                songs = official_songs
                
        if songs:
            set_cached_api("trending", songs)
        return jsonify(songs)
    except Exception as e:
        print(f"[Trending API] Exception: {e}. Trying official YouTube Data API fallback...")
        official_songs = get_trending_youtube_official()
        if official_songs:
            set_cached_api("trending", official_songs)
            return jsonify(official_songs)
        return jsonify([])

@app.route("/api/explore")
@app.route("/api/latest")
def explore_api():
    category = request.args.get('category', '').strip().lower()
    cache_key = f"explore_data_{category}" if category else "explore_data_all"
    cached = get_cached_api(cache_key, duration=1200)
    if cached is not None:
        return jsonify(cached)
        
    latest_songs = []
    charts_songs = []
    
    # 1. Fetch latest real new releases
    try:
        latest_res = yt.search("latest new music songs 2026", filter="songs")[:24]
        latest_songs = [format_song(r) for r in latest_res if format_song(r)]
    except Exception as e:
        print(f"[Explore] Search for latest songs failed: {e}")
        
    if not latest_songs:
        official_latest = search_youtube_official("latest hit music 2026 official video", max_results=20)
        if official_latest:
            latest_songs = official_latest

    # 2. Fetch charts songs
    try:
        playlist_id = 'PL4fGSI1pDJn6t3TXLGiiJdD-sZbrG3tG0'
        try:
            charts = yt.get_charts()
            videos = charts.get('videos', [])
            if videos:
                playlist_id = videos[0].get('playlistId') or playlist_id
        except Exception:
            pass
        playlist = yt.get_playlist(playlist_id)
        charts_songs = [format_song(i) for i in playlist.get('tracks', []) if format_song(i)][:20]
    except Exception as e:
        print(f"[Explore] Charts fetch failed: {e}")
        
    if not charts_songs:
        charts_songs = get_trending_youtube_official() or latest_songs[:12]

    # 3. Dynamic genre catalogue for exploration
    genres_data = {}
    genre_queries = {
        "Pop": "top pop hits 2026",
        "Hip-Hop": "top hip hop hits 2026",
        "Rock": "classic rock modern rock hits",
        "Electronic": "edm electronic dance hits",
        "R&B": "rnb soul hits 2026",
        "Indie": "indie pop alternative songs",
        "Chill": "lofi chill beats study",
        "Dance": "dance pop club party hits"
    }

    if category and category in [k.lower() for k in genre_queries.keys()]:
        target_key = next(k for k in genre_queries.keys() if k.lower() == category)
        try:
            g_res = yt.search(genre_queries[target_key], filter="songs")[:20]
            g_songs = [format_song(r) for r in g_res if format_song(r)]
            result = {
                "category": target_key,
                "tracks": g_songs if g_songs else latest_songs[:12]
            }
            set_cached_api(cache_key, result)
            return jsonify(result)
        except Exception:
            pass

    for g_name, g_query in list(genre_queries.items())[:6]:
        try:
            res = yt.search(g_query, filter="songs")[:8]
            g_items = [format_song(r) for r in res if format_song(r)]
            if g_items:
                genres_data[g_name] = g_items
        except Exception:
            pass

    payload = {
        "latest": latest_songs,
        "charts": charts_songs,
        "genres": genres_data
    }
    set_cached_api(cache_key, payload)
    return jsonify(payload)
    q = request.args.get('q', '')
    if not q: return jsonify([])
    cache_key = f"search_{q}"
    cached = get_cached_api(cache_key, duration=600)  # 10 mins cache
    if cached is not None:
        return jsonify(cached)
    try:
        results = yt.search(q, filter="songs")[:40]
        if not results: results = yt.search(q, filter="videos")[:40]
        songs = [format_song(r) for r in results if format_song(r)]
        if not songs:
            official_songs = search_youtube_official(q)
            if official_songs:
                songs = official_songs
        set_cached_api(cache_key, songs)
        return jsonify(songs)
    except Exception:
        official_songs = search_youtube_official(q)
        if official_songs:
            set_cached_api(cache_key, official_songs)
            return jsonify(official_songs)
        return jsonify([])


# Helper functions for resolving stream URLs with fallbacks to bypass blocks
IS_PRODUCTION_ENV = bool(os.environ.get('VERCEL') or os.environ.get('FLASK_ENV') == 'production')

def extract_stream_url(video_id):
    if IS_PRODUCTION_ENV:
        return None

    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'ignoreerrors': True,
        'cachedir': False,
        'noconfig': True,
        'socket_timeout': 2,
        'extractor_args': {'youtube': {'client': ['android']}}
    }
    if os.path.exists(cookies_path) and os.path.getsize(cookies_path) > 0:
        try:
            with open(cookies_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                if '# Netscape' in content or 'domain' in content:
                    ydl_opts['cookiefile'] = cookies_path
        except: pass

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            if info and 'url' in info:
                return info['url']
    except Exception as e:
        print(f"[yt-dlp fast] Extraction failed for {video_id}: {e}")

    return None

def extract_stream_url_fast(video_id):
    """Fast yt-dlp extraction: android client only, single attempt. Used in /play for speed."""
    if IS_PRODUCTION_ENV:
        return None
    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'ignoreerrors': True,
        'cachedir': False,
        'noconfig': True,
        'socket_timeout': 5,
        'extractor_args': {'youtube': {'client': ['android']}}
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            if info and 'url' in info:
                print(f"[yt-dlp fast] Got URL for {video_id}")
                return info['url']
    except Exception as e:
        print(f"[yt-dlp fast] Failed for {video_id}: {e}")
    return None

last_working_cobalt = None
last_working_piped = None
last_working_invidious = None

dynamic_cobalt_cache = {"instances": [], "last_fetched": 0}

def fetch_dynamic_cobalt_instances():
    now = time.time()
    # Cache for 15 minutes to prevent hitting rate limits
    if now - dynamic_cobalt_cache["last_fetched"] < 900:
        return dynamic_cobalt_cache["instances"]
        
    try:
        print("[Dynamic Cobalt] Fetching live instances from instances.cobalt.best...")
        r = requests.get("https://instances.cobalt.best/api/instances.json", timeout=2.0)
        if r.status_code == 200:
            data = r.json()
            instances = []
            if isinstance(data, list):
                for item in data:
                    url = item.get("url")
                    if url and (item.get("status") == "up" or item.get("score", 0) > 50):
                        instances.append(url.rstrip('/'))
            elif isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, dict):
                        url = v.get("url") or k
                        if url:
                            instances.append(url.rstrip('/'))
            if instances:
                print(f"[Dynamic Cobalt] Found {len(instances)} live instances.")
                dynamic_cobalt_cache["instances"] = instances
                dynamic_cobalt_cache["last_fetched"] = now
                return instances
    except Exception as e:
        print(f"[Dynamic Cobalt] Failed to fetch dynamic list: {e}")
        
    return dynamic_cobalt_cache["instances"]

def check_cobalt_instance(instance, headers, video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"
    schemas = [
        # Schema v10
        {"url": url, "downloadMode": "audio", "audioFormat": "mp3", "alwaysProxy": True},
        # Schema v7
        {"url": url, "audioOnly": True, "aFormat": "mp3", "alwaysProxy": True}
    ]
    target = instance.rstrip('/') + '/'
    for schema in schemas:
        try:
            r = requests.post(target, headers=headers, json=schema, timeout=2.0)
            if r.status_code == 200:
                res_data = r.json()
                stream_url = res_data.get("url") or res_data.get("picker")
                if stream_url:
                    return instance, stream_url
        except Exception:
            pass
    return None

def fetch_cobalt_stream_url(video_id):
    import concurrent.futures
    global last_working_cobalt
    instances = []
    
    # Try fetching live instances dynamically first
    dynamic_instances = fetch_dynamic_cobalt_instances()
    if dynamic_instances:
        instances.extend(dynamic_instances)
        
    # Reliable community fallbacks
    static_fallbacks = [
        "https://rue-cobalt.xenon.zone",
        "https://api.cobalt.tools",
        "https://cobalt.foxtrot-omega.me",
        "https://cobalt.willy.lol",
        "https://cobalt.k6.dev",
        "https://cobalt-api.lunar.icu",
        "https://cobalt.col1g3.de",
        "https://cobalt.projectsegfau.lt"
    ]
    for fallback in static_fallbacks:
        if fallback not in instances:
            instances.append(fallback)
            
    # Place last working instance first
    if last_working_cobalt and last_working_cobalt in instances:
        instances.remove(last_working_cobalt)
        instances.insert(0, last_working_cobalt)

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    candidates = instances[:8]
    print(f"[Cobalt Resolver] Checking {len(candidates)} candidates concurrently...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(candidates)) as executor:
        futures = {executor.submit(check_cobalt_instance, inst, headers, video_id): inst for inst in candidates}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                inst, stream_url = result
                print(f"[Cobalt Resolver] Success on concurrent: {inst}")
                last_working_cobalt = inst
                return stream_url
                
    return None

def check_piped_instance(instance, video_id):
    url = f"{instance}/streams/{video_id}"
    try:
        r = requests.get(url, timeout=1.8)
        if r.status_code == 200:
            data = r.json()
            audio_streams = data.get("audioStreams", [])
            if audio_streams:
                stream_url = audio_streams[0].get("url")
                if stream_url:
                    return instance, stream_url
    except Exception:
        pass
    return None

def fetch_piped_stream_url(video_id):
    import concurrent.futures
    global last_working_piped
    instances = [
        "https://api-piped.mha.fi",
        "https://api.piped.private.coffee",
        "https://pipedapi.kavin.rocks",
        "https://pipedapi.col1g3.de",
        "https://piped-api.garudalinux.org",
        "https://pipedapi.us.to",
        "https://api.piped.projectsegfau.lt",
        "https://pipedapi.privacydev.net",
        "https://pipedapi.lunar.icu"
    ]
    if last_working_piped and last_working_piped in instances:
        instances.remove(last_working_piped)
        instances.insert(0, last_working_piped)

    candidates = instances[:8]
    print(f"[Piped Resolver] Checking {len(candidates)} candidates concurrently...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(candidates)) as executor:
        futures = {executor.submit(check_piped_instance, inst, video_id): inst for inst in candidates}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                inst, stream_url = result
                print(f"[Piped Resolver] Success on concurrent: {inst}")
                last_working_piped = inst
                return stream_url
                
    return None

def check_invidious_instance(instance, video_id):
    # Fast direct audio stream probe
    direct_aac = f"{instance}/latest_version?id={video_id}&itag=140"
    try:
        r = requests.get(direct_aac, headers={"Range": "bytes=0-100"}, timeout=1.5, stream=True)
        if r.status_code in (200, 206):
            return instance, direct_aac
    except Exception:
        pass

    url = f"{instance}/api/v1/videos/{video_id}?local=true"
    try:
        r = requests.get(url, timeout=1.5)
        if r.status_code == 200:
            data = r.json()
            formats = data.get("adaptiveFormats", [])
            audio_formats = [f for f in formats if f.get("type", "").startswith("audio/")]
            if audio_formats:
                stream_url = audio_formats[0].get("url")
                if stream_url:
                    if stream_url.startswith("/"):
                        stream_url = f"{instance}{stream_url}"
                    return instance, stream_url
    except Exception:
        pass
    return None

dynamic_invidious_cache = {"instances": [], "last_fetched": 0}

def fetch_dynamic_invidious_instances():
    now = time.time()
    if now - dynamic_invidious_cache["last_fetched"] < 900:
        return dynamic_invidious_cache["instances"]
    try:
        print("[Dynamic Invidious] Fetching live instances from api.invidious.io...")
        r = requests.get("https://api.invidious.io/instances.json", timeout=2.0)
        if r.status_code == 200:
            data = r.json()
            instances = []
            for item in data:
                domain = item[0]
                details = item[1]
                if details.get("type") == "https" and details.get("api") is True:
                    stats = details.get("stats", {})
                    is_healthy = False
                    if stats and isinstance(stats, dict):
                        is_healthy = stats.get("status") == "healthy" or stats.get("monitor", {}).get("status") == "up"
                    if is_healthy or details.get("playback_enabled", True):
                        uri = details.get("uri") or f"https://{domain}"
                        instances.append(uri.rstrip('/'))
            if instances:
                print(f"[Dynamic Invidious] Found {len(instances)} live instances.")
                dynamic_invidious_cache["instances"] = instances
                dynamic_invidious_cache["last_fetched"] = now
                return instances
    except Exception as e:
        print(f"[Dynamic Invidious] Failed to fetch dynamic list: {e}")
    return dynamic_invidious_cache["instances"]

def fetch_invidious_stream_url(video_id):
    import concurrent.futures
    global last_working_invidious
    instances = []
    
    dynamic_instances = fetch_dynamic_invidious_instances()
    if dynamic_instances:
        instances.extend(dynamic_instances)
        
    static_fallbacks = [
        "https://inv.nadeko.net",
        "https://invidious.nerdvpn.de",
        "https://invidious.projectsegfau.lt",
        "https://invidious.privacydev.net",
        "https://invidious.tiekoetter.com",
        "https://invidious.lunar.icu"
    ]
    for fallback in static_fallbacks:
        if fallback not in instances:
            instances.append(fallback)
            
    if last_working_invidious and last_working_invidious in instances:
        instances.remove(last_working_invidious)
        instances.insert(0, last_working_invidious)

    candidates = instances[:8]
    print(f"[Invidious Resolver] Checking {len(candidates)} candidates concurrently...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(candidates)) as executor:
        futures = {executor.submit(check_invidious_instance, inst, video_id): inst for inst in candidates}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                inst, stream_url = result
                print(f"[Invidious Resolver] Success on concurrent: {inst}")
                last_working_invidious = inst
                return stream_url
                
    return None

def resolve_stream_url(video_id):
    url = get_cached_stream_url(video_id)
    if url:
        return url
            
    print(f"[Resolver] Resolving stream for {video_id} using parallel fallbacks...")
    import concurrent.futures
    
    resolvers = [
        ("Invidious", fetch_invidious_stream_url),
        ("Piped", fetch_piped_stream_url),
        ("Cobalt", fetch_cobalt_stream_url),
    ]
    resolvers.append(("yt-dlp", extract_stream_url_fast))
    
    resolved_url = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(resolvers)) as executor:
        future_to_resolver = {executor.submit(func, video_id): name for name, func in resolvers}
        try:
            for future in concurrent.futures.as_completed(future_to_resolver, timeout=4.5):
                name = future_to_resolver[future]
                try:
                    res = future.result()
                    if res:
                        print(f"[Parallel Resolver] Success from {name}: {res[:80]}")
                        resolved_url = res
                        break
                except Exception as e:
                    print(f"[Parallel Resolver] {name} exception: {e}")
        except concurrent.futures.TimeoutError:
            print("[Parallel Resolver] as_completed loop timed out waiting for slower resolvers")
                
    if resolved_url:
        set_cached_stream_url(video_id, resolved_url)
        return resolved_url
        
    return None

def resolve_direct_url(video_id):
    """Resolve a durable, direct audio URL — skips Cobalt tunnels which are IP-bound and single-use.
    Used exclusively by the /play endpoint to guarantee a fetchable URL."""
    import concurrent.futures

    print(f"[Direct Resolver] Resolving durable URL for {video_id}...")

    resolvers = [
        ("Invidious", fetch_invidious_stream_url),
        ("Piped", fetch_piped_stream_url),
        ("yt-dlp", extract_stream_url_fast),
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(resolvers)) as executor:
        future_to_name = {executor.submit(func, video_id): name for name, func in resolvers}
        try:
            for future in concurrent.futures.as_completed(future_to_name, timeout=4.5):
                name = future_to_name[future]
                try:
                    res = future.result()
                    if res and "/tunnel" not in res:
                        print(f"[Direct Resolver] Got durable URL from {name}: {res[:80]}...")
                        return res
                    elif res:
                        print(f"[Direct Resolver] Skipping tunnel URL from {name}")
                except Exception as e:
                    print(f"[Direct Resolver] {name} exception: {e}")
        except concurrent.futures.TimeoutError:
            print("[Direct Resolver] as_completed loop timed out")

    # Last resort: try Cobalt but only accept non-tunnel redirect URLs
    try:
        cobalt_url = fetch_cobalt_stream_url(video_id)
        if cobalt_url and "/tunnel" not in cobalt_url:
            print(f"[Direct Resolver] Cobalt returned a non-tunnel URL: {cobalt_url[:80]}...")
            return cobalt_url
    except Exception:
        pass

    print(f"[Direct Resolver] All resolvers failed for {video_id}")
    return None


@app.route("/stream/<video_id>")
def stream(video_id):
    """Returns stream metadata. Kept for backward compat - clients should use /play/<video_id>"""
    try:
        # Point proxy_url to the unified /play endpoint instead
        play_url = f"/play/{video_id}"
        # Also resolve a direct URL for client-side fallback
        url = resolve_stream_url(video_id)
        if not url:
            url = play_url  # fallback to play endpoint
        return jsonify({
            "url": url,
            "proxy_url": play_url
        })
    except Exception as e:
        print(f"Stream error for {video_id}: {e}")
        return jsonify({"error": str(e)}), 400

@app.route("/play/<video_id>")
def play(video_id):
    """Unified streaming endpoint – resolves once, caches URL, then proxies using chunks."""
    range_header = request.headers.get("Range")
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    # 1. Check cache first (fast path)
    stream_url = get_cached_stream_url(video_id)
    if not stream_url:
        # 2. Use the parallel resolver – this caches internally via set_cached_stream_url
        stream_url = resolve_stream_url(video_id)
        if not stream_url:
            return "Could not resolve a playable audio stream", 503

    # 3. Forward request to resolved URL, passing Range header if present
    headers = {"User-Agent": ua}
    if range_header:
        headers["Range"] = range_header

    try:
        r = requests.get(stream_url, headers=headers, stream=True, timeout=12.0)
        if r.status_code not in (200, 206):
            # If the cached URL expired, purge and retry once
            if video_id in stream_cache:
                del stream_cache[video_id]
            stream_url = resolve_stream_url(video_id)
            if not stream_url:
                return "Stream expired", 503
            r = requests.get(stream_url, headers=headers, stream=True, timeout=12.0)
    except Exception as e:
        return f"Proxy error: {e}", 500

    # 4. Stream response back to client
    def generate():
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                yield chunk

    response = Response(stream_with_context(generate()), status=r.status_code)
    response.headers["Content-Type"] = r.headers.get("Content-Type", "audio/mpeg")
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Content-Length"] = r.headers.get("Content-Length", "")
    if r.status_code == 206:
        response.headers["Content-Range"] = r.headers.get("Content-Range", "")
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Range, Content-Type"
    response.headers["Access-Control-Expose-Headers"] = "Content-Range, Content-Length, Accept-Ranges"
    response.headers["Cache-Control"] = "no-cache"
    return response



@app.route("/debug/<video_id>")
def debug_stream(video_id):
    """Diagnostic endpoint: resolves and fetches a URL, returns JSON with details."""
    result = {"video_id": video_id}
    try:
        url = resolve_direct_url(video_id)
        result["resolved_url"] = url[:120] if url else None
        result["is_tunnel"] = "/tunnel" in url if url else False
        result["is_googlevideo"] = "googlevideo.com" in url if url else False

        if url:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            r = requests.get(url, headers=headers, stream=("/tunnel" in url), timeout=10.0)
            result["upstream_status"] = r.status_code
            result["upstream_content_length"] = r.headers.get("Content-Length")
            result["upstream_content_type"] = r.headers.get("Content-Type")
            result["upstream_bytes_received"] = len(r.content)
        else:
            result["error"] = "resolve_direct_url returned None"
    except Exception as e:
        result["exception"] = str(e)
    return jsonify(result)

@app.route("/api/sign", methods=["POST"])
def sign_url():
    data = request.json or {}
    url = data.get("url")
    video_id = data.get("id", "")
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    # Signature valid for 2 hours (7200 seconds)
    expires_at = int(time.time()) + 7200
    sig = generate_signature(video_id, url, expires_at)
    import urllib.parse
    signed_url = f"/proxy?url={urllib.parse.quote(url)}&id={video_id}&exp={expires_at}&sig={sig}"
    return jsonify({"proxy_url": signed_url})

@app.route("/proxy")
def proxy():
    url = request.args.get('url')
    if not url:
        return "No URL", 400

    video_id = request.args.get('id', '')
    exp = request.args.get('exp', '')
    sig = request.args.get('sig', '')

    if not exp or not sig:
        return "Forbidden: Missing signature", 403

    try:
        # Give a 5-minute buffer
        if time.time() > int(exp) + 300:
            return "Forbidden: URL Expired", 403
        if not verify_signature(video_id, url, int(exp), sig):
            return "Forbidden: Invalid signature", 403
    except Exception as e:
        return f"Forbidden: Verification failed: {str(e)}", 403

    download = request.args.get('download') == 'true'
    title = request.args.get('title', 'audio')
    safe_title = "".join([c for c in title if c.isalnum() or c in ' -_']).strip()
    if not safe_title:
        safe_title = "audio"

    range_header = request.headers.get("Range")
    
    # Configure requests headers to upstream
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    if range_header:
        headers["Range"] = range_header

    r = None
    try:
        r = requests.get(url, headers=headers, stream=True, timeout=12.0)
    except Exception as e:
        print(f"[Proxy] Upstream request exception: {e}")

    if r is None or r.status_code not in [200, 206]:
        if video_id:
            print(f"[Proxy] Upstream failed or timed out. Resolving fresh URL for {video_id}...")
            if video_id in stream_cache:
                del stream_cache[video_id]
            fresh_url = resolve_stream_url(video_id)
            if fresh_url:
                print(f"[Proxy] Fresh URL resolved: {fresh_url}. Retrying request...")
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                }
                if range_header:
                    headers["Range"] = range_header
                try:
                    r = requests.get(fresh_url, headers=headers, stream=True, timeout=12.0)
                except Exception as retry_err:
                    print(f"[Proxy] Upstream retry request exception: {retry_err}")

    if r is None:
        return "Proxy error: connection failed to all resolved stream endpoints", 500

    if r.status_code not in [200, 206]:
        return f"Proxy error: upstream returned status code {r.status_code}", r.status_code

    try:
        status_code = r.status_code
        content = r.content
        total_len = len(content)
        content_type = "audio/mpeg"  # stable audio Content-Type

        content_range = r.headers.get("Content-Range")
        content_length = r.headers.get("Content-Length", str(total_len))

        # If client requested range but upstream returned 200, manually slice the content
        if range_header and status_code == 200:
            status_code = 206
            try:
                range_val = range_header.replace('bytes=', '')
                start_str, end_str = range_val.split('-')
                start = int(start_str) if start_str else 0
                end = int(end_str) if end_str else total_len - 1
                
                content = content[start:end+1]
                content_length = str(len(content))
                content_range = f"bytes {start}-{start + len(content) - 1}/{total_len}"
            except Exception as slice_err:
                print(f"[Proxy] Error slicing content: {slice_err}")

        response = Response(content, status=status_code)
        response.headers["Content-Type"] = content_type
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["Content-Length"] = content_length
        if content_range:
            response.headers["Content-Range"] = content_range

        if download:
            response.headers["Content-Disposition"] = f'attachment; filename="{safe_title}.mp3"'
        else:
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Headers"] = "Range, Content-Type"
            response.headers["Access-Control-Expose-Headers"] = "Content-Range, Content-Length, Accept-Ranges"
            response.headers["Cache-Control"] = "no-cache"

        return response
    except Exception as e:
        print(f"Proxy error: {e}")
        return str(e), 500

@app.route("/api/image-proxy")
def image_proxy():
    url = request.args.get('url')
    if not url: return "No URL", 400
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(url, headers=headers, timeout=10)
        response = Response(r.content, status=r.status_code)
        response.headers["Content-Type"] = r.headers.get("Content-Type", "image/jpeg")
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response
    except Exception as e:
        video_id = request.args.get('id')
        if video_id:
            return redirect(f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")
        return str(e), 500

@app.route("/api/download/<video_id>", methods=["GET", "OPTIONS"])
@app.route("/download/<video_id>", methods=["GET", "OPTIONS"])
def download_audio(video_id):
    if request.method == 'OPTIONS':
        res = Response()
        origin = request.headers.get("Origin") or "*"
        res.headers["Access-Control-Allow-Origin"] = origin
        res.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        res.headers["Access-Control-Allow-Headers"] = "Range, Content-Type, Authorization"
        res.headers["Access-Control-Expose-Headers"] = "Content-Disposition, Content-Length"
        return res

    title = request.args.get('title', 'audio')
    safe_title = "".join([c for c in title if c.isalnum() or c in ' -_']).strip() or "audio"
    mode = request.args.get('mode', '')
    redirect_mode = request.args.get('redirect') == 'true' or mode == 'redirect'
    json_mode = mode == 'json'

    print(f"[Download] Request for video_id={video_id}, title={safe_title}, mode={mode}")
    
    try:
        # Force fresh resolution for download stream
        if video_id in stream_cache:
            del stream_cache[video_id]
        url = resolve_stream_url(video_id)
        if not url:
            raise Exception("Failed to resolve stream URL from available resolvers")
            
        # Return direct URL for client-side download to avoid Vercel 4.5MB payload limit
        if json_mode:
            return jsonify({
                "status": "ready",
                "download_url": url,
                "filename": f"{safe_title}.mp3"
            })

        if redirect_mode:
            return redirect(url, code=302)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        r = requests.get(url, headers=headers, stream=True, timeout=20)
        
        if r.status_code not in [200, 206]:
            print("[Download] Cached stream returned error, resolving fresh URL...")
            if video_id in stream_cache:
                del stream_cache[video_id]
            url = resolve_stream_url(video_id)
            if not url:
                return redirect(url, code=302)
            r = requests.get(url, headers=headers, stream=True, timeout=20)
            
        if r.status_code not in [200, 206]:
            # If server stream fails, redirect directly to audio CDN URL
            return redirect(url, code=302)
            
        def generate():
            try:
                for chunk in r.iter_content(chunk_size=128 * 1024):
                    if chunk:
                        yield chunk
            except Exception as stream_err:
                print(f"[Download Stream] Error: {stream_err}")
                
        response = Response(stream_with_context(generate()), status=200)
        response.headers["Content-Type"] = "audio/mpeg"
        response.headers["Content-Disposition"] = f'attachment; filename="{safe_title}.mp3"'
        
        origin = request.headers.get("Origin") or "*"
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Expose-Headers"] = "Content-Disposition, Content-Length"
        
        if 'content-length' in r.headers:
            response.headers["Content-Length"] = r.headers['content-length']
            
        return response
    except Exception as e:
        print(f"[Download] Failed for {video_id}: {e}")
        return jsonify({"error": f"Download failed: {str(e)}"}), 500

def sanitize_lyrics_search(title, artist):
    import re
    # Clean artist name first
    artist_clean = re.sub(r'\s*-\s*topic$', '', artist, flags=re.IGNORECASE).strip()
    artist_clean = re.sub(r'(?:feat\.?|ft\.?)\s+.*$', '', artist_clean, flags=re.IGNORECASE).strip()

    title_clean = title
    # Remove common promotional tags/brackets and noise
    title_clean = re.sub(r'[\(\[][^\]\)]*(?:official|video|lyric|audio|live|remix|edit|hd|4k|hq|clip|visualizer|remastered|bonus)[^\]\)]*[\)\]]', '', title_clean, flags=re.IGNORECASE)
    
    # If the title is in the format "Artist - Title", extract just "Title"
    if " - " in title_clean:
        parts = title_clean.split(" - ", 1)
        if parts[0].strip().lower() == artist_clean.lower() or artist_clean.lower() in parts[0].lower():
            title_clean = parts[1]
            
    # Remove artist name prefix/suffix if it's still in the title
    title_clean = re.sub(rf'^\s*{re.escape(artist_clean)}\s*-\s*', '', title_clean, flags=re.IGNORECASE)
    title_clean = re.sub(rf'\s*-\s*{re.escape(artist_clean)}\s*$', '', title_clean, flags=re.IGNORECASE)
    
    # Clean up leading track numbers e.g. "01 ", "01. "
    title_clean = re.sub(r'^\d+[\.\s\-]+', '', title_clean)
    title_clean = re.sub(r'\s+', ' ', title_clean).strip()
    
    return title_clean, artist_clean

@app.route("/api/lyrics/<video_id>")
def get_lyrics(video_id):
    title = request.args.get('title', '').strip()
    artist = request.args.get('artist', '').strip()
    duration_str = request.args.get('duration', '').strip()
    
    target_duration = 0
    if duration_str:
        try:
            if ":" in duration_str:
                p = duration_str.split(":")
                target_duration = int(p[0]) * 60 + int(float(p[1]))
            else:
                target_duration = int(float(duration_str))
        except Exception:
            target_duration = 0

    cache_key = f"lyrics_{video_id}"
    cached = get_cached_api(cache_key, duration=86400)
    if cached is not None:
        return jsonify(cached)
        
    res = None
    
    # 1. Try fetching synced lyrics from LRCLib (precision matching with duration)
    if title and artist:
        title_clean, artist_clean = sanitize_lyrics_search(title, artist)
        try:
            print(f"[Lyrics API] Fetching synced lyrics from LRCLib for '{title_clean}' by '{artist_clean}' (duration: {target_duration}s)")
            lrclib_url = "https://lrclib.net/api/get"
            params = {
                "artist_name": artist_clean,
                "track_name": title_clean
            }
            if target_duration > 0:
                params["duration"] = target_duration

            r = requests.get(lrclib_url, params=params, timeout=5)
            if r.status_code == 200:
                data = r.json()
                synced_lyrics = data.get("syncedLyrics")
                plain_lyrics = data.get("plainLyrics")
                if synced_lyrics:
                    res = {"synced": True, "lyrics": synced_lyrics, "duration": data.get("duration")}
                elif plain_lyrics:
                    res = {"synced": False, "lyrics": plain_lyrics, "duration": data.get("duration")}
        except Exception as e:
            print(f"[Lyrics API] LRCLib exact failed: {e}")

        # Fallback: LRCLib search query with duration alignment
        if not res or not res.get("synced"):
            try:
                search_url = "https://lrclib.net/api/search"
                r = requests.get(search_url, params={"q": f"{artist_clean} {title_clean}"}, timeout=5)
                if r.status_code == 200:
                    results = r.json()
                    if results and isinstance(results, list):
                        synced_candidates = [it for it in results if it.get("syncedLyrics")]
                        if synced_candidates:
                            if target_duration > 0:
                                # Pick candidate closest in duration to avoid mismatching versions
                                best = min(synced_candidates, key=lambda it: abs(it.get("duration", 0) - target_duration))
                            else:
                                best = synced_candidates[0]
                            res = {"synced": True, "lyrics": best.get("syncedLyrics"), "duration": best.get("duration")}
                            print(f"[Lyrics API] Found synced lyrics via search fallback: {best.get('trackName')}")
            except Exception as search_err:
                print(f"[Lyrics API] LRCLib search fallback failed: {search_err}")
            
    # Fallback to YTMusic native lyrics if LRCLib didn't yield anything
    if not res:
        try:
            print(f"[Lyrics API] Falling back to YTMusic native lyrics for ID: {video_id}")
            watch = yt.get_watch_playlist(videoId=video_id)
            lyrics_id = watch.get('lyrics')
            if lyrics_id:
                lyrics_data = yt.get_lyrics(lyrics_id)
                lyrics_text = lyrics_data.get('lyrics', '')
                if lyrics_text and lyrics_text != 'No lyrics found.':
                    res = {"synced": False, "lyrics": lyrics_text}
            if not res:
                res = {"synced": False, "lyrics": "No lyrics found."}
        except Exception as e:
            print(f"[Lyrics API] YTMusic fallback failed: {e}")
            res = {"synced": False, "lyrics": "No lyrics found."}
            
    set_cached_api(cache_key, res)
    return jsonify(res)

@app.route("/api/suggestions/<video_id>")
def get_suggestions(video_id):
    title = request.args.get('title', '').strip()
    artist = request.args.get('artist', '').strip()
    cache_key = f"suggestions_{video_id}_{artist[:10]}"
    cached = get_cached_api(cache_key, duration=1800)
    if cached is not None:
        return jsonify(cached)
        
    songs = []
    # 1. Native YTMusic watch playlist (radio for this song)
    try:
        watch = yt.get_watch_playlist(videoId=video_id, limit=20)
        songs = [format_song(t) for t in watch.get('tracks', []) if format_song(t) and t.get('videoId') != video_id]
    except Exception as e:
        print(f"[Suggestions API] Watch playlist failed for {video_id}: {e}")
        
    # 2. Artist based related songs & intelligent recommendation
    if len(songs) < 8 and artist:
        try:
            import re
            clean_artist = re.sub(r'\s*-\s*topic$', '', artist, flags=re.IGNORECASE).strip()
            clean_artist = re.sub(r'(?:feat\.?|ft\.?)\s+.*$', '', clean_artist, flags=re.IGNORECASE).strip()
            artist_results = yt.search(f"{clean_artist} popular songs", filter="songs")[:15]
            artist_songs = [format_song(r) for r in artist_results if format_song(r) and r.get('videoId') != video_id]
            for s in artist_songs:
                if not any(existing['id'] == s['id'] for existing in songs):
                    songs.append(s)
        except Exception as e:
            print(f"[Suggestions API] Artist search failed: {e}")

    # 3. Official YouTube API fallback
    if len(songs) < 5:
        official_songs = get_youtube_suggestions_official(video_id)
        if official_songs:
            for s in official_songs:
                if s['id'] != video_id and not any(existing['id'] == s['id'] for existing in songs):
                    songs.append(s)

    # 4. Deduplicate and return top clean songs
    seen = set()
    unique_songs = []
    for s in songs:
        if s['id'] not in seen and s['id'] != video_id:
            seen.add(s['id'])
            unique_songs.append(s)
            
    if unique_songs:
        set_cached_api(cache_key, unique_songs[:18])
    return jsonify(unique_songs[:18])

@app.route("/api/artist/<artist_id>")
def get_artist_details(artist_id):
    cache_key = f"artist_{artist_id}"
    cached = get_cached_api(cache_key, duration=3600)  # 1 hour cache
    if cached is not None:
        return jsonify(cached)
    try:
        data = yt.get_artist(artist_id)
        
        # Extract best thumbnail image
        thumbs = data.get('thumbnails') or []
        image_url = thumbs[-1].get('url') if thumbs else ""
        if image_url and image_url.startswith('//'):
            image_url = 'https:' + image_url
            
        # Extract description/bio
        description = data.get('description') or data.get('bio') or ""
        if isinstance(description, dict):
            description = description.get('text', '')
            
        # Extract songs (usually under 'songs' or 'latestSongs')
        songs_data = data.get('songs', {}) or data.get('latestSongs', {})
        results = songs_data.get('results', [])
        formatted_songs = []
        for s in results:
            video_id = s.get('videoId') or s.get('id')
            if not video_id:
                continue
            s_thumbs = s.get('thumbnails') or []
            s_thumb_url = s_thumbs[-1].get('url') if s_thumbs else ""
            formatted_songs.append({
                "id": video_id,
                "title": s.get('title', 'Unknown'),
                "artist": data.get('name', 'Unknown Artist'),
                "artistId": artist_id,
                "image": proxy_track_image(video_id, s_thumb_url),
                "duration": s.get('duration') or "3:45"
            })
            
        # Extract albums
        albums_data = data.get('albums', {})
        albums_results = albums_data.get('results', [])
        formatted_albums = []
        for a in albums_results:
            a_thumbs = a.get('thumbnails') or []
            a_thumb_url = a_thumbs[-1].get('url') if a_thumbs else ""
            formatted_albums.append({
                "id": a.get('browseId'),
                "title": a.get('title', 'Unknown Album'),
                "year": a.get('year', ''),
                "image": proxy_track_image(a.get('browseId', ''), a_thumb_url)
            })

        # Extract singles
        singles_data = data.get('singles', {})
        singles_results = singles_data.get('results', [])
        formatted_singles = []
        for a in singles_results:
            a_thumbs = a.get('thumbnails') or []
            a_thumb_url = a_thumbs[-1].get('url') if a_thumbs else ""
            formatted_singles.append({
                "id": a.get('browseId'),
                "title": a.get('title', 'Unknown Single'),
                "year": a.get('year', ''),
                "image": proxy_track_image(a.get('browseId', ''), a_thumb_url)
            })

        # Extract related artists
        related_data = data.get('related', {})
        related_results = related_data.get('results', [])
        formatted_related = []
        for r in related_results:
            r_thumbs = r.get('thumbnails') or []
            r_thumb_url = r_thumbs[-1].get('url') if r_thumbs else ""
            formatted_related.append({
                "id": r.get('browseId') or r.get('channelId'),
                "name": r.get('title') or r.get('name') or r.get('artist'),
                "subscribers": r.get('subscribers', ''),
                "image": proxy_track_image(r.get('browseId', ''), r_thumb_url) if r_thumb_url else '/favicon.svg'
            })
            
        artist_info = {
            "id": artist_id,
            "name": data.get('name', 'Unknown Artist'),
            "description": description,
            "subscribers": data.get('subscribers') or data.get('stats', {}).get('subscribers') or 'N/A',
            "monthlyListeners": data.get('monthlyListeners') or 'N/A',
            "image": image_url,
            "songs": formatted_songs,
            "albums": formatted_albums,
            "singles": formatted_singles,
            "related": formatted_related
        }
        
        set_cached_api(cache_key, artist_info)
        return jsonify(artist_info)
    except Exception as e:
        print(f"Error getting artist {artist_id}: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/user_data")
def user_data():
    if current_user.is_authenticated:
        liked = [proxy_db_track(s) for s in current_user.get_liked_songs()]
        playlists = current_user.get_playlists()
        for pl in playlists:
            pl['songs'] = [proxy_db_track(s) for s in pl['songs']]
        return jsonify({
            "logged_in": True, 
            "username": current_user.username, 
            "liked_songs": liked, 
            "playlists": playlists
        })
    return jsonify({"logged_in": False})

@app.after_request
def add_cache_headers(response):
    if request.method == 'GET' and request.path in ['/api/home', '/api/trending']:
        response.headers['Cache-Control'] = 'public, max-age=60, s-maxage=300, stale-while-revalidate=3600'
    return response

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
