import { initializeApp, getApps, getApp } from 'firebase/app';
import { getAuth, GoogleAuthProvider } from 'firebase/auth';
import { getFirestore } from 'firebase/firestore';

const firebaseConfig = {
    apiKey: import.meta.env.PUBLIC_FIREBASE_API_KEY || "demo-api-key",
    authDomain: import.meta.env.PUBLIC_FIREBASE_AUTH_DOMAIN || "demo-app.firebaseapp.com",
    projectId: import.meta.env.PUBLIC_FIREBASE_PROJECT_ID || "demo-app",
    storageBucket: import.meta.env.PUBLIC_FIREBASE_STORAGE_BUCKET || "demo-app.appspot.com",
    messagingSenderId: import.meta.env.PUBLIC_FIREBASE_MESSAGING_SENDER_ID || "123456789",
    appId: import.meta.env.PUBLIC_FIREBASE_APP_ID || "1:123456789:web:demo"
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

export default app;
