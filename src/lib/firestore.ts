import { 
    collection, 
    doc, 
    setDoc, 
    deleteDoc, 
    getDocs, 
    getDoc, 
    updateDoc, 
    arrayUnion, 
    arrayRemove, 
    query, 
    orderBy, 
    serverTimestamp 
} from 'firebase/firestore';
import { db } from './firebase';

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

/**
 * Save a liked song to user's Firestore collection `users/{userId}/likes/{songId}`
 */
export async function saveLikedSong(userId: string, song: SongItem) {
    if (!userId || !song || !song.id) return false;
    try {
        const songRef = doc(db, 'users', userId, 'likes', song.id);
        await setDoc(songRef, {
            ...song,
            addedAt: Date.now()
        }, { merge: true });
        return true;
    } catch (error) {
        console.error('Error saving liked song to Firestore:', error);
        return false;
    }
}

/**
 * Remove a liked song from user's Firestore collection
 */
export async function removeLikedSong(userId: string, songId: string) {
    if (!userId || !songId) return false;
    try {
        const songRef = doc(db, 'users', userId, 'likes', songId);
        await deleteDoc(songRef);
        return true;
    } catch (error) {
        console.error('Error removing liked song from Firestore:', error);
        return false;
    }
}

/**
 * Fetch all liked songs for a user from Firestore
 */
export async function getLikedSongs(userId: string): Promise<SongItem[]> {
    if (!userId) return [];
    try {
        const likesRef = collection(db, 'users', userId, 'likes');
        const snapshot = await getDocs(likesRef);
        const songs: SongItem[] = [];
        snapshot.forEach(docSnap => {
            songs.push(docSnap.data() as SongItem);
        });
        return songs.sort((a, b) => (b.addedAt || 0) - (a.addedAt || 0));
    } catch (error) {
        console.error('Error fetching liked songs from Firestore:', error);
        return [];
    }
}

/**
 * Create a new playlist in user's Firestore collection `users/{userId}/playlists/{playlistId}`
 */
export async function createFirestorePlaylist(userId: string, name: string, description: string = ''): Promise<Playlist | null> {
    if (!userId || !name.trim()) return null;
    try {
        const playlistId = 'pl_' + Date.now().toString(36) + Math.random().toString(36).substring(2, 5);
        const playlistRef = doc(db, 'users', userId, 'playlists', playlistId);
        const newPlaylist: Playlist = {
            id: playlistId,
            name: name.trim(),
            description: description.trim(),
            songs: [],
            createdAt: Date.now()
        };
        await setDoc(playlistRef, newPlaylist);
        return newPlaylist;
    } catch (error) {
        console.error('Error creating playlist in Firestore:', error);
        return null;
    }
}

/**
 * Fetch user playlists from Firestore
 */
export async function getUserPlaylists(userId: string): Promise<Playlist[]> {
    if (!userId) return [];
    try {
        const playlistsRef = collection(db, 'users', userId, 'playlists');
        const snapshot = await getDocs(playlistsRef);
        const playlists: Playlist[] = [];
        snapshot.forEach(docSnap => {
            playlists.push(docSnap.data() as Playlist);
        });
        return playlists.sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0));
    } catch (error) {
        console.error('Error fetching playlists from Firestore:', error);
        return [];
    }
}

/**
 * Add a song to a user playlist in Firestore
 */
export async function addSongToPlaylist(userId: string, playlistId: string, song: SongItem) {
    if (!userId || !playlistId || !song) return false;
    try {
        const playlistRef = doc(db, 'users', userId, 'playlists', playlistId);
        await updateDoc(playlistRef, {
            songs: arrayUnion(song)
        });
        return true;
    } catch (error) {
        console.error('Error adding song to playlist in Firestore:', error);
        return false;
    }
}

/**
 * Remove a song from a user playlist in Firestore
 */
export async function removeSongFromPlaylist(userId: string, playlistId: string, song: SongItem) {
    if (!userId || !playlistId || !song) return false;
    try {
        const playlistRef = doc(db, 'users', userId, 'playlists', playlistId);
        await updateDoc(playlistRef, {
            songs: arrayRemove(song)
        });
        return true;
    } catch (error) {
        console.error('Error removing song from playlist in Firestore:', error);
        return false;
    }
}

/**
 * Delete a playlist from Firestore
 */
export async function deletePlaylist(userId: string, playlistId: string) {
    if (!userId || !playlistId) return false;
    try {
        const playlistRef = doc(db, 'users', userId, 'playlists', playlistId);
        await deleteDoc(playlistRef);
        return true;
    } catch (error) {
        console.error('Error deleting playlist from Firestore:', error);
        return false;
    }
}

export interface FollowedArtist {
    id: string;
    name: string;
    image?: string;
    followedAt?: number;
}

/**
 * Follow an artist in user's Firestore collection `users/{userId}/following/{artistId}`
 */
export async function followArtist(userId: string, artist: FollowedArtist) {
    if (!userId || !artist || !artist.id) return false;
    try {
        const artistRef = doc(db, 'users', userId, 'following', artist.id);
        await setDoc(artistRef, {
            ...artist,
            followedAt: Date.now()
        }, { merge: true });
        return true;
    } catch (error) {
        console.error('Error following artist in Firestore:', error);
        return false;
    }
}

/**
 * Unfollow an artist from user's Firestore collection
 */
export async function unfollowArtist(userId: string, artistId: string) {
    if (!userId || !artistId) return false;
    try {
        const artistRef = doc(db, 'users', userId, 'following', artistId);
        await deleteDoc(artistRef);
        return true;
    } catch (error) {
        console.error('Error unfollowing artist in Firestore:', error);
        return false;
    }
}

/**
 * Fetch all followed artists for a user from Firestore
 */
export async function getFollowedArtists(userId: string): Promise<FollowedArtist[]> {
    if (!userId) return [];
    try {
        const followingRef = collection(db, 'users', userId, 'following');
        const snapshot = await getDocs(followingRef);
        const artists: FollowedArtist[] = [];
        snapshot.forEach(docSnap => {
            artists.push(docSnap.data() as FollowedArtist);
        });
        return artists.sort((a, b) => (b.followedAt || 0) - (a.followedAt || 0));
    } catch (error) {
        console.error('Error fetching followed artists from Firestore:', error);
        return [];
    }
}

/**
 * Check if a user is following an artist in Firestore
 */
export async function isFollowingArtist(userId: string, artistId: string): Promise<boolean> {
    if (!userId || !artistId) return false;
    try {
        const artistRef = doc(db, 'users', userId, 'following', artistId);
        const docSnap = await getDoc(artistRef);
        return docSnap.exists();
    } catch (error) {
        console.error('Error checking follow status in Firestore:', error);
        return false;
    }
}

/**
 * User Security and Personal Profile Interface
 */
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

/**
 * Synchronize User Profile and Security Data in Firestore
 */
export async function syncUserProfile(user: any): Promise<UserSecurityProfile> {
    const profile: UserSecurityProfile = {
        uid: user.uid,
        email: user.email,
        displayName: user.displayName || user.email?.split('@')[0] || 'Music Fan',
        photoURL: user.photoURL || null,
        emailVerified: Boolean(user.emailVerified),
        authProvider: user.providerData?.[0]?.providerId || 'firebase',
        lastLoginAt: Date.now(),
        securityLevel: user.emailVerified ? 'enhanced' : 'standard',
        verificationSentAt: Date.now()
    };

    // Save to localStorage as immediate offline cache
    try {
        localStorage.setItem(`user_profile_${user.uid}`, JSON.stringify(profile));
        localStorage.setItem('active_user_uid', user.uid);
    } catch (e) {}

    // Persist to Firestore
    try {
        const userRef = doc(db, 'users', user.uid);
        await setDoc(userRef, profile, { merge: true });
    } catch (err) {
        console.warn('Firestore profile sync warning (using local secure cache):', err);
    }

    return profile;
}

/**
 * Get User Security Profile
 */
export async function getUserProfile(userId: string): Promise<UserSecurityProfile | null> {
    if (!userId) return null;
    try {
        const userRef = doc(db, 'users', userId);
        const snap = await getDoc(userRef);
        if (snap.exists()) {
            return snap.data() as UserSecurityProfile;
        }
    } catch (err) {
        console.warn('Error reading user profile from Firestore:', err);
    }

    // Fallback to local storage
    try {
        const local = localStorage.getItem(`user_profile_${userId}`);
        if (local) return JSON.parse(local);
    } catch (e) {}

    return null;
}
