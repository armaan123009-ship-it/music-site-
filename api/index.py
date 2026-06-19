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
        set_cached_api(cache_key, songs)
        return jsonify(songs)
    except Exception: return jsonify([])

@app.route("/stream/<video_id>")
def stream(video_id):
    try:
        cached_url = get_cached_stream_url(video_id)
        if cached_url:
            return jsonify({
                "url": cached_url, 
                "proxy_url": f"/proxy?url={requests.utils.quote(cached_url)}"
            })
            
        ydl_opts = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'ignoreerrors': True,
            'logtostderr': False,
        }
        
        if os.path.exists(cookies_path) and os.path.getsize(cookies_path) > 0:
            try:
                with open(cookies_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    if '# Netscape' in content or 'domain' in content:
                        ydl_opts['cookiefile'] = cookies_path
            except: pass
                
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
            if not info or 'url' not in info:
                info = ydl.extract_info(f"ytsearch:{video_id}", download=False)['entries'][0]
                
            url = info['url']
            set_cached_stream_url(video_id, url)
            return jsonify({
                "url": url, 
                "proxy_url": f"/proxy?url={requests.utils.quote(url)}"
            })
    except Exception as e:
        print(f"Stream error for {video_id}: {e}")
        return jsonify({"error": str(e)}), 400

@app.route("/proxy")
def proxy():
    url = request.args.get('url')
    if not url: return "No URL", 400
    
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
        print("[Download] Retrieving cached stream URL...")
        url = get_cached_stream_url(video_id)
        r = None
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        
        if url:
            try:
                print(f"[Download] Testing cached URL: {url[:60]}...")
                test_r = requests.get(url, headers=headers, stream=True, timeout=10)
                print(f"[Download] Cached URL check returned status: {test_r.status_code}")
                if test_r.status_code in [200, 206]:
                    r = test_r
            except Exception as test_err:
                print(f"[Download] Cached stream check failed for {video_id}: {test_err}")
                
        if r is None:
            print("[Download] Cached URL not found or invalid. Fetching fresh URL using yt_dlp...")
            ydl_opts = {
                'format': 'bestaudio/best',
                'quiet': True,
                'no_warnings': True,
                'nocheckcertificate': True,
                'ignoreerrors': True,
            }
            
            if os.path.exists(cookies_path) and os.path.getsize(cookies_path) > 0:
                try:
                    with open(cookies_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        if '# Netscape' in content or 'domain' in content:
                            ydl_opts['cookiefile'] = cookies_path
                except: pass
                
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                print("[Download] Extracting info for watch URL...")
                info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
                if not info or 'url' not in info:
                    print("[Download] Watch URL failed. Using search fallback...")
                    info = ydl.extract_info(f"ytsearch:{video_id}", download=False)['entries'][0]
                url = info['url']
                set_cached_stream_url(video_id, url)
            
            print(f"[Download] Requesting fresh URL: {url[:60]}...")
            r = requests.get(url, headers=headers, stream=True, timeout=20)
            print(f"[Download] Fresh URL request returned status: {r.status_code}")
            
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
    cache_key = f"lyrics_{video_id}"
    cached = get_cached_api(cache_key, duration=86400)  # 24 hours cache
    if cached is not None:
        return jsonify(cached)
    try:
        watch = yt.get_watch_playlist(videoId=video_id)
        lyrics_id = watch.get('lyrics')
        if not lyrics_id:
            res = {"lyrics": "No lyrics found."}
        else:
            res = {"lyrics": yt.get_lyrics(lyrics_id).get('lyrics', 'No lyrics found.')}
        set_cached_api(cache_key, res)
        return jsonify(res)
    except:
        return jsonify({"lyrics": "No lyrics found."})

@app.route("/api/suggestions/<video_id>")
def get_suggestions(video_id):
    cache_key = f"suggestions_{video_id}"
    cached = get_cached_api(cache_key, duration=3600)  # 1 hour cache
    if cached is not None:
        return jsonify(cached)
    try:
        watch = yt.get_watch_playlist(videoId=video_id, limit=10)
        songs = [format_song(t) for t in watch.get('tracks', []) if format_song(t) and t.get('videoId') != video_id]
        set_cached_api(cache_key, songs)
        return jsonify(songs)
    except:
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
