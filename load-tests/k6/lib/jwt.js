import crypto from 'k6/crypto';
import encoding from 'k6/encoding';

// 기존 Token은 만료됐더라도 Test 사용자 ID를 찾는 Seed로만 사용한다.
// 새 Token은 Backend와 동일한 HS256, iss, sub, iat, exp 계약으로 매 k6 실행 때 생성한다.
export function mintAccessTokensFromSeeds({ seedTokens, secretBase64, issuer }) {
  if (!Array.isArray(seedTokens) || seedTokens.length === 0) {
    throw new Error('Direct JWT mode requires seed tokens with Test user subjects.');
  }
  if (!secretBase64 || !issuer) {
    throw new Error('JWT_SECRET_BASE64 and JWT_ISSUER are both required.');
  }

  let signingKey;
  try {
    signingKey = encoding.b64decode(secretBase64, 'std');
  } catch (_) {
    throw new Error('JWT_SECRET_BASE64 must be valid standard Base64.');
  }

  if (signingKey.byteLength < 32) {
    throw new Error('JWT signing key must decode to at least 32 bytes.');
  }

  const issuedAt = Math.floor(Date.now() / 1000);
  const expiresAt = issuedAt + 3600;
  const subjects = seedTokens.map(subjectFromSeedToken);

  if (new Set(subjects).size !== subjects.length) {
    throw new Error('Seed tokens must contain unique Test user subjects.');
  }

  return subjects.map((subject) =>
    signHs256({ subject, issuer, issuedAt, expiresAt, signingKey }),
  );
}

function subjectFromSeedToken(token) {
  const parts = String(token).split('.');
  if (parts.length !== 3) {
    throw new Error('Every seed token must use JWT compact serialization.');
  }

  let payload;
  try {
    payload = JSON.parse(encoding.b64decode(parts[1], 'rawurl', 's'));
  } catch (_) {
    throw new Error('A seed token contains an invalid JWT payload.');
  }

  const subject = String(payload?.sub ?? '');
  if (!/^[1-9][0-9]*$/.test(subject)) {
    throw new Error('Every seed token must contain a positive integer subject.');
  }
  return subject;
}

export function signHs256({ subject, issuer, issuedAt, expiresAt, signingKey }) {
  const header = encoding.b64encode(
    JSON.stringify({ alg: 'HS256', typ: 'JWT' }),
    'rawurl',
  );
  const payload = encoding.b64encode(
    JSON.stringify({
      iss: issuer,
      sub: subject,
      iat: issuedAt,
      exp: expiresAt,
    }),
    'rawurl',
  );
  const signingInput = `${header}.${payload}`;
  const hmac = crypto.createHMAC('sha256', signingKey);
  hmac.update(signingInput);

  return `${signingInput}.${hmac.digest('base64rawurl')}`;
}
