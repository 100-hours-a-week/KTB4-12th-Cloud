import http from 'k6/http';
import { check } from 'k6';

import { accessTokenForVu, BASE_URL } from './config.js';
import { businessErrors } from './metrics.js';

export function getJson({ path, endpoint, validateData, authenticated = true }) {
  return requestJson({
    method: 'GET',
    path,
    endpoint,
    validateData,
    authenticated,
  });
}

export function postJson({
  path,
  endpoint,
  body,
  validateData,
  authenticated = true,
  headers = {},
  expectedStatus = 200,
}) {
  return requestJson({
    method: 'POST',
    path,
    endpoint,
    body,
    validateData,
    authenticated,
    headers,
    expectedStatus,
  });
}

function requestJson({
  method,
  path,
  endpoint,
  body,
  validateData,
  authenticated,
  headers = {},
  expectedStatus = 200,
}) {
  const tags = { endpoint };
  const requestHeaders = {
    Accept: 'application/json',
    ...headers,
  };

  if (authenticated) {
    requestHeaders.Authorization = `Bearer ${accessTokenForVu()}`;
  }

  let response;
  if (method === 'POST') {
    requestHeaders['Content-Type'] = 'application/json';
    response = http.post(`${BASE_URL}${path}`, JSON.stringify(body), {
      headers: requestHeaders,
      tags,
      timeout: '5s',
    });
  } else {
    response = http.get(`${BASE_URL}${path}`, {
      headers: requestHeaders,
      tags,
      timeout: '5s',
    });
  }

  let data = null;
  let valid = false;

  if (response.status === expectedStatus) {
    try {
      const parsed = response.json();
      data = parsed?.data ?? null;
      valid = parsed?.error == null && validateData(data);
    } catch (_) {
      valid = false;
    }
  }

  businessErrors.add(!valid, tags);
  check(
    response,
    {
      [`${endpoint}: status is ${expectedStatus}`]:
        (result) => result.status === expectedStatus,
      [`${endpoint}: response contract is valid`]: () => valid,
    },
    tags,
  );

  return { response, data, valid };
}
