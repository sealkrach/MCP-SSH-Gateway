/**
 * api.js — client REST pour MCP SSH Gateway
 * Token stocké en mémoire React (pas localStorage)
 */

const BASE = import.meta.env.VITE_API_URL || '/api'

let _token = null
let _onUnauth = null  // callback → retour login

export const setToken   = t  => { _token = t }
export const clearToken = () => { _token = null }
export const onUnauth   = fn => { _onUnauth = fn }

async function req(method, path, { body, params } = {}) {
  const url = new URL(BASE + path, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v) })
  }
  const headers = { 'Content-Type': 'application/json' }
  if (_token) headers['Authorization'] = `Bearer ${_token}`

  const res = await fetch(url, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })

  if (res.status === 401) {
    clearToken()
    _onUnauth?.()
    throw new Error('Session expirée')
  }
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    let msg = text
    try { msg = JSON.parse(text)?.detail || text } catch {}
    throw new Error(msg)
  }
  if (res.status === 204) return null
  return res.json()
}

// ── Auth ──────────────────────────────────────────────────────────────────────
export const auth = {
  login:    (username, password) => req('POST', '/auth/token', { body: { username, password } }),
  me:       ()                   => req('GET',  '/auth/me'),
  users:    ()                   => req('GET',  '/auth/users'),
}

// ── API Tokens ────────────────────────────────────────────────────────────────
export const tokens = {
  list:   ()                              => req('GET',    '/auth/tokens'),
  create: (name, username, roles, expires_days) =>
    req('POST', '/auth/tokens', { body: { name, username: username || null, roles: roles || null, expires_days: expires_days || null } }),
  revoke: (id)                            => req('DELETE', `/auth/tokens/${id}`),
}

// ── Hosts ─────────────────────────────────────────────────────────────────────
export const hosts = {
  list:   ()          => req('GET',    '/hosts'),
  create: (body)      => req('POST',   '/hosts',        { body }),
  update: (id, body)  => req('PUT',    `/hosts/${id}`,  { body }),
  delete: (id)        => req('DELETE', `/hosts/${id}`),
}

// ── Exec ──────────────────────────────────────────────────────────────────────
export const exec = (host, command, timeout = 60) =>
  req('POST', '/exec', { body: { host, command, timeout } })

// ── Audit ─────────────────────────────────────────────────────────────────────
export const audit = ({ limit = 50, env, status, user_filter } = {}) =>
  req('GET', '/audit', { params: { limit, env, status, user_filter } })

// ── Policies ──────────────────────────────────────────────────────────────────
export const policies = {
  list:   ()          => req('GET',    '/policies'),
  create: (body)      => req('POST',   '/policies',        { body }),
  update: (id, body)  => req('PUT',    `/policies/${id}`,  { body }),
  delete: (id)        => req('DELETE', `/policies/${id}`),
}

// ── Metrics ───────────────────────────────────────────────────────────────────
export const metrics = () => req('GET', '/metrics/summary')

// ── Vault KV credentials ──────────────────────────────────────────────────────
export const vault = {
  store:  (private_key, passphrase) => req('POST',   '/vault/credentials', { body: { private_key, passphrase: passphrase || null } }),
  status: ()                        => req('GET',    '/vault/credentials/status'),
  delete: ()                        => req('DELETE', '/vault/credentials'),
}

// ── Health ────────────────────────────────────────────────────────────────────
export const health = () => req('GET', '/health')
