import { requireEnv } from '../lib/config.js';
import { postJson } from '../lib/http.js';
import { apiTestOptions } from '../lib/profiles.js';

const ENDPOINT = 'login';
const LOGIN_EMAIL = requireEnv('LOGIN_EMAIL');
const LOGIN_PASSWORD = requireEnv('LOGIN_PASSWORD');

export const options = apiTestOptions({
  endpoint: ENDPOINT,
  exec: 'login',
  authenticated: false,
  changesData: true,
});

export function login() {
  postJson({
    path: '/api/auth/login',
    endpoint: ENDPOINT,
    authenticated: false,
    body: { email: LOGIN_EMAIL, password: LOGIN_PASSWORD },
    validateData: (data) =>
      typeof data?.accessToken === 'string' && data.accessToken.length > 0,
  });
}
