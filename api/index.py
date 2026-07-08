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

def generate_signature(video_id, url, expires_at):
    message = f"{video_id}:{url}:{expires_at}"
    signature = hmac.new(
        app.secret_key.encode('utf-8'),
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
    try:
        sections = []
        try:
            home_data = yt.get_home(limit=5)
            for section in home_data:
                items = [format_song(i) for i in section.get('contents', []) if format_song(i)][:12]
                if items:
                    sections.append({"title": section.get('title', 'Recommended'), "items": items})
        except: pass
        
        if not sections:
            playlist_id = 'PL4fGSI1pDJn6t3TXLGiiJdD-sZbrG3tG0'
            try:
                charts = yt.get_charts()
                videos = charts.get('videos', [])
                if videos:
                    playlist_id = videos[0].get('playlistId') or playlist_id
            except: pass
            
            playlist = yt.get_playlist(playlist_id)
            items = [format_song(i) for i in playlist.get('tracks', []) if format_song(i)][:12]
            sections.append({"title": "Trending Now", "items": items})
            
        set_cached_api("home", sections)
        return jsonify(sections)
    except Exception:
        return jsonify([])

@app.route("/api/trending")
def trending():
    cached = get_cached_api("trending", duration=1800)  # 30 mins cache
    if cached is not None:
        return jsonify(cached)
    try:
        playlist_id = 'PL4fGSI1pDJn6t3TXLGiiJdD-sZbrG3tG0'
        try:
            charts = yt.get_charts()
            videos = charts.get('videos', [])
            if videos:
                playlist_id = videos[0].get('playlistId') or playlist_id
        except: pass
        
        playlist = yt.get_playlist(playlist_id)
        songs = [format_song(i) for i in playlist.get('tracks', []) if format_song(i)]
        set_cached_api("trending", songs)
        return jsonify(songs)
    except Exception:
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

def fetch_cobalt_stream_url(video_id):
    instances = [
        "https://api.cobalt.tools",
        "https://cobalt.foxtrot-omega.me",
        "https://api.cobalt.best",
        "https://cobalt.unblocker.cc"
    ]
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    data = {
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "downloadMode": "audio",
        "audioFormat": "mp3"
    }

    for instance in instances:
        endpoints = [f"{instance}/api/json", instance, f"{instance}/"]
        for endpoint in endpoints:
            try:
                print(f"[Cobalt Resolver] Attempting: {endpoint} for video_id={video_id}")
                r = requests.post(endpoint, headers=headers, json=data, timeout=8)
                if r.status_code == 200:
                    res_data = r.json()
                    stream_url = res_data.get("url") or res_data.get("picker")
                    if stream_url:
                        print(f"[Cobalt Resolver] Success on: {endpoint}")
                        return stream_url
            except Exception as e:
                print(f"Cobalt endpoint {endpoint} failed: {e}")
                
    return None

def fetch_piped_stream_url(video_id):
    instances = [
        "https://pipedapi.kavin.rocks",
        "https://api.piped.yt",
        "https://piped-api.lunar.icu",
        "https://pipedapi.colt.rocks"
    ]
    
    for instance in instances:
        url = f"{instance}/streams/{video_id}"
        try:
            print(f"[Piped Resolver] Querying instance: {url}")
            r = requests.get(url, timeout=6)
            if r.status_code == 200:
                data = r.json()
                audio_streams = data.get("audioStreams", [])
                if audio_streams:
                    stream_url = audio_streams[0].get("url")
                    if stream_url:
                        print(f"[Piped Resolver] Success on: {instance}")
                        return stream_url
        except Exception as e:
            print(f"Piped endpoint {url} failed: {e}")
            
    return None

def fetch_invidious_stream_url(video_id):
    instances = [
        "https://invidious.io.lol",
        "https://inv.tux.im",
        "https://invidious.projectsegfaut.im",
        "https://yewtu.be"
    ]
    
    for instance in instances:
        url = f"{instance}/api/v1/videos/{video_id}?local=true"
        try:
            print(f"[Invidious Resolver] Querying: {url}")
            r = requests.get(url, timeout=6)
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
                        return stream_url
        except Exception as e:
            print(f"Invidious endpoint {url} failed: {e}")
            
    return None

def resolve_stream_url(video_id):
    url = get_cached_stream_url(video_id)
    if url:
        return url
        
    url = extract_stream_url(video_id)
    if not url:
        print(f"[Resolver] yt-dlp failed for {video_id}, trying Cobalt fallback...")
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
    if not url: return "No URL", 400
    
    video_id = request.args.get('id', '')
    exp = request.args.get('exp', '')
    sig = request.args.get('sig', '')
    
    if not exp or not sig:
        return "Forbidden: Missing signature", 403
        
    try:
        if time.time() > int(exp):
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
        
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    if request.headers.get("Range"): 
        headers["Range"] = request.headers.get("Range")
    try:
        r = requests.get(url, headers=headers, stream=True, timeout=15)
        
        # Transparently resolve fresh URL if cached or resolved URL is forbidden/expired
        if r.status_code not in [200, 206] and video_id:
            print(f"[Proxy] Upstream returned status code {r.status_code}. Resolving fresh URL for {video_id}...")
            if video_id in stream_cache:
                del stream_cache[video_id]
            fresh_url = resolve_stream_url(video_id)
            if fresh_url:
                print(f"[Proxy] Fresh URL resolved. Retrying request...")
                r = requests.get(fresh_url, headers=headers, stream=True, timeout=15)
                
        def generate():
            for chunk in r.iter_content(chunk_size=512*1024):  # Increased buffer to 512KB for faster loading
                if chunk: yield chunk
        response = Response(generate(), status=r.status_code)
        for k, v in r.headers.items():
            if k.lower() not in ['content-encoding', 'transfer-encoding', 'connection', 'access-control-allow-origin', 'content-disposition']:
                response.headers[k] = v
                
        if download:
            response.headers["Content-Type"] = "audio/mpeg"
            response.headers["Content-Disposition"] = f'attachment; filename="{safe_title}.mp3"'
        else:
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Headers"] = "Range, Content-Type"
            response.headers["Access-Control-Expose-Headers"] = "Content-Range, Content-Length, Accept-Ranges"
            
        return response
    except Exception as e:
        # Fallback fresh resolution on request exceptions
        if video_id:
            try:
                print(f"[Proxy] Exception caught during stream request: {e}. Resolving fresh URL...")
                if video_id in stream_cache:
                    del stream_cache[video_id]
                fresh_url = resolve_stream_url(video_id)
                if fresh_url:
                    r = requests.get(fresh_url, headers=headers, stream=True, timeout=15)
                    def generate():
                        for chunk in r.iter_content(chunk_size=512*1024):
                            if chunk: yield chunk
                    response = Response(generate(), status=r.status_code)
                    for k, v in r.headers.items():
                        if k.lower() not in ['content-encoding', 'transfer-encoding', 'connection', 'access-control-allow-origin', 'content-disposition']:
                            response.headers[k] = v
                    response.headers["Access-Control-Allow-Origin"] = "*"
                    response.headers["Access-Control-Allow-Headers"] = "Range, Content-Type"
                    response.headers["Access-Expose-Headers"] = "Content-Range, Content-Length, Accept-Ranges"
                    return response
            except Exception as retry_err:
                return f"Verification retry failed: {str(retry_err)}", 500
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
    try:
        url = resolve_stream_url(video_id)
        if not url:
            raise Exception("Failed to resolve stream URL from all sources")
            
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
        try:
            print(f"[Lyrics API] Fetching synced lyrics from Lrclib for '{title}' by '{artist}'")
            lrclib_url = "https://lrclib.net/api/get"
            params = {
                "artist_name": artist,
                "track_name": title
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
            print(f"[Lyrics API] Lrclib failed: {e}")
            
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
