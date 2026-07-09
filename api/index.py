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
CORS(app, supports_credentials=True)

# Production cookie settings for cross-origin authentication
if os.environ.get('FLASK_ENV') == 'production' or os.environ.get('DATABASE_URL') or os.environ.get('VERCEL'):
    app.config.update(
        SESSION_COOKIE_SAMESITE='None',
        SESSION_COOKIE_SECURE=True
    )

# ---------- DATABASE SETUP ----------
db_url = os.environ.get('DATABASE_URL')
if db_url:
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url
else:
    # Use writeable /tmp path on Vercel serverless environment, otherwise local instance
    if os.environ.get('VERCEL') or os.environ.get('FLASK_ENV') == 'production':
        app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:////tmp/users.db'
    else:
        # Resolve to instance directory in the parent folder
        instance_dir = os.path.join(base_dir, 'instance')
        os.makedirs(instance_dir, exist_ok=True)
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{os.path.join(instance_dir, "users.db")}'

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

with app.app_context():
    db.create_all()

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
        else:
            artist = 'Various Artists'
            
        thumbs = item.get('thumbnails') or []
        thumb_url = thumbs[-1].get('url') if thumbs else ""
        
        image_url = proxy_track_image(video_id, thumb_url)
        return {"id": video_id, "title": title, "artist": artist, "image": image_url, "duration": item.get('duration', '3:45')}
    except Exception: return None

@app.route("/api/register", methods=["POST"])
def register():
    username = request.form.get("username")
    email = request.form.get("email")
    password = request.form.get("password")
    
    if User.query.filter_by(username=username).first():
        return jsonify({"error": "Username already exists"}), 400
        
    user = User(username=username, email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    login_user(user)
    return jsonify({"success": True})

@app.route("/api/login", methods=["POST"])
def login():
    username = request.form.get("username")
    password = request.form.get("password")
    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password):
        login_user(user, remember=True)
        return jsonify({"success": True})
    return jsonify({"error": "Invalid credentials"}), 401

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

@app.route("/api/search")
def search():
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
def extract_stream_url(video_id):
    clients = [
        ['android'],
        ['ios'],
        ['mweb'],
        ['tv']
    ]
    
    for client in clients:
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'ignoreerrors': True,
            'cachedir': False,
            'noconfig': True,
            'extractor_args': {
                'youtube': {
                    'client': client
                }
            }
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
                    
                search_info = ydl.extract_info(f"ytsearch:{video_id}", download=False)
                if search_info and 'entries' in search_info and len(search_info['entries']) > 0:
                    first_entry = search_info['entries'][0]
                    if first_entry and 'url' in first_entry:
                        return first_entry['url']
        except Exception as e:
            print(f"Extraction failed for client {client}: {e}")
            
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'ignoreerrors': True,
            'cachedir': False,
            'noconfig': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            if info and 'url' in info:
                return info['url']
            search_info = ydl.extract_info(f"ytsearch:{video_id}", download=False)
            if search_info and 'entries' in search_info and len(search_info['entries']) > 0:
                return search_info['entries'][0]['url']
    except Exception as e:
        print(f"Fallback extraction failed: {e}")
        
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

def fetch_cobalt_stream_url(video_id):
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
    data = {
        "url": f"https://www.youtube.com/watch?v={video_id}"
    }

    for instance in instances:
        endpoints = [instance, f"{instance}/", f"{instance}/api/json"]
        for endpoint in endpoints:
            try:
                print(f"[Cobalt Resolver] Attempting: {endpoint} for video_id={video_id}")
                r = requests.post(endpoint, headers=headers, json=data, timeout=1.5)
                if r.status_code == 200:
                    res_data = r.json()
                    stream_url = res_data.get("url") or res_data.get("picker")
                    if stream_url:
                        print(f"[Cobalt Resolver] Success on: {endpoint}")
                        last_working_cobalt = instance
                        return stream_url
            except Exception as e:
                pass
                
    return None

def fetch_piped_stream_url(video_id):
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

    for instance in instances:
        url = f"{instance}/streams/{video_id}"
        try:
            print(f"[Piped Resolver] Querying instance: {url}")
            r = requests.get(url, timeout=1.5)
            if r.status_code == 200:
                data = r.json()
                audio_streams = data.get("audioStreams", [])
                if audio_streams:
                    stream_url = audio_streams[0].get("url")
                    if stream_url:
                        print(f"[Piped Resolver] Success on: {instance}")
                        last_working_piped = instance
                        return stream_url
        except Exception as e:
            pass
            
    return None

def fetch_invidious_stream_url(video_id):
    global last_working_invidious
    instances = [
        "https://inv.nadeko.net",
        "https://invidious.nerdvpn.de",
        "https://invidious.f5.si",
        "https://yt.chocolatemoo53.com",
        "https://inv.zoomerville.com",
        "https://invidious.tiekoetter.com",
        "https://invidious.projectsegfau.lt",
        "https://invidious.privacydev.net",
        "https://invidious.lunar.icu"
    ]
    if last_working_invidious and last_working_invidious in instances:
        instances.remove(last_working_invidious)
        instances.insert(0, last_working_invidious)

    for instance in instances:
        url = f"{instance}/api/v1/videos/{video_id}?local=true"
        try:
            print(f"[Invidious Resolver] Querying: {url}")
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
                        print(f"[Invidious Resolver] Success on: {instance}")
                        last_working_invidious = instance
                        return stream_url
        except Exception as e:
            pass
            
    return None

def resolve_stream_url(video_id):
    url = get_cached_stream_url(video_id)
    if url:
        return url
        
    # Skip yt-dlp extraction on Vercel/Production to prevent serverless function timeouts
    is_production = os.environ.get('VERCEL') or os.environ.get('FLASK_ENV') == 'production'
    if not is_production:
        url = extract_stream_url(video_id)
    else:
        print(f"[Resolver] Skipping local yt-dlp extraction on production/Vercel (prevents 10s function timeout). Trying fallbacks...")
        
    if not url:
        print(f"[Resolver] yt-dlp failed or skipped for {video_id}, trying Cobalt fallback...")
        url = fetch_cobalt_stream_url(video_id)
        
    if not url:
        print(f"[Resolver] Cobalt failed for {video_id}, trying Piped fallback...")
        url = fetch_piped_stream_url(video_id)
        
    if not url:
        print(f"[Resolver] Piped failed for {video_id}, trying Invidious fallback...")
        url = fetch_invidious_stream_url(video_id)
        
    if url:
        set_cached_stream_url(video_id, url)
        return url
        
    return None

@app.route("/stream/<video_id>")
def stream(video_id):
    try:
        url = resolve_stream_url(video_id)
        if not url:
            raise Exception("Failed to resolve stream URL from all sources")
            
        expires_at = int(time.time()) + 1800  # 30 minutes validity
        sig = generate_signature(video_id, url, expires_at)
        proxy_url = f"/proxy?url={requests.utils.quote(url)}&id={video_id}&exp={expires_at}&sig={sig}"
        
        return jsonify({
            "url": url, 
            "proxy_url": proxy_url
        })
    except Exception as e:
        print(f"Stream error for {video_id}: {e}")
        return jsonify({"error": str(e)}), 400

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
        # Allow a 5-minute (300s) clock skew grace period for serverless instances
        if time.time() > int(exp) + 300:
            return "Forbidden: URL Expired", 403
        if not verify_signature(video_id, url, int(exp), sig):
            return "Forbidden: Invalid signature", 403
    except Exception as e:
        return f"Forbidden: Verification failed: {str(e)}", 403

    download = request.args.get('download') == 'true'
    title = request.args.get('title', 'audio')
    # Clean filename
    safe_title = "".join([c for c in title if c.isalnum() or c in ' -_']).strip()
    if not safe_title:
        safe_title = "audio"

    range_header = request.headers.get("Range")

    # 2.5 MB maximum response chunk size to stay safely within Vercel's 4.5 MB payload limit
    MAX_CHUNK_SIZE = 2500000

    start = 0
    end = None
    is_range = False

    if range_header:
        is_range = True
        try:
            range_val = range_header.replace('bytes=', '')
            start_str, end_str = range_val.split('-')
            start = int(start_str) if start_str else 0
            if end_str:
                end = int(end_str)
        except Exception as e:
            print(f"Error parsing range header: {e}")

    # Enforce 2.5MB chunk size limit for the upstream request
    if end is None:
        end = start + MAX_CHUNK_SIZE - 1
    elif end - start + 1 > MAX_CHUNK_SIZE:
        end = start + MAX_CHUNK_SIZE - 1

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Range": f"bytes={start}-{end}"
    }

    r = None
    try:
        r = requests.get(url, headers=headers, stream=True, timeout=10)
    except Exception as e:
        print(f"[Proxy] Upstream request exception: {e}")

    # Transparently resolve fresh URL if request failed, timed out, or returned forbidden/expired status
    if r is None or r.status_code not in [200, 206]:
        if video_id:
            print(f"[Proxy] Upstream failed or returned invalid status. Resolving fresh URL for {video_id}...")
            if video_id in stream_cache:
                del stream_cache[video_id]
            fresh_url = resolve_stream_url(video_id)
            if fresh_url:
                print(f"[Proxy] Fresh URL resolved: {fresh_url}. Retrying request...")
                try:
                    r = requests.get(fresh_url, headers=headers, stream=True, timeout=10)
                except Exception as retry_err:
                    print(f"[Proxy] Upstream retry request exception: {retry_err}")

    if r is None:
        return "Proxy error: connection failed to all resolved stream endpoints", 500

    try:
        status_code = r.status_code
        content = r.content
        total_len = len(content)

        content_type = r.headers.get("Content-Type", "audio/mpeg")
        
        # If upstream responded with 206 Partial Content, we mirror its range headers
        if status_code == 206:
            content_range = r.headers.get("Content-Range")
            content_length = r.headers.get("Content-Length", str(total_len))
        else:
            # If upstream returned 200, we slice the content in memory and return 206
            status_code = 206
            sliced = content[start:end+1]
            content_range = f"bytes {start}-{start + len(sliced) - 1}/{total_len}"
            content_length = str(len(sliced))
            content = sliced

        response = Response(content, status=status_code)
        response.headers["Content-Type"] = content_type
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["Content-Length"] = content_length
        if content_range:
            response.headers["Content-Range"] = content_range

        if download:
            response.headers["Content-Disposition"] = f'attachment; filename="{safe_title}.mp3"'
            response.headers["Content-Type"] = "audio/mpeg"
        else:
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Headers"] = "Range, Content-Type"
            response.headers["Access-Control-Expose-Headers"] = "Content-Range, Content-Length, Accept-Ranges"

        # Copy through additional safe headers from upstream
        for k, v in r.headers.items():
            lk = k.lower()
            if lk in ['content-encoding', 'transfer-encoding', 'connection', 'access-control-allow-origin', 'content-disposition', 'content-type', 'accept-ranges', 'content-length', 'content-range']:
                continue
            response.headers[k] = v

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

@app.route("/api/download/<video_id>")
def download_audio(video_id):
    title = request.args.get('title', 'audio')
    # Clean filename
    safe_title = "".join([c for c in title if c.isalnum() or c in ' -_']).strip()
    if not safe_title:
        safe_title = "audio"
    print(f"[Download] Request received for video_id={video_id}, title={title}")
    
    is_production = os.environ.get('VERCEL') or os.environ.get('FLASK_ENV') == 'production'
    
    try:
        url = resolve_stream_url(video_id)
        if not url:
            raise Exception("Failed to resolve stream URL from all sources")
            
        if is_production:
            # On production Vercel, bypass the 4.5MB serverless response payload limits
            # by redirecting the client's browser directly to the stream provider link
            print(f"[Download] Production mode: Redirecting directly to stream URL: {url}")
            return redirect(url)
            
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        r = requests.get(url, headers=headers, stream=True, timeout=20)
        
        if r.status_code not in [200, 206]:
            # If the resolved URL is expired or forbidden (e.g. from cache), force a fresh resolution
            print("[Download] Cached stream returned error, resolving fresh URL...")
            if video_id in stream_cache:
                del stream_cache[video_id]
            url = resolve_stream_url(video_id)
            if not url:
                raise Exception("Failed to resolve fresh stream URL")
            r = requests.get(url, headers=headers, stream=True, timeout=20)
            
        if r.status_code not in [200, 206]:
            raise Exception(f"YouTube stream returned status code {r.status_code}")
            
        print("[Download] Preparing response stream...")
        def generate():
            print("[Download Stream] Starting stream generator")
            try:
                for chunk in r.iter_content(chunk_size=128*1024):  # 128KB buffer for streaming
                    if chunk:
                        yield chunk
            except Exception as stream_err:
                print(f"[Download Stream] Error during streaming: {stream_err}")
            finally:
                print("[Download Stream] Stream generator finished")
                
        response = Response(stream_with_context(generate()), status=200)
        response.headers["Content-Type"] = "audio/mpeg"
        response.headers["Content-Disposition"] = f'attachment; filename="{safe_title}.mp3"'
        
        if 'content-length' in r.headers:
            response.headers["Content-Length"] = r.headers['content-length']
            print(f"[Download] Content-Length is {r.headers['content-length']}")
            
        print("[Download] Response ready. Returning to client...")
        return response
    except Exception as e:
        print(f"[Download] Failed for {video_id}: {e}")
        return jsonify({"error": f"Download failed: {str(e)}"}), 500

def sanitize_lyrics_search(title, artist):
    import re
    # Clean artist name first
    artist_clean = re.sub(r'\s*-\s*topic$', '', artist, flags=re.IGNORECASE)
    artist_clean = re.sub(r'(?:feat\.?|ft\.?)\s+.*$', '', artist_clean, flags=re.IGNORECASE).strip()

    title_clean = title
    # Remove common promotional tags/brackets
    title_clean = re.sub(r'[\(\[][^\]\)]*(?:official|video|lyric|audio|live|remix|edit|hd|4k|hq|clip)[^\]\)]*[\)\]]', '', title_clean, flags=re.IGNORECASE)
    
    # If the title is in the format "Artist - Title", extract just "Title"
    if " - " in title_clean:
        parts = title_clean.split(" - ", 1)
        # If the first part matches the clean artist, use the second part
        if parts[0].strip().lower() == artist_clean.lower() or artist_clean.lower() in parts[0].lower():
            title_clean = parts[1]
            
    # Remove artist name prefix/suffix if it's still in the title
    title_clean = re.sub(rf'^\s*{re.escape(artist_clean)}\s*-\s*', '', title_clean, flags=re.IGNORECASE)
    title_clean = re.sub(rf'\s*-\s*{re.escape(artist_clean)}\s*$', '', title_clean, flags=re.IGNORECASE)
    
    # Clean up multiple whitespaces
    title_clean = re.sub(r'\s+', ' ', title_clean).strip()
    
    return title_clean, artist_clean

@app.route("/api/lyrics/<video_id>")
def get_lyrics(video_id):
    title = request.args.get('title', '')
    artist = request.args.get('artist', '')
    
    cache_key = f"lyrics_{video_id}"
    cached = get_cached_api(cache_key, duration=86400)  # 24 hours cache
    if cached is not None:
        return jsonify(cached)
        
    res = None
    
    # Try fetching synced lyrics from Lrclib first
    if title and artist:
        title_clean, artist_clean = sanitize_lyrics_search(title, artist)
        try:
            print(f"[Lyrics API] Fetching synced lyrics from Lrclib (Exact) for '{title_clean}' by '{artist_clean}'")
            lrclib_url = "https://lrclib.net/api/get"
            params = {
                "artist_name": artist_clean,
                "track_name": title_clean
            }
            r = requests.get(lrclib_url, params=params, timeout=5)
            if r.status_code == 200:
                data = r.json()
                synced_lyrics = data.get("syncedLyrics")
                plain_lyrics = data.get("plainLyrics")
                if synced_lyrics:
                    res = {"synced": True, "lyrics": synced_lyrics}
                elif plain_lyrics:
                    res = {"synced": False, "lyrics": plain_lyrics}
        except Exception as e:
            print(f"[Lyrics API] Lrclib exact failed: {e}")

        # Fallback: if exact match didn't yield synced lyrics, try general search endpoint
        if not res or not res.get("synced"):
            try:
                print(f"[Lyrics API] Synced lyrics not found by exact match. Trying Lrclib search query...")
                search_url = "https://lrclib.net/api/search"
                r = requests.get(search_url, params={"q": f"{artist_clean} {title_clean}"}, timeout=5)
                if r.status_code == 200:
                    results = r.json()
                    if results and isinstance(results, list) and len(results) > 0:
                        # Find the first result that has synced lyrics
                        for item in results:
                            synced_lyrics = item.get("syncedLyrics")
                            if synced_lyrics:
                                res = {"synced": True, "lyrics": synced_lyrics}
                                print(f"[Lyrics API] Found synced lyrics via search fallback: {item.get('trackName')}")
                                break
            except Exception as search_err:
                print(f"[Lyrics API] Lrclib search fallback failed: {search_err}")
            
    # Fallback to YTMusic if Lrclib didn't yield anything
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
    cache_key = f"suggestions_{video_id}"
    cached = get_cached_api(cache_key, duration=3600)  # 1 hour cache
    if cached is not None:
        return jsonify(cached)
    try:
        watch = yt.get_watch_playlist(videoId=video_id, limit=10)
        songs = [format_song(t) for t in watch.get('tracks', []) if format_song(t) and t.get('videoId') != video_id]
        if not songs:
            official_songs = get_youtube_suggestions_official(video_id)
            if official_songs:
                songs = official_songs
        set_cached_api(cache_key, songs)
        return jsonify(songs)
    except Exception:
        official_songs = get_youtube_suggestions_official(video_id)
        if official_songs:
            songs = [s for s in official_songs if s['id'] != video_id]
            set_cached_api(cache_key, songs)
            return jsonify(songs)
        return jsonify([])


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

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
