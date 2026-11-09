import { createClient, type User, type Session } from '@supabase/supabase-js';

export const SUPABASE_PROJECT_REF = 'dgrnzltscdqelldffjhu';
export const SUPABASE_URL = import.meta.env.PUBLIC_SUPABASE_URL || `https://${SUPABASE_PROJECT_REF}.supabase.co`;

// Supabase anon public key (can be overridden via environment variables)
export const SUPABASE_ANON_KEY = import.meta.env.PUBLIC_SUPABASE_ANON_KEY || 
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRncm56bHRzY2RxZWxsZGZmamh1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NDAwMDAwMDAsImV4cCI6MjA1NTU3NjAwMH0.supabase-anon-key';

// Create browser-safe Supabase client
export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
        storageKey: 'supabase_auth_token'
    }
});

export interface AppUser {
    uid: string;
    email: string | null;
    displayName: string | null;
    photoURL?: string | null;
    emailVerified: boolean;
    provider?: string;
    lastLoginAt?: number;
}

/**
 * Normalizes a Supabase User into the standard AppUser format
 */
export function normalizeUser(user: User | null): AppUser | null {
    if (!user) return null;
    const meta = user.user_metadata || {};
    return {
        uid: user.id,
        email: user.email || null,
        displayName: meta.display_name || meta.full_name || meta.name || user.email?.split('@')[0] || 'Music Fan',
        photoURL: meta.avatar_url || meta.picture || null,
        emailVerified: Boolean(user.email_confirmed_at || user.confirmed_at),
        provider: user.app_metadata?.provider || 'supabase',
        lastLoginAt: Date.now()
    };
}

/**
 * Sign up with Email and Password using Supabase
 */
export async function signUpWithEmail(email: string, password: string, displayName?: string) {
    const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: {
            data: {
                display_name: displayName || email.split('@')[0],
                full_name: displayName || email.split('@')[0]
            }
        }
    });

    if (error) throw error;
    return normalizeUser(data.user);
}

/**
 * Sign in with Email and Password using Supabase
 */
export async function signInWithEmail(email: string, password: string) {
    const { data, error } = await supabase.auth.signInWithPassword({
        email,
        password
    });

    if (error) throw error;
    return normalizeUser(data.user);
}

/**
 * Sign in with Google OAuth using Supabase
 */
export async function signInWithGoogle(redirectTo?: string) {
    const targetUrl = redirectTo || (typeof window !== 'undefined' ? `${window.location.origin}/profile` : '/profile');
    const { data, error } = await supabase.auth.signInWithOAuth({
        provider: 'google',
        options: {
            redirectTo: targetUrl,
            queryParams: {
                access_type: 'offline',
                prompt: 'select_account'
            }
        }
    });

    if (error) throw error;
    return data;
}

/**
 * Sign out
 */
export async function signOut() {
    try {
        await supabase.auth.signOut();
    } catch (e) {
        console.warn('Supabase sign out notice:', e);
    }
}

/**
 * Get current session and user
 */
export async function getCurrentUser(): Promise<AppUser | null> {
    try {
        const { data: { session } } = await supabase.auth.getSession();
        if (session?.user) {
            return normalizeUser(session.user);
        }
    } catch {}
    return null;
}

/**
 * Subscribe to Supabase Auth state changes
 */
export function onAuthStateChange(callback: (user: AppUser | null, session: Session | null) => void) {
    return supabase.auth.onAuthStateChange((event, session) => {
        const appUser = session?.user ? normalizeUser(session.user) : null;
        callback(appUser, session);
    });
}

