import { mintAccessTokensFromSeeds } from './jwt.js';

export const PROFILE = __ENV.PROFILE || 'smoke';
export const BASE_URL = (__ENV.BASE_URL || '').replace(/\/$/, '');
export const ACCESS_TOKEN = __ENV.ACCESS_TOKEN || '';
export const ACCESS_TOKENS = loadAccessTokens();
export const USE_DIRECT_JWT = Boolean(__ENV.JWT_SECRET_BASE64);

export const IS_PRODUCTION =
  /^https?:\/\/(www\.)?seonjalal\.com(?::\d+)?(?:\/|$)/i.test(BASE_URL);

export function validateBaseUrl() {
  if (!BASE_URL || !/^https?:\/\//i.test(BASE_URL)) {
    throw new Error(
      'BASE_URL must be an http(s) URL. Example: https://dev.example.com',
    );
  }
}

export function requireAccessToken() {
  if (ACCESS_TOKENS.length === 0) {
    throw new Error(
      'ACCESS_TOKEN, ACCESS_TOKENS, or ACCESS_TOKENS_FILE is required. Never commit tokens.',
    );
  }
}

export function accessTokenForVu() {
  requireAccessToken();
  return ACCESS_TOKENS[(__VU - 1) % ACCESS_TOKENS.length];
}

export function requireEnv(name) {
  const value = __ENV[name];
  if (value === undefined || value === null || String(value).trim() === '') {
    throw new Error(`${name} is required.`);
  }
  return String(value).trim();
}

export function requirePositiveInteger(name) {
  const value = Number(requireEnv(name));
  if (!Number.isInteger(value) || value <= 0) {
    throw new Error(`${name} must be a positive integer.`);
  }
  return value;
}

function loadAccessTokens() {
  let tokens;

  if (__ENV.ACCESS_TOKENS_FILE) {
    let parsed;
    try {
      parsed = JSON.parse(open(__ENV.ACCESS_TOKENS_FILE));
    } catch (_) {
      throw new Error('ACCESS_TOKENS_FILE must contain a valid JSON array.');
    }
    if (!Array.isArray(parsed)) {
      throw new Error('ACCESS_TOKENS_FILE must contain a JSON array.');
    }
    tokens = normalizeTokens(parsed);
  } else {
    tokens = normalizeTokens((__ENV.ACCESS_TOKENS || ACCESS_TOKEN).split(','));
  }

  if (!__ENV.JWT_SECRET_BASE64) {
    return tokens;
  }

  if (!/^https:\/\/v1-benchmark\.seonjalal\.com$/i.test(BASE_URL)) {
    throw new Error('Direct JWT mode is allowed only for https://v1-benchmark.seonjalal.com.');
  }
  if (!__ENV.ACCESS_TOKENS_FILE) {
    throw new Error('Direct JWT mode requires ACCESS_TOKENS_FILE as a Test user seed.');
  }

  return mintAccessTokensFromSeeds({
    seedTokens: tokens,
    secretBase64: __ENV.JWT_SECRET_BASE64,
    issuer: String(__ENV.JWT_ISSUER || '').trim(),
  });
}

function normalizeTokens(values) {
  return values
    .map((value) => String(value).trim())
    .filter((value) => value.length > 0);
}
