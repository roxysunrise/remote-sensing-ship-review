/* 会话存储：登录页与工作台共用的唯一入口。
 *
 * 存储位置由「记住登录」决定：
 *   勾选 → localStorage   （7 天，关闭浏览器仍保留）
 *   不勾选 → sessionStorage（仅当前标签页，关闭即失效）
 *
 * token 的键名与 api/client.js 保持一致（dsh.ship.token），
 * 因此工作台里的 API 客户端无需知道会话存在哪里。
 */
(function (global) {
  'use strict';

  const TOKEN_KEY = 'dsh.ship.token';
  const SESSION_KEY = 'dsh.ship.session';
  const LAST_USER_KEY = 'dsh.ship.lastUser';
  const LAST_PROJECT_KEY = 'dsh.ship.lastProject';
  const DEFAULT_TTL = 7 * 24 * 60 * 60 * 1000;

  function safe(store, fn) {
    try { return store ? fn(store) : null; } catch (_) { return null; }
  }

  function stores() {
    return [global.sessionStorage, global.localStorage];
  }

  function readRaw(key) {
    for (const store of stores()) {
      const value = safe(store, s => s.getItem(key));
      if (value) return value;
    }
    return null;
  }

  function removeEverywhere(key) {
    for (const store of stores()) safe(store, s => s.removeItem(key));
  }

  function clear() {
    removeEverywhere(TOKEN_KEY);
    removeEverywhere(SESSION_KEY);
  }

  function read() {
    const raw = readRaw(SESSION_KEY);
    if (!raw) return null;
    let session;
    try { session = JSON.parse(raw); } catch (_) { clear(); return null; }
    if (!session || typeof session.token !== 'string' || !session.token) { clear(); return null; }
    if (!session.user || !session.user.id) { clear(); return null; }
    if (session.expiresAt && Date.now() >= session.expiresAt) { clear(); return null; }
    return session;
  }

  function token() {
    const session = read();
    if (session) return session.token;
    return readRaw(TOKEN_KEY) || '';
  }

  function save(tokenValue, user, remember, ttlMs) {
    if (!tokenValue || !user || !user.id) throw new Error('AuthSession.save: token 与 user.id 必填');
    clear();
    const ttl = typeof ttlMs === 'number' ? ttlMs : (remember ? DEFAULT_TTL : 0);
    const now = Date.now();
    const session = {
      token: tokenValue,
      user: user,
      remember: !!remember,
      savedAt: now,
      expiresAt: ttl > 0 ? now + ttl : 0
    };
    const store = remember ? global.localStorage : global.sessionStorage;
    if (!store) throw new Error('AuthSession.save: 浏览器不支持所选存储');
    store.setItem(TOKEN_KEY, tokenValue);
    store.setItem(SESSION_KEY, JSON.stringify(session));
    return session;
  }

  function isAuthed() {
    return !!read();
  }

  function loginUrl(next) {
    const base = (global.AUTH_CONFIG && global.AUTH_CONFIG.loginUrl) || 'login.html';
    if (!next) return base;
    return base + '?next=' + encodeURIComponent(next);
  }

  /* 工作台启动时调用：无会话则跳登录页，并带上返回路径。 */
  function requireAuth() {
    if (read()) return true;
    const file = (global.location.pathname.split('/').pop() || 'index.html');
    global.location.replace(loginUrl(file + global.location.search));
    return false;
  }

  global.AuthSession = {
    TOKEN_KEY,
    SESSION_KEY,
    LAST_USER_KEY,
    LAST_PROJECT_KEY,
    read,
    token,
    save,
    clear,
    isAuthed,
    loginUrl,
    requireAuth
  };
})(window);
