export const PRODUCTION_RENDER_BACKEND = "https://music-backend-api-9cg6.onrender.com";

const getApiBase = () => {
    // Priority 1: Render backend URL or custom API URL from env
    if (import.meta.env.PUBLIC_RENDER_API_URL) {
        return import.meta.env.PUBLIC_RENDER_API_URL.replace(/\/$/, '');
    }
    if (import.meta.env.PUBLIC_API_URL) {
        return import.meta.env.PUBLIC_API_URL.replace(/\/$/, '');
    }
    if (import.meta.env.PUBLIC_SITE_URL) {
        return import.meta.env.PUBLIC_SITE_URL.replace(/\/$/, '');
    }

    // Priority 2: Safe process.env fallback for node/SSR contexts
    try {
        if (typeof process !== 'undefined' && process.env) {
            if (process.env.PUBLIC_RENDER_API_URL) return process.env.PUBLIC_RENDER_API_URL.replace(/\/$/, '');
            if (process.env.PUBLIC_API_URL) return process.env.PUBLIC_API_URL.replace(/\/$/, '');
            if (process.env.PUBLIC_SITE_URL) return process.env.PUBLIC_SITE_URL.replace(/\/$/, '');
        }
    } catch (e) { }

    if (typeof window !== 'undefined') {
        const isLocalhost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
        const isCapacitor = (window as any).Capacitor || window.location.protocol === 'capacitor:' || window.location.origin.includes('capacitor');

        // Dev environment ports (when testing with local python app.py)
        const devFrontPorts = new Set(['4321', '4320', '3000', '5173']);
        if (isLocalhost && devFrontPorts.has(window.location.port)) {
            return 'http://127.0.0.1:5000';
        }

        // On Capacitor APK or Mobile App: use the dedicated Render backend
        if (isCapacitor) {
            return PRODUCTION_RENDER_BACKEND;
        }

        // Production web on Vercel: use dedicated Render backend for heavy streaming and search
        return PRODUCTION_RENDER_BACKEND;
    }

    return PRODUCTION_RENDER_BACKEND; // Default fallback
};

export const API_BASE = getApiBase();

export async function apiFetch(path: string, options: RequestInit = {}) {
    const url = path.startsWith('http') ? path : `${API_BASE}${path.startsWith('/') ? '' : '/'}${path}`;
    try {
        const res = await fetch(url, options);
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        return await res.json();
    } catch (e) {
        console.error(`API Fetch failed for ${url}:`, e);
        return null;
    }
}

function getCache(key: string, ttlMs: number = 300000) {
    if (typeof window === 'undefined') return null;
    try {
        const itemStr = localStorage.getItem(`cache_${key}`);
        if (!itemStr) return null;
        const item = JSON.parse(itemStr);
        if (Date.now() - item.ts < ttlMs) {
            return item.data;
        }
    } catch (e) { }
    return null;
}

function setCache(key: string, data: any) {
    if (typeof window === 'undefined' || !data) return;
    try {
        localStorage.setItem(`cache_${key}`, JSON.stringify({ ts: Date.now(), data }));
    } catch (e) { }
}

export async function fetchHome() {
    const cached = getCache('home', 600000); // 10 mins cache
    if (cached) {
        // Refresh in background silently for instant UI rendering
        apiFetch('/api/home').then(data => data && setCache('home', data)).catch(() => {});
        return cached;
    }
    const data = await apiFetch('/api/home');
    if (data) setCache('home', data);
    return data || [];
}

export async function fetchTrending() {
    const cached = getCache('trending', 900000); // 15 mins cache
    if (cached) {
        apiFetch('/api/trending').then(data => data && setCache('trending', data)).catch(() => {});
        return cached;
    }
    const data = await apiFetch('/api/trending');
    if (data) setCache('trending', data);
    return data || [];
}

export async function searchSongs(query: string) {
    return await apiFetch(`/api/search?q=${encodeURIComponent(query)}`) || [];
}

export async function getStreamUrl(videoId: string, options: RequestInit = {}) {
    const data = await apiFetch(`/stream/${videoId}`, options);
    return data ? data.url : null;
}

export async function getStreamProxyUrl(videoId: string, options: RequestInit = {}) {
    const data = await apiFetch(`/stream/${videoId}`, options);
    if (data && data.proxy_url) {
        return data.proxy_url.startsWith('http') ? data.proxy_url : `${API_BASE}${data.proxy_url}`;
    }
    return null;
}

export async function fetchSuggestions(videoId: string, title: string = '', artist: string = '') {
    const params = new URLSearchParams();
    if (title) params.set('title', title);
    if (artist) params.set('artist', artist);
    const qs = params.toString() ? `?${params.toString()}` : '';
    return await apiFetch(`/api/suggestions/${videoId}${qs}`) || [];
}

export async function fetchLyrics(videoId: string, title: string = '', artist: string = '', duration: string = '') {
    const params = new URLSearchParams();
    if (title) params.set('title', title);
    if (artist) params.set('artist', artist);
    if (duration) params.set('duration', duration);
    const qs = params.toString() ? `?${params.toString()}` : '';
    return await apiFetch(`/api/lyrics/${videoId}${qs}`);
}

export async function fetchExplore(category: string = '') {
    const qs = category ? `?category=${encodeURIComponent(category)}` : '';
    const cached = getCache(`explore_${category || 'all'}`, 600000);
    if (cached) {
        apiFetch(`/api/explore${qs}`).then(data => data && setCache(`explore_${category || 'all'}`, data)).catch(() => {});
        return cached;
    }
    const data = await apiFetch(`/api/explore${qs}`);
    if (data) setCache(`explore_${category || 'all'}`, data);
    return data;
}

export async function getDownloadDetails(videoId: string, title: string = '') {
    return await apiFetch(`/api/download/${videoId}?mode=json&title=${encodeURIComponent(title)}`);
}

export async function toggleLike(song: any, action: 'add' | 'remove') {
    try {
        const { getCurrentUser } = await import('./supabase');
        const { saveLikedSong, removeLikedSong, getActiveUser } = await import('./supabaseDb');
        const currentUser = await getCurrentUser() || getActiveUser();
        const userId = currentUser ? currentUser.uid : 'guest';
        if (action === 'add') {
            await saveLikedSong(userId, song);
        } else {
            await removeLikedSong(userId, song.id);
        }
    } catch (e) {
        console.error('Supabase toggleLike error:', e);
    }

    return await apiFetch('/api/like', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ song, action })
    });
}

export async function getUserData() {
    try {
        const { getCurrentUser } = await import('./supabase');
        const { getLikedSongs, getUserPlaylists, getActiveUser } = await import('./supabaseDb');
        const currentUser = await getCurrentUser() || getActiveUser();
        const userId = currentUser ? currentUser.uid : 'guest';
        const [liked_songs, playlists] = await Promise.all([
            getLikedSongs(userId),
            getUserPlaylists(userId)
        ]);
        return {
            logged_in: Boolean(currentUser),
            username: currentUser ? (currentUser.displayName || currentUser.email?.split('@')[0] || 'User') : 'Guest Explorer',
            email: currentUser ? currentUser.email : null,
            liked_songs,
            playlists
        };
    } catch (e) {
        console.error('Supabase getUserData error:', e);
    }
    return await apiFetch('/api/user_data') || { logged_in: false, liked_songs: [], playlists: [] };
}

export async function createPlaylist(name: string) {
    try {
        const { getCurrentUser } = await import('./supabase');
        const { createPlaylist: createSupabasePlaylist, getActiveUser } = await import('./supabaseDb');
        const currentUser = await getCurrentUser() || getActiveUser();
        const userId = currentUser ? currentUser.uid : 'guest';
        const playlist = await createSupabasePlaylist(userId, name);
        if (playlist) {
            return { success: true, playlist };
        }
    } catch (e) {
        console.error('Supabase createPlaylist error:', e);
    }
    return await apiFetch('/api/playlists/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
    });
}

export async function addToPlaylist(playlistId: string, song: any) {
    try {
        const { getCurrentUser } = await import('./supabase');
        const { addSongToPlaylist, getActiveUser } = await import('./supabaseDb');
        const currentUser = await getCurrentUser() || getActiveUser();
        const userId = currentUser ? currentUser.uid : 'guest';
        const success = await addSongToPlaylist(userId, playlistId, song);
        if (success) return { success: true };
    } catch (e) {
        console.error('Supabase addToPlaylist error:', e);
    }
    return await apiFetch('/api/playlists/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ playlist_id: playlistId, song })
    });
}

export async function fetchArtistDetails(artistId: string) {
    return await apiFetch(`/api/artist/${artistId}`);
}

export async function toggleFollowArtist(artist: { id: string, name: string, image?: string }, action: 'follow' | 'unfollow') {
    try {
        const { getCurrentUser } = await import('./supabase');
        const { followArtist, unfollowArtist, getActiveUser } = await import('./supabaseDb');
        const currentUser = await getCurrentUser() || getActiveUser();
        if (currentUser) {
            if (action === 'follow') {
                await followArtist(currentUser.uid, artist);
            } else {
                await unfollowArtist(currentUser.uid, artist.id);
            }
            return { success: true };
        }
    } catch (e) {
        console.error('Supabase toggleFollowArtist error:', e);
    }
    return { success: false };
}

