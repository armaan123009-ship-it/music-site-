const getApiBase = () => {
    // Priority: environment variable set at build-time
    if (import.meta.env.PUBLIC_API_URL) {
        return import.meta.env.PUBLIC_API_URL;
    }
    
    if (typeof window !== 'undefined') {
        // If we are on the dev server (4321), point to the local backend (5000)
        if (window.location.port === '4321') {
            return 'http://127.0.0.1:5000';
        }
        // Otherwise use the current origin
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

export async function fetchHome() {
    return await apiFetch('/api/home') || [];
}

export async function fetchTrending() {
    return await apiFetch('/api/trending') || [];
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
