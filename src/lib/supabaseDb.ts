import { supabase, type AppUser } from './supabase';

export interface SongItem {
    id: string;
    title: string;
    artist?: string;
    thumbnail?: string;
    duration?: string;
    addedAt?: number;
    [key: string]: any;
}

export interface Playlist {
    id: string;
    name: string;
    description?: string;
    songs: SongItem[];
    createdAt?: number;
}

export interface FollowedArtist {
    id: string;
    name: string;
    image?: string;
    followedAt?: number;
}

export interface UserSecurityProfile {
    uid: string;
    email: string | null;
    displayName: string | null;
    photoURL?: string | null;
    emailVerified: boolean;
    authProvider: string;
    lastLoginAt: number;
    securityLevel: 'enhanced' | 'standard';
    verificationSentAt?: number;
}

// Local storage fallback helpers
function getLocalItem(key: string, fallback: any = null) {
    if (typeof window === 'undefined') return fallback;
    try {
        const val = localStorage.getItem(key);
        return val ? JSON.parse(val) : fallback;
    } catch {
        return fallback;
    }
}

function setLocalItem(key: string, value: any) {
    if (typeof window === 'undefined') return;
    try {
        localStorage.setItem(key, JSON.stringify(value));
    } catch {}
}

/**
 * Save a liked song to Supabase and local storage
 */
export async function saveLikedSong(userId: string, song: SongItem): Promise<boolean> {
    if (!userId || !song || !song.id) return false;

    // 1. Local backup for instant UI responsiveness & offline support
    const localLikes: SongItem[] = getLocalItem(`local_likes_${userId}`, []);
    if (!localLikes.some(s => s.id === song.id)) {
        localLikes.unshift({ ...song, addedAt: Date.now() });
        setLocalItem(`local_likes_${userId}`, localLikes);
    }

    // 2. Persist to Supabase table `liked_songs`
    try {
        const { error } = await supabase
            .from('liked_songs')
            .upsert({
                user_id: userId,
                song_id: song.id,
                title: song.title,
                artist: song.artist || '',
                thumbnail: song.thumbnail || song.image || '',
                duration: song.duration || '',
                metadata: song,
                added_at: new Date().toISOString()
            }, { onConflict: 'user_id,song_id' });

        if (error) {
            console.warn('Supabase liked_songs sync notice (saved locally):', error.message);
        }
        return true;
    } catch (err) {
        console.warn('Supabase request notice (persisted in local library):', err);
        return true;
    }
}

/**
 * Remove a liked song from Supabase and local storage
 */
export async function removeLikedSong(userId: string, songId: string): Promise<boolean> {
    if (!userId || !songId) return false;

    // Remove from local storage
    const localLikes: SongItem[] = getLocalItem(`local_likes_${userId}`, []);
    setLocalItem(`local_likes_${userId}`, localLikes.filter(s => s.id !== songId));

    try {
        const { error } = await supabase
            .from('liked_songs')
            .delete()
            .match({ user_id: userId, song_id: songId });

        if (error) {
            console.warn('Supabase remove liked song notice:', error.message);
        }
        return true;
    } catch (err) {
        console.warn('Supabase delete error:', err);
        return true;
    }
}

/**
 * Fetch all liked songs for a user (Supabase + local merge)
 */
export async function getLikedSongs(userId: string): Promise<SongItem[]> {
    if (!userId) return [];
    const localLikes: SongItem[] = getLocalItem(`local_likes_${userId}`, []);

    try {
        const { data, error } = await supabase
            .from('liked_songs')
            .select('*')
            .eq('user_id', userId)
            .order('added_at', { ascending: false });

        if (!error && Array.isArray(data) && data.length > 0) {
            const mapped: SongItem[] = data.map(item => ({
                id: item.song_id || item.id,
                title: item.title,
                artist: item.artist,
                thumbnail: item.thumbnail,
                duration: item.duration,
                addedAt: item.added_at ? new Date(item.added_at).getTime() : Date.now(),
                ...(item.metadata || {})
            }));

            // Merge with local likes
            const merged = [...mapped];
            localLikes.forEach(l => {
                if (!merged.some(m => m.id === l.id)) merged.push(l);
            });

            const sorted = merged.sort((a, b) => (b.addedAt || 0) - (a.addedAt || 0));
            setLocalItem(`local_likes_${userId}`, sorted);
            return sorted;
        }
    } catch (err) {
        console.warn('Supabase getLikedSongs notice (using local storage):', err);
    }

    return localLikes;
}

/**
 * Create a new playlist in Supabase and local storage
 */
export async function createPlaylist(userId: string, name: string, description: string = ''): Promise<Playlist | null> {
    if (!userId || !name.trim()) return null;
    const playlistId = 'pl_' + Date.now().toString(36) + Math.random().toString(36).substring(2, 6);
    const newPlaylist: Playlist = {
        id: playlistId,
        name: name.trim(),
        description: description.trim(),
        songs: [],
        createdAt: Date.now()
    };

    // Save locally
    const localPls: Playlist[] = getLocalItem(`local_playlists_${userId}`, []);
    localPls.unshift(newPlaylist);
    setLocalItem(`local_playlists_${userId}`, localPls);

    try {
        const { error } = await supabase
            .from('playlists')
            .insert({
                id: playlistId,
                user_id: userId,
                name: newPlaylist.name,
                description: newPlaylist.description,
                songs: newPlaylist.songs,
                created_at: new Date().toISOString()
            });

        if (error) {
            console.warn('Supabase create playlist notice (persisted locally):', error.message);
        }
    } catch (err) {
        console.warn('Supabase playlist insert notice:', err);
    }

    return newPlaylist;
}

export const createFirestorePlaylist = createPlaylist; // Backward compatibility alias

/**
 * Fetch user playlists from Supabase and local backup
 */
export async function getUserPlaylists(userId: string): Promise<Playlist[]> {
    if (!userId) return [];
    const localPls: Playlist[] = getLocalItem(`local_playlists_${userId}`, []);

    try {
        const { data, error } = await supabase
            .from('playlists')
            .select('*')
            .eq('user_id', userId)
            .order('created_at', { ascending: false });

        if (!error && Array.isArray(data) && data.length > 0) {
            const mapped: Playlist[] = data.map(item => ({
                id: item.id,
                name: item.name,
                description: item.description || '',
                songs: Array.isArray(item.songs) ? item.songs : [],
                createdAt: item.created_at ? new Date(item.created_at).getTime() : Date.now()
            }));

            const merged = [...mapped];
            localPls.forEach(lp => {
                if (!merged.some(m => m.id === lp.id)) merged.push(lp);
            });

            const sorted = merged.sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
            setLocalItem(`local_playlists_${userId}`, sorted);
            return sorted;
        }
    } catch (err) {
        console.warn('Supabase getUserPlaylists notice:', err);
    }

    return localPls;
}

/**
 * Add a song to a playlist
 */
export async function addSongToPlaylist(userId: string, playlistId: string, song: SongItem): Promise<boolean> {
    if (!userId || !playlistId || !song) return false;

    // Update local copy
    const localPls: Playlist[] = getLocalItem(`local_playlists_${userId}`, []);
    const targetPl = localPls.find(p => p.id === playlistId);
    if (targetPl && !targetPl.songs.some(s => s.id === song.id)) {
        targetPl.songs.push(song);
        setLocalItem(`local_playlists_${userId}`, localPls);
    }

    try {
        const playlists = await getUserPlaylists(userId);
        const pl = playlists.find(p => p.id === playlistId);
        if (pl) {
            const updatedSongs = [...pl.songs.filter(s => s.id !== song.id), song];
            await supabase
                .from('playlists')
                .update({ songs: updatedSongs })
                .eq('id', playlistId);
            return true;
        }
    } catch (e) {
        console.warn('Supabase addSongToPlaylist error:', e);
    }
    return true;
}

/**
 * Remove a song from a playlist
 */
export async function removeSongFromPlaylist(userId: string, playlistId: string, song: SongItem): Promise<boolean> {
    if (!userId || !playlistId || !song) return false;

    const localPls: Playlist[] = getLocalItem(`local_playlists_${userId}`, []);
    const targetPl = localPls.find(p => p.id === playlistId);
    if (targetPl) {
        targetPl.songs = targetPl.songs.filter(s => s.id !== song.id);
        setLocalItem(`local_playlists_${userId}`, localPls);
    }

    try {
        const playlists = await getUserPlaylists(userId);
        const pl = playlists.find(p => p.id === playlistId);
        if (pl) {
            const updatedSongs = pl.songs.filter(s => s.id !== song.id);
            await supabase
                .from('playlists')
                .update({ songs: updatedSongs })
                .eq('id', playlistId);
            return true;
        }
    } catch (e) {
        console.warn('Supabase removeSongFromPlaylist error:', e);
    }
    return true;
}

/**
 * Delete a playlist
 */
export async function deletePlaylist(userId: string, playlistId: string): Promise<boolean> {
    if (!userId || !playlistId) return false;

    const localPls: Playlist[] = getLocalItem(`local_playlists_${userId}`, []);
    setLocalItem(`local_playlists_${userId}`, localPls.filter(p => p.id !== playlistId));

    try {
        await supabase
            .from('playlists')
            .delete()
            .eq('id', playlistId);
    } catch (e) {
        console.warn('Supabase deletePlaylist error:', e);
    }
    return true;
}

/**
 * Follow an artist
 */
export async function followArtist(userId: string, artist: FollowedArtist): Promise<boolean> {
    if (!userId || !artist || !artist.id) return false;

    const localFollowing: FollowedArtist[] = getLocalItem(`local_following_${userId}`, []);
    if (!localFollowing.some(a => a.id === artist.id)) {
        localFollowing.unshift({ ...artist, followedAt: Date.now() });
        setLocalItem(`local_following_${userId}`, localFollowing);
    }

    try {
        await supabase
            .from('followed_artists')
            .upsert({
                user_id: userId,
                artist_id: artist.id,
                name: artist.name,
                image: artist.image || '',
                followed_at: new Date().toISOString()
            }, { onConflict: 'user_id,artist_id' });
    } catch (e) {
        console.warn('Supabase followArtist error:', e);
    }
    return true;
}

/**
 * Unfollow an artist
 */
export async function unfollowArtist(userId: string, artistId: string): Promise<boolean> {
    if (!userId || !artistId) return false;

    const localFollowing: FollowedArtist[] = getLocalItem(`local_following_${userId}`, []);
    setLocalItem(`local_following_${userId}`, localFollowing.filter(a => a.id !== artistId));

    try {
        await supabase
            .from('followed_artists')
            .delete()
            .match({ user_id: userId, artist_id: artistId });
    } catch (e) {
        console.warn('Supabase unfollowArtist error:', e);
    }
    return true;
}

/**
 * Fetch all followed artists
 */
export async function getFollowedArtists(userId: string): Promise<FollowedArtist[]> {
    if (!userId) return [];
    const localFollowing: FollowedArtist[] = getLocalItem(`local_following_${userId}`, []);

    try {
        const { data, error } = await supabase
            .from('followed_artists')
            .select('*')
            .eq('user_id', userId)
            .order('followed_at', { ascending: false });

        if (!error && Array.isArray(data) && data.length > 0) {
            const mapped: FollowedArtist[] = data.map(item => ({
                id: item.artist_id || item.id,
                name: item.name,
                image: item.image,
                followedAt: item.followed_at ? new Date(item.followed_at).getTime() : Date.now()
            }));
            return mapped;
        }
    } catch (e) {
        console.warn('Supabase getFollowedArtists notice:', e);
    }

    return localFollowing;
}

/**
 * Check if following an artist
 */
export async function isFollowingArtist(userId: string, artistId: string): Promise<boolean> {
    if (!userId || !artistId) return false;
    const localFollowing: FollowedArtist[] = getLocalItem(`local_following_${userId}`, []);
    if (localFollowing.some(a => a.id === artistId)) return true;

    try {
        const { data } = await supabase
            .from('followed_artists')
            .select('artist_id')
            .match({ user_id: userId, artist_id: artistId })
            .limit(1);

        return Boolean(data && data.length > 0);
    } catch {
        return false;
    }
}

/**
 * Synchronize User Profile and Security Data in Supabase and local storage
 */
export async function syncUserProfile(user: any): Promise<UserSecurityProfile> {
    const uid = user.uid || user.id;
    const profile: UserSecurityProfile = {
        uid: uid,
        email: user.email || null,
        displayName: user.displayName || user.user_metadata?.display_name || user.email?.split('@')[0] || 'Music Fan',
        photoURL: user.photoURL || user.user_metadata?.avatar_url || null,
        emailVerified: Boolean(user.emailVerified || user.email_confirmed_at),
        authProvider: user.provider || user.app_metadata?.provider || 'supabase',
        lastLoginAt: Date.now(),
        securityLevel: (user.emailVerified || user.email_confirmed_at) ? 'enhanced' : 'standard',
        verificationSentAt: Date.now()
    };

    // Save to localStorage
    try {
        localStorage.setItem(`user_profile_${uid}`, JSON.stringify(profile));
        localStorage.setItem('active_user_uid', uid);
    } catch (e) {}

    // Persist to Supabase table `user_profiles`
    try {
        await supabase
            .from('user_profiles')
            .upsert({
                id: uid,
                email: profile.email,
                display_name: profile.displayName,
                photo_url: profile.photoURL,
                email_verified: profile.emailVerified,
                provider: profile.authProvider,
                updated_at: new Date().toISOString()
            }, { onConflict: 'id' });
    } catch (err) {
        console.warn('Supabase profile sync warning (using local secure cache):', err);
    }

    return profile;
}

/**
 * Get active user from current session or localStorage cache
 */
export function getActiveUser(): UserSecurityProfile | null {
    if (typeof window === 'undefined') return null;
    try {
        const uid = localStorage.getItem('active_user_uid');
        if (!uid) return null;
        const profileStr = localStorage.getItem(`user_profile_${uid}`);
        return profileStr ? JSON.parse(profileStr) : null;
    } catch {
        return null;
    }
}

/**
 * Clear active user session
 */
export function clearActiveUser() {
    if (typeof window === 'undefined') return;
    try {
        localStorage.removeItem('active_user_uid');
        localStorage.removeItem('supabase_auth_token');
    } catch {}
}

