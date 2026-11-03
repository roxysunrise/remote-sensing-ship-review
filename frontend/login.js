/* 登录页逻辑：只调用真实后端，不包含任何 mock 账号。
 *
 * 流程：
 *   1. 有本地会话 → GET /me 校验 → 直接进入工作台
 *   2. 提交 → POST /auth/login → 保存 token/user → 跳转 index.html
 *
 * 依赖：config.js（APP_CONFIG）、auth-config.js（AUTH_CONFIG）、auth-session.js
 */
'use strict';

(function () {
  const $ = (sel, root = document) => root.querySelector(sel);

  const AUTH = window.AUTH_CONFIG || {};
  const APP = window.APP_CONFIG || {};

  const BASE = (AUTH.baseUrl == null ? (APP.baseUrl || '') : AUTH.baseUrl).replace(/\/+$/, '');
  const PREFIX = (AUTH.apiPrefix || APP.apiPrefix || '/api/v1').replace(/\/+$/, '');
  const APP_URL = AUTH.appUrl || 'index.html';
  const TIMEOUT = Number(AUTH.requestTimeoutMs) > 0 ? Number(AUTH.requestTimeoutMs) : 15000;
  const REMEMBER_DAYS = Number(AUTH.rememberDays) > 0 ? Number(AUTH.rememberDays) : 7;
  const REMEMBER_TTL = REMEMBER_DAYS * 24 * 60 * 60 * 1000;
  const PROJECTS = Array.isArray(AUTH.projects) ? AUTH.projects : [];

  const S = window.AuthSession;

  const el = {
    form: $('#login-form'),
    user: $('#username'),
    pass: $('#password'),
    project: $('#project'),
    projectList: $('#project-list'),
    remember: $('#remember'),
    submit: $('#submit'),
    submitLabel: $('#submit .label'),
    alert: $('#alert'),
    alertMsg: $('#alert-msg'),
    alertCode: $('#alert-code'),
    health: $('#health'),
    endpoint: $('#endpoint'),
    caps: $('#caps'),
    reveal: $('#toggle-pass')
  };

  let busy = false;
  let composing = false;

  /* ------------------------------------------------------------------ 请求 */

  class AuthError extends Error {
    constructor(status, code, message, details) {
      super(message || code);
      this.name = 'AuthError';
      this.status = status;
      this.code = code;
      this.details = details;
    }
  }

  function apiUrl(path) {
    return BASE + PREFIX + path;
  }

  function prettyEndpoint() {
    const raw = apiUrl('');
    return raw.replace(/^https?:\/\//, '') || raw;
  }

  async function request(method, path, body, token) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
    const headers = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (token) headers.Authorization = 'Bearer ' + token;

    let res;
    try {
      res = await fetch(apiUrl(path), {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: ctrl.signal,
        credentials: 'same-origin',
        mode: 'cors'
      });
    } catch (err) {
      const aborted = err && err.name === 'AbortError';
      throw new AuthError(
        0,
        aborted ? 'TIMEOUT' : 'NETWORK_ERROR',
        aborted ? '后端响应超时' : '无法连接后端服务'
      );
    } finally {
      clearTimeout(timer);
    }

    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }

    if (!res.ok) {
      const payload = (data && data.error) || {};
      throw new AuthError(
        res.status,
        payload.code || 'HTTP_' + res.status,
        payload.message || res.statusText || '请求失败',
        payload.details
      );
    }
    return data;
  }

  /* ------------------------------------------------------------------ 界面 */

  function showAlert(tone, message, code) {
    el.alert.hidden = false;
    el.alert.dataset.tone = tone || 'error';
    el.alertMsg.textContent = message;
    if (code) {
      el.alertCode.hidden = false;
      el.alertCode.textContent = code;
    } else {
      el.alertCode.hidden = true;
      el.alertCode.textContent = '';
    }
  }

  function hideAlert() {
    el.alert.hidden = true;
    el.alertCode.hidden = true;
  }

  function setFieldError(input, message) {
    const field = input.closest('.field');
    if (!field) return;
    field.classList.toggle('invalid', !!message);
    input.setAttribute('aria-invalid', message ? 'true' : 'false');
    const err = field.querySelector('.field-err');
    if (err) err.textContent = message || '';
  }

  function clearFieldError(input) {
    if (input.getAttribute('aria-invalid') === 'true') setFieldError(input, '');
  }

  function setBusy(on, label) {
    busy = on;
    el.form.setAttribute('aria-busy', on ? 'true' : 'false');
    [el.user, el.pass, el.project, el.remember].forEach(node => { node.disabled = on; });
    el.reveal.disabled = on;
    el.submit.disabled = on;
    el.submit.classList.toggle('busy', on);
    el.submit.classList.remove('ok');
    el.submitLabel.textContent = on ? (label || '正在登录…') : '登录';
  }

  function setHealth(state, text) {
    el.health.classList.remove('ok', 'bad');
    if (state === 'ok') el.health.classList.add('ok');
    if (state === 'bad') el.health.classList.add('bad');
    const label = el.health.querySelector('span');
    if (label) label.textContent = text;
  }

  function fillProjects() {
    if (!el.projectList) return;
    el.projectList.innerHTML = '';
    for (const project of PROJECTS) {
      if (!project || !project.id) continue;
      const option = document.createElement('option');
      option.value = project.id;
      option.label = project.name ? project.name + (project.code ? ' · ' + project.code : '') : project.id;
      el.projectList.appendChild(option);
    }

    const last = safeGet(S && S.LAST_PROJECT_KEY);
    const fallback = last && PROJECTS.some(p => p.id === last) ? last : (PROJECTS[0] && PROJECTS[0].id);
    if (!el.project.value && fallback) el.project.value = fallback;
  }

  function safeGet(key) {
    try { return localStorage.getItem(key); } catch (_) { return null; }
  }

  function safeSet(key, value) {
    try { value ? localStorage.setItem(key, value) : localStorage.removeItem(key); } catch (_) { /* 隐私模式忽略 */ }
  }

  /* ------------------------------------------------------------------ 校验 */

  function validateUser() {
    const value = el.user.value.trim();
    let message = '';
    if (!value) message = '请输入账号';
    else if (value.length > 64) message = '账号不能超过 64 个字符';
    setFieldError(el.user, message);
    return !message;
  }

  function validatePass() {
    const value = el.pass.value;
    let message = '';
    if (!value) message = '请输入密码';
    else if (value.length > 256) message = '密码过长';
    setFieldError(el.pass, message);
    return !message;
  }

  function validateProject() {
    const value = el.project.value.trim();
    let message = '';
    if (!value) message = '请输入项目编号';
    else if (value.length > 64) message = '项目编号不能超过 64 个字符';
    setFieldError(el.project, message);
    return !message;
  }

  /* ------------------------------------------------------------------ 跳转 */

  function nextUrl() {
    const raw = new URLSearchParams(location.search).get('next');
    if (!raw) return APP_URL;
    try {
      const target = new URL(raw, location.href);
      if (target.origin !== location.origin) return APP_URL;
      const file = target.pathname.split('/').pop();
      if (file === 'login.html') return APP_URL;
      return target.pathname + target.search + target.hash;
    } catch (_) {
      return APP_URL;
    }
  }

  function enterApp() {
    location.replace(nextUrl());
  }

  /* ------------------------------------------------------------------ 登录 */

  function handleLoginError(err) {
    const status = err.status || 0;
    const code = err.code || 'UNKNOWN';
    let message = err.message || '登录失败';
    let focus = null;

    if (status === 401) {
      if (code === 'INVALID_CREDENTIALS') {
        message = '账号或密码不正确，请检查后重试。';
        focus = el.pass;
      } else {
        message = '登录状态无效，请重新登录。';
      }
    } else if (status === 403) {
      message = '你不是该项目的成员，无法进入。请确认项目编号，或联系项目负责人添加成员。';
      focus = el.project;
    } else if (status === 0) {
      if (code === 'STORAGE_ERROR') {
        message = '浏览器无法保存登录状态（可能处于隐私模式），请允许本地存储后重试。';
      } else {
        message = code === 'TIMEOUT'
          ? '后端响应超时，请稍后重试。'
          : '无法连接后端服务，请确认服务已启动且地址正确。';
      }
    } else if (status === 429) {
      message = '尝试次数过多，请稍后再试。';
    } else if (status >= 500) {
      message = '服务端异常（' + status + '），请稍后重试或联系管理员。';
    } else if (status === 400) {
      message = err.message || '登录请求格式不正确。';
    }

    const detail = [status || '', code].filter(Boolean).join(' · ') +
      (status === 0 ? ' · ' + prettyEndpoint() : '');
    showAlert('error', message, detail);

    if (focus) {
      focus.focus();
      if (focus === el.pass) focus.select();
    }
  }

  async function onSubmit(event) {
    event.preventDefault();
    if (busy || composing) return;

    hideAlert();
    const okUser = validateUser();
    const okPass = validatePass();
    const okProject = validateProject();
    if (!okUser || !okPass || !okProject) {
      if (!okUser) el.user.focus();
      else if (!okPass) el.pass.focus();
      else el.project.focus();
      return;
    }

    const username = el.user.value.trim();
    const password = el.pass.value;
    const projectId = el.project.value.trim();
    const remember = !!el.remember.checked;

    setBusy(true, '正在验证…');
    const started = performance.now();

    try {
      const result = await request('POST', '/auth/login', {
        username,
        password,
        project_id: projectId
      }, null);

      if (!result || !result.token || !result.user || !result.user.id) {
        throw new AuthError(502, 'BAD_RESPONSE', '后端返回的登录数据不完整');
      }

      try {
        S.save(result.token, result.user, remember, remember ? REMEMBER_TTL : 0);
      } catch (_) {
        throw new AuthError(0, 'STORAGE_ERROR', '无法保存登录状态');
      }
      safeSet(S.LAST_USER_KEY, username);
      safeSet(S.LAST_PROJECT_KEY, projectId);

      el.submit.classList.add('ok');
      el.submitLabel.textContent = '登录成功，正在进入…';

      // 留出最短反馈时间，避免闪一下就跳走
      const elapsed = performance.now() - started;
      if (elapsed < 320) await new Promise(resolve => setTimeout(resolve, 320 - elapsed));

      enterApp();
    } catch (err) {
      setBusy(false);
      handleLoginError(err instanceof AuthError ? err : new AuthError(0, 'UNKNOWN', String(err && err.message || err)));
    }
  }

  /* ------------------------------------------------------------------ 会话恢复 */

  async function restoreSession() {
    if (!S) return;

    const session = S.read();
    const token = S.token();
    if (!session && !token) return;

    if (!token) {
      S.clear();
      return;
    }

    const remember = session ? !!session.remember : !!safeGet(S.TOKEN_KEY);

    setBusy(true, '正在恢复会话…');
    try {
      const me = await request('GET', '/me', undefined, token);
      if (!me || !me.id) throw new AuthError(401, 'UNAUTHENTICATED', '会话无效');

      S.save(token, me, remember, remember ? REMEMBER_TTL : 0);
      el.submit.classList.add('ok');
      el.submitLabel.textContent = '正在进入工作台…';
      enterApp();
    } catch (err) {
      setBusy(false);

      if (err.status === 401) {
        S.clear();
        showAlert('info', '会话已过期，请重新登录。', 'UNAUTHENTICATED');
      } else if (err.status === 403) {
        S.clear();
        showAlert('info', '当前账号已无权访问该项目，请重新登录。', 'PROJECT_FORBIDDEN');
      } else if (err.status === 0) {
        // 后端暂时不可达：保留会话，允许手工重试
        showAlert('info', '暂时无法校验已有登录状态，可以重新登录。', err.code || 'NETWORK_ERROR');
      } else {
        S.clear();
        showAlert('info', '登录状态无法校验，请重新登录。', (err.status || '') + ' · ' + (err.code || ''));
      }
    }
  }

  async function checkHealth() {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), Math.min(TIMEOUT, 5000));
    try {
      const res = await fetch(apiUrl('/health'), {
        headers: { Accept: 'application/json' },
        signal: ctrl.signal,
        credentials: 'same-origin',
        mode: 'cors'
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      let data = null;
      try { data = await res.json(); } catch (_) { data = null; }
      const degraded = data && data.status === 'degraded';
      setHealth(degraded ? '' : 'ok', degraded ? '后端降级运行' : '后端可用');
    } catch (_) {
      setHealth('bad', '后端不可达');
    } finally {
      clearTimeout(timer);
    }
  }

  /* ------------------------------------------------------------------ 绑定 */

  function bind() {
    el.form.addEventListener('submit', onSubmit);

    [el.user, el.pass, el.project].forEach(input => {
      input.addEventListener('input', () => {
        clearFieldError(input);
        if (!el.alert.hidden && el.alert.dataset.tone === 'error') hideAlert();
      });
    });

    el.user.addEventListener('compositionstart', () => { composing = true; });
    el.user.addEventListener('compositionend', () => { composing = false; });

    el.pass.addEventListener('keyup', event => {
      if (typeof event.getModifierState !== 'function') return;
      el.caps.hidden = !event.getModifierState('CapsLock');
    });
    el.pass.addEventListener('blur', () => { el.caps.hidden = true; });

    el.reveal.addEventListener('click', () => {
      const show = el.pass.type === 'password';
      el.pass.type = show ? 'text' : 'password';
      el.reveal.setAttribute('aria-pressed', String(show));
      el.reveal.setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
      el.pass.focus();
    });

    // 从工作台退出后按浏览器「后退」：恢复表单可用状态
    window.addEventListener('pageshow', event => {
      if (!event.persisted) return;
      setBusy(false);
      hideAlert();
      restoreSession();
    });
  }

  function init() {
    const endpoint = prettyEndpoint();
    el.endpoint.textContent = endpoint;
    el.endpoint.title = apiUrl('');

    if (!S) {
      showAlert('error', '会话模块未加载，无法登录。请检查 auth-session.js。', 'AUTH_SESSION_MISSING');
      setBusy(true, '无法登录');
      return;
    }

    const params = new URLSearchParams(location.search);
    if (params.get('logout') === '1') {
      showAlert('info', '已安全退出登录。', '');
      params.delete('logout');
      const query = params.toString();
      try {
        history.replaceState(null, '', location.pathname + (query ? '?' + query : '') + location.hash);
      } catch (_) { /* file:// 下可能受限 */ }
    }

    fillProjects();
    bind();
    checkHealth();

    const lastUser = safeGet(S.LAST_USER_KEY);
    if (lastUser) {
      el.user.value = lastUser;
      el.pass.focus();
    }

    restoreSession();
  }

  init();
})();
