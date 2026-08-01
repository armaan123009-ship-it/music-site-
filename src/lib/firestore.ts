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
