import { initializeApp, getApps, getApp } from 'firebase/app';
import { getAuth, GoogleAuthProvider } from 'firebase/auth';
import { getFirestore } from 'firebase/firestore';

export const GOOGLE_CLIENT_ID = import.meta.env.PUBLIC_GOOGLE_CLIENT_ID || "161268743227-d1dtao524e18tur2ilh6h7jmh6evujsj.apps.googleusercontent.com";

// Check if a real, valid Firebase API key is configured (not dummy or empty)
const rawApiKey = import.meta.env.PUBLIC_FIREBASE_API_KEY;
export const hasValidFirebaseApiKey = Boolean(
    rawApiKey && 
    rawApiKey.startsWith("AIzaSy") && 
    rawApiKey !== "AIzaSyD4sC5H6EHd97Fl9aHvcek9F2UO1KXVIVU" &&
    rawApiKey !== "your_firebase_api_key" &&
    rawApiKey !== "demo-api-key"
);

const apiKey = hasValidFirebaseApiKey
    ? rawApiKey 
    : (import.meta.env.PUBLIC_YOUTUBE_API_KEY || "AIzaSyD4sC5H6EHd97Fl9aHvcek9F2UO1KXVIVU");

const projectId = import.meta.env.PUBLIC_FIREBASE_PROJECT_ID || "webproject-music-site";
const messagingSenderId = import.meta.env.PUBLIC_FIREBASE_MESSAGING_SENDER_ID || "161268743227";

const firebaseConfig = {
    apiKey: apiKey,
    authDomain: import.meta.env.PUBLIC_FIREBASE_AUTH_DOMAIN || `${projectId}.firebaseapp.com`,
    projectId: projectId,
    storageBucket: import.meta.env.PUBLIC_FIREBASE_STORAGE_BUCKET || `${projectId}.appspot.com`,
    messagingSenderId: messagingSenderId,
    appId: import.meta.env.PUBLIC_FIREBASE_APP_ID || `1:${messagingSenderId}:web:production`
};

// Initialize Firebase app if not already initialized
const app = getApps().length === 0 ? initializeApp(firebaseConfig) : getApp();

export const db = getFirestore(app);

let authInstance: any = null;
function getAuthInstance() {
    if (!authInstance) {
        authInstance = getAuth(app);
    }
    return authInstance;
}

export const auth = new Proxy({}, {
    get(target, prop) {
        const instance = getAuthInstance();
        const value = instance[prop];
        if (typeof value === 'function') {
            return value.bind(instance);
        }
        return value;
    },
    set(target, prop, value) {
        const instance = getAuthInstance();
        instance[prop] = value;
        return true;
    }
}) as any;

export const googleProvider = new GoogleAuthProvider();
googleProvider.setCustomParameters({
    client_id: GOOGLE_CLIENT_ID,
    prompt: 'select_account'
});

export default app;
