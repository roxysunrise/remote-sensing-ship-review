/* API 客户端：唯一的数据出入口。
 *
 * 两种适配器，方法签名完全一致：
 *   mode = 'mock' → 调用 api/mock-backend.js（浏览器内模拟服务端）
 *   mode = 'http' → 调用真实后端（REST + JSON，见 api/openapi.yaml）
 *
 * UI 只依赖本文件暴露的方法，切换后端不改任何界面代码。
 */
'use strict';

(function (global) {
  class ApiError extends Error {
    constructor(status, code, message, details) {
      super(message || code);
      this.name = 'ApiError';
      this.status = status;
      this.code = code;
      this.details = details;
    }
  }

  const TOKEN_KEY = 'dsh.ship.token';

  function createHttpAdapter(config) {
    const base = (config.baseUrl || '').replace(/\/$/, '');
    const prefix = config.apiPrefix || '/api/v1';

    // 会话可能由独立登录页写入 sessionStorage（未勾选「记住登录」），
    // 统一交给 AuthSession 读取；没有该模块时退回 localStorage。
    function token() {
      if (global.AuthSession) return global.AuthSession.token();
      return localStorage.getItem(TOKEN_KEY) || '';
    }
    function setToken(t) {
      if (t) {
        localStorage.setItem(TOKEN_KEY, t);
      } else {
        localStorage.removeItem(TOKEN_KEY);
        try { sessionStorage.removeItem(TOKEN_KEY); } catch (_) { /* ignore */ }
      }
    }

    async function request(method, path, body, opts = {}) {
      const headers = Object.assign({ Accept: 'application/json' }, opts.headers || {});
      const t = token();
      if (t) headers.Authorization = 'Bearer ' + t;
      let payload;
      if (body !== undefined && body !== null) {
        if (body instanceof Blob || body instanceof ArrayBuffer || ArrayBuffer.isView(body)) {
          payload = body;
          headers['Content-Type'] = 'application/octet-stream';
        } else {
          payload = JSON.stringify(body);
          headers['Content-Type'] = 'application/json';
        }
      }
      let res;
      try {
        res = await fetch(base + prefix + path, { method, headers, body: payload, credentials: 'same-origin' });
      } catch (e) {
        throw new ApiError(0, 'NETWORK_ERROR', '无法连接后端服务：' + (e.message || e));
      }
      if (res.status === 204) return null;
      const text = await res.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }
      if (!res.ok) {
        // 已带 token 仍返回 401：会话被撤销或过期，清理本地会话并通知界面
        if (res.status === 401 && t && global.AuthSession) {
          global.AuthSession.clear();
          try { global.dispatchEvent(new CustomEvent('auth:expired')); } catch (_) { /* ignore */ }
        }
        const err = (data && data.error) || {};
        throw new ApiError(res.status, err.code || 'HTTP_' + res.status, err.message || res.statusText, err.details);
      }
      return data;
    }

    return {
      mode: 'http',
      baseUrl: base + prefix,

      async login(username, password, projectId) {
        const r = await request('POST', '/auth/login', { username, password, project_id: projectId });
        if (r && r.token) setToken(r.token);
        return r;
      },
      async logout() {
        try { await request('POST', '/auth/logout', {}); } finally { setToken(null); }
        return { ok: true };
      },
      me: () => request('GET', '/me'),

      listProjects: () => request('GET', '/projects'),
      getProject: id => request('GET', `/projects/${encodeURIComponent(id)}`),

      createUpload: (projectId, body) => request('POST', `/projects/${encodeURIComponent(projectId)}/uploads`, body),
      putChunk: (uploadId, index, bytes) => request('PUT', `/uploads/${encodeURIComponent(uploadId)}/chunks/${index}`, bytes),
      getUpload: uploadId => request('GET', `/uploads/${encodeURIComponent(uploadId)}`),
      completeUpload: (uploadId, body) => request('POST', `/uploads/${encodeURIComponent(uploadId)}/complete`, body || {}),

      getImage: id => request('GET', `/images/${encodeURIComponent(id)}`),

      createTask: (projectId, body) => request('POST', `/projects/${encodeURIComponent(projectId)}/tasks`, body),
      getTask: id => request('GET', `/tasks/${encodeURIComponent(id)}`),
      cancelTask: id => request('POST', `/tasks/${encodeURIComponent(id)}/cancel`, {}),
      retryTask: id => request('POST', `/tasks/${encodeURIComponent(id)}/retry`, {}),
      getTaskResult: id => request('GET', `/tasks/${encodeURIComponent(id)}/result`),

      getDraft: id => request('GET', `/tasks/${encodeURIComponent(id)}/draft`),
      patchDetection: (taskId, detId, patch) =>
        request('PATCH', `/tasks/${encodeURIComponent(taskId)}/draft/detections/${detId}`, patch),
      addDetection: (taskId, body) => request('POST', `/tasks/${encodeURIComponent(taskId)}/draft/detections`, body),
      resetDraft: (taskId, versionId) =>
        request('POST', `/tasks/${encodeURIComponent(taskId)}/draft/reset`, { version_id: versionId }),

      listVersions: id => request('GET', `/tasks/${encodeURIComponent(id)}/versions`),
      getVersion: (versionId, taskId) =>
        request('GET', `/versions/${encodeURIComponent(versionId)}?task_id=${encodeURIComponent(taskId)}`),
      commitVersion: (taskId, body) => request('POST', `/tasks/${encodeURIComponent(taskId)}/versions`, body || {}),

      getReviewQueue: id => request('GET', `/tasks/${encodeURIComponent(id)}/draft/review-queue`),
      createRegion: (taskId, body) => request('POST', `/tasks/${encodeURIComponent(taskId)}/draft/regions`, body),

      requestAiCandidate: (imageId, body) =>
        request('POST', `/images/${encodeURIComponent(imageId)}/ai-candidates`, body),
      acceptAiCandidate: id => request('POST', `/ai-candidates/${encodeURIComponent(id)}/accept`, {}),
      cancelAiCandidate: id => request('POST', `/ai-candidates/${encodeURIComponent(id)}/cancel`, {}),

      createExport: (versionId, body, taskId) =>
        request('POST', `/versions/${encodeURIComponent(versionId)}/exports?task_id=${encodeURIComponent(taskId)}`, body || {}),
      getExport: id => request('GET', `/exports/${encodeURIComponent(id)}`),

      health: () => request('GET', '/health')
    };
  }

  function createMockAdapter(config, scene) {
    const backend = global.createMockBackend(scene, {
      getImage: () => (config.getImage ? config.getImage() : null)
    });
    return Object.assign({ mode: 'mock', baseUrl: '(in-browser mock)' }, backend);
  }

  function createAPI(config) {
    const adapter = config.apiMode === 'http'
      ? createHttpAdapter(config)
      : createMockAdapter(config, global.SCENE);

    // 统一把适配器异常转成 ApiError，UI 只需判断 err.code / err.status
    const api = { mode: adapter.mode, baseUrl: adapter.baseUrl, config };
    for (const key of Object.keys(adapter)) {
      if (typeof adapter[key] !== 'function') continue;
      api[key] = async (...args) => {
        try {
          return await adapter[key](...args);
        } catch (e) {
          if (e instanceof ApiError) throw e;
          throw new ApiError(e.status || 500, e.code || 'INTERNAL_ERROR', e.message || String(e), e.details);
        }
      };
    }
    return api;
  }

  global.API = createAPI(global.APP_CONFIG || { apiMode: 'mock' });
  global.ApiError = ApiError;
})(window);
