const getApiBase = () => {
    // Priority 1: build-time Vite environment variables
    if (import.meta.env.PUBLIC_API_URL) {
        return import.meta.env.PUBLIC_API_URL;
    }
    if (import.meta.env.PUBLIC_SITE_URL) {
        return import.meta.env.PUBLIC_SITE_URL;
    }

    // Priority 2: Safe process.env fallback for node/SSR contexts
    try {
        if (typeof process !== 'undefined' && process.env) {
            if (process.env.PUBLIC_API_URL) return process.env.PUBLIC_API_URL;
            if (process.env.PUBLIC_SITE_URL) return process.env.PUBLIC_SITE_URL;
        }
    } catch (e) { }

    if (typeof window !== 'undefined') {
        const isLocalhost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';

        // Detect if running under Capacitor (mobile app WebView)
        const isCapacitor = (window as any).Capacitor || window.location.protocol === 'capacitor:';

        if (isCapacitor && isLocalhost) {
            // For emulator/ADB reverse proxy on dev, point to local Flask backend
            return 'http://127.0.0.1:5000';
        }

        // Frontend dev server ports commonly used in local dev
        const devFrontPorts = new Set(['4321', '4320', '3000', '5173']);
        if (isLocalhost && devFrontPorts.has(window.location.port)) {
            return 'http://127.0.0.1:5000';
        }

        // Production / Mobile Web: use current origin (same host) to ensure mobile requests work without 127.0.0.1 connection failures
        return window.location.origin;
    }

    return 'http://127.0.0.1:5000'; // Fallback for SSR
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

export async function fetchSuggestions(videoId: string) {
    return await apiFetch(`/api/suggestions/${videoId}`) || [];
}

export async function fetchLyrics(videoId: string) {
    const data = await apiFetch(`/api/lyrics/${videoId}`);
    return data ? data.lyrics : null;
}

export async function toggleLike(song: any, action: 'add' | 'remove') {
    return await apiFetch('/api/like', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ song, action })
    });
}

export async function getUserData() {
    return await apiFetch('/api/user_data') || { logged_in: false };
}

export async function createPlaylist(name: string) {
    return await apiFetch('/api/playlists/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
    });
}

export async function addToPlaylist(playlistId: string, song: any) {
    return await apiFetch('/api/playlists/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ playlist_id: playlistId, song })
    });
}
