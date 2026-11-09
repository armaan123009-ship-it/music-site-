import { signInWithPopup } from 'firebase/auth';
import { auth, googleProvider, GOOGLE_CLIENT_ID, hasValidFirebaseApiKey } from './firebase';
import { syncUserProfile } from './firestore';

let tokenClientInstance: any = null;
let googleAuthInitialized = false;

/**
 * Safely parse a JWT payload (e.g. from Google ID token credential)
 */
export function parseJwt(token: string): any {
    try {
        const base64Url = token.split('.')[1];
        if (!base64Url) return null;
        const base64 = base64Url.replace(/-/g, '+').replace(/_/g, '/');
        const jsonPayload = decodeURIComponent(
            atob(base64)
                .split('')
                .map(c => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
                .join('')
        );
        return JSON.parse(jsonPayload);
    } catch (e) {
        console.warn('JWT parse notice:', e);
        return null;
    }
}

/**
 * Ensures Google Identity Services client script is loaded
 */
export function ensureGoogleScriptLoaded(): Promise<any> {
    return new Promise((resolve, reject) => {
        if (typeof window === 'undefined') return reject(new Error('Window unavailable'));
        const google = (window as any).google;
        if (google?.accounts?.oauth2 || google?.accounts?.id) {
            return resolve(google);
        }

        let script = document.querySelector('script[src="https://accounts.google.com/gsi/client"]') as HTMLScriptElement;
        if (!script) {
            script = document.createElement('script');
            script.src = 'https://accounts.google.com/gsi/client';
            script.async = true;
            script.defer = true;
            document.head.appendChild(script);
        }

        script.addEventListener('load', () => {
            const g = (window as any).google;
            resolve(g);
        }, { once: true });

        script.addEventListener('error', () => {
            reject(new Error('Could not load Google authentication service'));
        }, { once: true });
    });
}

/**
 * Pre-initialize Google Identity Services so that tokenClient.requestAccessToken()
 * can be triggered synchronously upon user interaction (avoiding popup blockers).
 */
export async function initGoogleAuth(onSuccess?: (user: any) => void): Promise<void> {
    try {
        const google = await ensureGoogleScriptLoaded();
        if (!google) return;

        // 1. Initialize Google ID Token / One-Tap & standard button handler
        if (google.accounts?.id) {
            google.accounts.id.initialize({
                client_id: GOOGLE_CLIENT_ID,
                callback: async (response: any) => {
                    if (response.credential) {
                        const payload = parseJwt(response.credential);
                        if (payload) {
                            const profile = {
                                uid: payload.sub,
                                email: payload.email,
                                displayName: payload.name || payload.email?.split('@')[0],
                                photoURL: payload.picture,
                                emailVerified: Boolean(payload.email_verified),
                                providerData: [{ providerId: 'google.com' }]
                            };
                            await syncUserProfile(profile);
                            if (onSuccess) onSuccess(profile);
                        }
                    }
                },
                auto_select: false,
                cancel_on_tap_outside: true
            });
        }

        // 2. Initialize OAuth 2.0 Token Client for custom button popups
        if (google.accounts?.oauth2 && !tokenClientInstance) {
            tokenClientInstance = google.accounts.oauth2.initTokenClient({
                client_id: GOOGLE_CLIENT_ID,
                scope: 'email profile openid',
                callback: () => {} // Overridden dynamically per sign-in request
            });
        }

        googleAuthInitialized = true;
    } catch (err) {
        console.warn('Google Identity initialization notice:', err);
    }
}

/**
 * Render the official Google Sign-in button into a container element if present
 */
export async function renderGoogleButton(containerId: string, onSuccess: (user: any) => void): Promise<boolean> {
    try {
        const google = await ensureGoogleScriptLoaded();
        const container = document.getElementById(containerId);
        if (!google?.accounts?.id || !container) return false;

        await initGoogleAuth(onSuccess);
        google.accounts.id.renderButton(container, {
            theme: 'filled_black',
            size: 'large',
            shape: 'pill',
            width: 320,
            text: 'continue_with'
        });
        return true;
    } catch {
        return false;
    }
}

/**
 * Direct Google OAuth 2.0 Token prompt via GIS Token Client
 */
export function signInWithGoogleDirect(): Promise<any> {
    return new Promise(async (resolve, reject) => {
        try {
            await ensureGoogleScriptLoaded();
            const google = (window as any).google;
            if (!google?.accounts?.oauth2) {
                return reject(new Error('Google Identity Services is not loaded. Please refresh or check connection.'));
            }

            const currentOrigin = window.location.origin;

            const client = google.accounts.oauth2.initTokenClient({
                client_id: GOOGLE_CLIENT_ID,
                scope: 'email profile openid',
                error_callback: (err: any) => {
                    const desc = err?.message || err?.error_description || JSON.stringify(err);
                    if (desc.includes('origin') || desc.includes('mismatch')) {
                        reject(new Error(`Origin not allowed: Please add "${currentOrigin}" to Authorized JavaScript Origins in Google Cloud Console for Client ID.`));
                    } else {
                        reject(new Error(desc || 'Google authentication encountered an error.'));
                    }
                },
                callback: async (tokenResponse: any) => {
                    if (tokenResponse.error) {
                        const errCode = tokenResponse.error;
                        if (errCode === 'popup_closed_by_user') {
                            reject(new Error('Sign-in cancelled. Please complete sign-in in the Google popup window.'));
                            return;
                        }
                        if (errCode.includes('origin') || errCode === 'idpiframe_initialization_failed') {
                            reject(new Error(`Origin mismatch: Please add "${currentOrigin}" to Authorized JavaScript Origins in Google Cloud Console for Client ID ${GOOGLE_CLIENT_ID}.`));
                            return;
                        }
                        reject(new Error(tokenResponse.error_description || tokenResponse.error));
                        return;
                    }
                    if (tokenResponse.access_token) {
                        try {
                            const res = await fetch('https://www.googleapis.com/oauth2/v3/userinfo', {
                                headers: { Authorization: `Bearer ${tokenResponse.access_token}` }
                            });
                            if (!res.ok) {
                                throw new Error(`Google UserInfo returned HTTP ${res.status}`);
                            }
                            const info = await res.json();
                            const profile = {
                                uid: info.sub,
                                email: info.email,
                                displayName: info.name || info.email?.split('@')[0],
                                photoURL: info.picture,
                                emailVerified: Boolean(info.email_verified),
                                providerData: [{ providerId: 'google.com' }]
                            };
                            await syncUserProfile(profile);
                            resolve(profile);
                        } catch (fetchErr: any) {
                            reject(new Error('Unable to retrieve profile from Google: ' + fetchErr.message));
                        }
                    }
                }
            });

            client.requestAccessToken();
        } catch (err: any) {
            reject(err);
        }
    });
}

/**
 * Fallback: Full-page OAuth redirect (works 100% when popups or third-party cookies are restricted)
 */
export function signInWithGoogleRedirectFlow(): void {
    if (typeof window === 'undefined') return;
    const redirectUri = window.location.origin + window.location.pathname;
    const params = new URLSearchParams({
        client_id: GOOGLE_CLIENT_ID,
        redirect_uri: redirectUri,
        response_type: 'token',
        scope: 'email profile openid',
        prompt: 'select_account',
        include_granted_scopes: 'true'
    });
    window.location.href = `https://accounts.google.com/o/oauth2/v2/auth?${params.toString()}`;
}

/**
 * Checks for OAuth 2.0 access_token in the URL fragment (#access_token=...) after a redirect flow
 */
export async function checkGoogleRedirectHash(): Promise<any> {
    if (typeof window === 'undefined') return null;
    const hash = window.location.hash;
    if (!hash || !hash.includes('access_token=')) return null;

    try {
        const params = new URLSearchParams(hash.substring(1));
        const accessToken = params.get('access_token');
        if (!accessToken) return null;

        // Clean the hash from the browser URL so it does not persist in history
        history.replaceState(null, '', window.location.pathname + window.location.search);

        const res = await fetch('https://www.googleapis.com/oauth2/v3/userinfo', {
            headers: { Authorization: `Bearer ${accessToken}` }
        });
        if (!res.ok) return null;
        const info = await res.json();
        const profile = {
            uid: info.sub,
            email: info.email,
            displayName: info.name || info.email?.split('@')[0],
            photoURL: info.picture,
            emailVerified: Boolean(info.email_verified),
            providerData: [{ providerId: 'google.com' }]
        };
        await syncUserProfile(profile);
        return profile;
    } catch (e) {
        console.warn('OAuth redirect hash processing notice:', e);
        return null;
    }
}

/**
 * Unified Google Sign-in:
 * - If genuine Firebase API key is present: uses Firebase Auth popup.
 * - Otherwise: skips Firebase completely to avoid 'auth/api-key-not-valid' and directly uses GIS.
 */
export async function performGoogleSignIn(): Promise<any> {
    if (hasValidFirebaseApiKey) {
        try {
            const result = await signInWithPopup(auth, googleProvider);
            if (result && result.user) {
                await syncUserProfile(result.user);
                return result.user;
            }
        } catch (err: any) {
            console.warn('Firebase Auth popup failed, proceeding to Direct Google Identity:', err?.code || err?.message);
        }
    }

    // Direct Google Identity Services with user's Google Client ID
    return await signInWithGoogleDirect();
}
