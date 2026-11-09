/**
 * Supabase Auth Bridge (Replaces Firebase completely)
 */
import { supabase, getCurrentUser, onAuthStateChange } from './supabase';

export const auth = {
    get currentUser() {
        return null;
    },
    onAuthStateChanged(callback: (user: any) => void) {
        return onAuthStateChange((user) => callback(user));
    }
};

export const googleProvider = {};
export const hasValidFirebaseApiKey = false;
export const db = {};

export default supabase;
