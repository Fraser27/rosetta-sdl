/**
 * Cognito authentication helpers.
 *
 * Uses the Cognito Hosted UI (OAuth2 Authorization Code flow).
 * Config is loaded from /runtime-config.json (deployed by CDK) at startup,
 * with Vite env vars as fallback for local dev.
 */

interface RuntimeConfig {
  cognitoUserPoolId?: string;
  cognitoClientId?: string;
  cognitoRegion?: string;
  cognitoDomain?: string;
}

let runtimeConfig: RuntimeConfig = {};

export async function loadRuntimeConfig(): Promise<void> {
  try {
    const res = await fetch('/runtime-config.json');
    if (res.ok) runtimeConfig = await res.json();
  } catch {
    // No runtime config — fall back to Vite env vars (local dev)
  }
}

function cfg(runtimeKey: keyof RuntimeConfig, viteKey: string): string {
  return runtimeConfig[runtimeKey] || import.meta.env[viteKey] || '';
}

const getPoolId = () => cfg('cognitoUserPoolId', 'VITE_COGNITO_USER_POOL_ID');
const getClientId = () => cfg('cognitoClientId', 'VITE_COGNITO_CLIENT_ID');
const getRegion = () => cfg('cognitoRegion', 'VITE_COGNITO_REGION') || 'us-east-1';
const getDomain = () => cfg('cognitoDomain', 'VITE_COGNITO_DOMAIN');

export function isAuthEnabled(): boolean {
  return !!(getPoolId() && getClientId() && getDomain());
}


function getRedirectUri(): string {
  return `${window.location.origin}/`;
}

/** Redirect to Cognito Hosted UI for login */
export function login(): void {
  if (!isAuthEnabled()) return;
  const params = new URLSearchParams({
    client_id: getClientId(),
    response_type: 'token',
    scope: 'openid email profile',
    redirect_uri: getRedirectUri(),
  });
  window.location.href = `https://${getDomain()}/login?${params}`;
}

/** Redirect to Cognito for logout */
export function logout(): void {
  clearTokens();
  if (!isAuthEnabled()) {
    window.location.reload();
    return;
  }
  const params = new URLSearchParams({
    client_id: getClientId(),
    logout_uri: getRedirectUri(),
  });
  window.location.href = `https://${getDomain()}/logout?${params}`;
}

/** Parse tokens from URL hash after Cognito redirect */
export function handleAuthCallback(): boolean {
  const hash = window.location.hash;
  if (!hash || !hash.includes('id_token')) return false;

  const params = new URLSearchParams(hash.substring(1));
  const idToken = params.get('id_token');
  const accessToken = params.get('access_token');
  const expiresIn = params.get('expires_in');

  if (idToken && accessToken) {
    const expiry = Date.now() + (parseInt(expiresIn || '3600') * 1000);
    localStorage.setItem('id_token', idToken);
    localStorage.setItem('access_token', accessToken);
    localStorage.setItem('token_expiry', String(expiry));

    // Parse user info from ID token
    try {
      const payload = JSON.parse(atob(idToken.split('.')[1]));
      localStorage.setItem('user_email', payload.email || payload['cognito:username'] || 'user');
    } catch {
      localStorage.setItem('user_email', 'user');
    }

    // Clean up URL
    window.history.replaceState(null, '', '/');
    return true;
  }
  return false;
}

/** Get the access token for API calls */
export function getAccessToken(): string | null {
  if (!isAuthEnabled()) return null;

  const token = localStorage.getItem('access_token');
  const expiry = parseInt(localStorage.getItem('token_expiry') || '0');

  if (!token || Date.now() > expiry) {
    clearTokens();
    return null;
  }
  return token;
}

/** Check if user is authenticated */
export function isAuthenticated(): boolean {
  if (!isAuthEnabled()) return true; // Auth disabled in local dev
  return !!getAccessToken();
}

/** Get user email */
export function getUserEmail(): string {
  return localStorage.getItem('user_email') || 'user';
}

function clearTokens(): void {
  localStorage.removeItem('id_token');
  localStorage.removeItem('access_token');
  localStorage.removeItem('token_expiry');
  localStorage.removeItem('user_email');
}
