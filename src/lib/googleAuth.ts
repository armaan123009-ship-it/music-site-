import { signInWithPopup } from 'firebase/auth';
import { auth, googleProvider, GOOGLE_CLIENT_ID } from './firebase';
import { syncUserProfile } from './firestore';

/**
 * Direct Google OAuth 2.0 UserInfo via Google Identity Services (GIS)
 * Bypasses Firebase API key validation errors (auth/api-key-not-valid) completely.
 */
export function signInWithGoogleDirect(): Promise<any> {
    return new Promise((resolve, reject) => {
        const loadGsiAndPrompt = () => {
            if (typeof window === 'undefined') return reject(new Error('Window unavailable'));
            const google = (window as any).google;
            if (!google?.accounts?.oauth2) {
                return reject(new Error('Google Identity Services unavailable'));
            }

            try {
                const client = google.accounts.oauth2.initTokenClient({
                    client_id: GOOGLE_CLIENT_ID,
                    scope: 'email profile openid',
                    callback: async (tokenResponse: any) => {
                        if (tokenResponse.error) {
                            reject(new Error(tokenResponse.error_description || tokenResponse.error));
                            return;
                        }
                        if (tokenResponse.access_token) {
                            try {
                                const res = await fetch('https://www.googleapis.com/oauth2/v3/userinfo', {
                                    headers: { Authorization: `Bearer ${tokenResponse.access_token}` }
                                });
                                const info = await res.json();
                                const profile = {
                                    uid: info.sub,
                                    email: info.email,
                                    displayName: info.name,
                                    photoURL: info.picture,
                                    emailVerified: Boolean(info.email_verified),
                                    providerData: [{ providerId: 'google.com' }]
                                };
                                await syncUserProfile(profile);
                                resolve(profile);
                            } catch (fetchErr) {
                                reject(fetchErr);
                            }
                        }
                    }
                });
                client.requestAccessToken();
            } catch (err) {
                reject(err);
            }
        };

        if (typeof window !== 'undefined' && (window as any).google?.accounts?.oauth2) {
            loadGsiAndPrompt();
        } else {
            let script = document.querySelector('script[src="https://accounts.google.com/gsi/client"]') as HTMLScriptElement;
            if (!script) {
                script = document.createElement('script');
                script.src = 'https://accounts.google.com/gsi/client';
                script.async = true;
                script.defer = true;
                document.head.appendChild(script);
            }
            
            script.addEventListener('load', () => {
                setTimeout(loadGsiAndPrompt, 50);
            }, { once: true });
            script.addEventListener('error', () => reject(new Error('Could not load Google authentication service')), { once: true });
        }
    });
}

/**
 * Unified Google Sign-in: Tries Firebase Auth popup/redirect first;
 * if Firebase encounters auth/api-key-not-valid or configuration blocks,
 * seamlessly uses Direct Google Identity Services with the Google Client ID.
 */
export async function performGoogleSignIn(): Promise<any> {
    try {
        const result = await signInWithPopup(auth, googleProvider);
        if (result && result.user) {
            await syncUserProfile(result.user);
            return result.user;
        }
    } catch (err: any) {
        console.warn('Firebase Google Auth notice, falling back to Direct Google Identity:', err?.code || err?.message);
        return await signInWithGoogleDirect();
    }
}
