const runtimeBase = (window.__APP_CONFIG__ && window.__APP_CONFIG__.API_BASE_URL) || '';
const buildBase = import.meta.env.VITE_API_BASE_URL || '';

export const API_BASE_URL = (runtimeBase || buildBase || 'http://localhost:8000').replace(/\/+$/, '');

/** Error carrying the HTTP status so the UI can label 422 / 503 properly. */
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

function describeDetail(detail, fallback) {
  if (!detail) return fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const first = detail[0];
    if (!first) return fallback;
    const where = Array.isArray(first.loc) ? first.loc.filter((p) => p !== 'body').join('.') : '';
    return where ? `${where}: ${first.msg}` : first.msg || fallback;
  }
  return fallback;
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, options);
  } catch (networkError) {
    throw new ApiError(`Cannot reach the backend at ${API_BASE_URL}`, 0);
  }

  let body = null;
  try {
    body = await response.json();
  } catch (parseError) {
    body = null;
  }

  if (!response.ok) {
    throw new ApiError(
      describeDetail(body && body.detail, `Request failed with status ${response.status}`),
      response.status,
    );
  }
  return body;
}

export function getHealth() {
  return request('/health');
}

export function getSessionMessages(sessionId) {
  return request(`/sessions/${encodeURIComponent(sessionId)}/messages`);
}

export function postChat({ sessionId, message, systemPrompt, simulateFailure }) {
  const headers = { 'Content-Type': 'application/json' };
  if (simulateFailure) headers['X-Simulate-Failure'] = simulateFailure;

  const payload = { session_id: sessionId, message };
  if (systemPrompt && systemPrompt.trim()) payload.system_prompt = systemPrompt.trim();

  return request('/chat', { method: 'POST', headers, body: JSON.stringify(payload) });
}

export function newSessionId() {
  const random = Math.random().toString(36).slice(2, 8);
  return `s-${Date.now().toString(36)}${random}`;
}
