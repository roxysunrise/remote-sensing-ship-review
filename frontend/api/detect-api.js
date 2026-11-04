/* 检测工作台的唯一数据出入口。
 *
 * 界面（detect.js）只调用 window.DetectAPI，不直接 fetch，也不直接跑模型。
 * 两个适配器方法签名一致：
 *   mode='mock' → api/detect-mock.js（浏览器内启发式，离线演示）
 *   mode='http' → 真实推理服务（见 api/DETECT_API.md / api/detect-openapi.yaml）
 *
 * 请求对象（detect / segment）：
 *   {
 *     session_id, kind: 'image'|'video',
 *     frame: { index, time_ms, width, height, canvas },   // canvas 仅本地 mock 使用，HTTP 序列化时剔除
 *     image: { data_url, width, height },                  // 下采样后的 JPEG，坐标需按比例还原
 *     roi: [x0,y0,x1,y1], point: [x,y], prompt, options
 *   }
 */
'use strict';

(function (global) {
  class DetectApiError extends Error {
    constructor(status, code, message, details) {
      super(message || code);
      this.name = 'DetectApiError';
      this.status = status;
      this.code = code;
      this.details = details;
    }
  }

  const TOKEN_KEY = 'dsh.ship.token';

  function authToken() {
    if (global.AuthSession && typeof global.AuthSession.token === 'function') {
      return global.AuthSession.token();
    }
    try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (_) { return ''; }
  }

  function endpoint(cfg, key) {
    const map = cfg.endpoints || {};
    const defaults = {
      capabilities: '/vision/capabilities',
      sessions: '/vision/sessions',
      detect: '/vision/detect',
      segment: '/vision/segment',
      cancel: '/vision/jobs/{job_id}/cancel'
    };
    return map[key] || defaults[key];
  }

  /** 去掉仅本地可用的 canvas，避免 JSON.stringify 产生空对象或循环引用。 */
  function serialize(req) {
    const out = Object.assign({}, req);
    if (out.frame) {
      const frame = Object.assign({}, out.frame);
      delete frame.canvas;
      out.frame = frame;
    }
    if (out.image) {
      out.image = {
        data_url: out.image.data_url,
        width: out.image.width,
        height: out.image.height
      };
    }
    return out;
  }

  function createHttpAdapter(cfg) {
    const base = String(cfg.baseUrl || '').replace(/\/$/, '');
    const prefix = cfg.apiPrefix || '/api/v1';
    const timeout = Number(cfg.timeoutMs) > 0 ? Number(cfg.timeoutMs) : 20000;

    function url(path) {
      return base + prefix + path;
    }

    async function request(method, path, body) {
      const ctrl = new AbortController();
      const timer = setTimeout(() => {
        try { ctrl.abort(); } catch (_) { /* ignore */ }
      }, timeout);

      const headers = { Accept: 'application/json' };
      const token = authToken();
      if (token) headers.Authorization = 'Bearer ' + token;
      let payload;
      if (body !== undefined && body !== null) {
        headers['Content-Type'] = 'application/json';
        payload = JSON.stringify(body);
      }

      let res;
      try {
        res = await fetch(url(path), {
          method, headers, body: payload, signal: ctrl.signal, credentials: 'same-origin'
        });
      } catch (err) {
        if (err && err.name === 'AbortError') {
          throw new DetectApiError(0, 'TIMEOUT', `推理服务 ${timeout} ms 内未响应`);
        }
        throw new DetectApiError(0, 'NETWORK_ERROR', '无法连接推理服务：' + (err.message || err));
      } finally {
        clearTimeout(timer);
      }

      const text = await res.text();
      let data = null;
      try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }

      if (!res.ok) {
        const e = (data && data.error) || {};
        throw new DetectApiError(
          res.status,
          e.code || 'HTTP_' + res.status,
          e.message || res.statusText || '推理服务返回错误',
          e.details);
      }
      return data;
    }

    return {
      mode: 'http',
      baseUrl: url(''),

      capabilities: () => request('GET', endpoint(cfg, 'capabilities')),
      createSession: input => request('POST', endpoint(cfg, 'sessions'), input),
      detect: req => request('POST', endpoint(cfg, 'detect'), serialize(req)),
      segment: req => request('POST', endpoint(cfg, 'segment'), serialize(req)),
      cancel: jobId => request('POST',
        endpoint(cfg, 'cancel').replace('{job_id}', encodeURIComponent(jobId)), {})
    };
  }

  function createMockAdapter(cfg) {
    if (typeof global.createDetectMock !== 'function') {
      throw new Error('detect-mock.js 未加载，无法使用 mock 适配器');
    }
    const impl = global.createDetectMock(cfg);
    return Object.assign({ mode: 'mock', baseUrl: '(local heuristic)' }, impl);
  }

  function createAPI(config) {
    const cfg = config || { mode: 'mock' };

    function make(mode) {
      if (mode === 'http') return createHttpAdapter(cfg);
      if (mode === 'mock') return createMockAdapter(cfg);
      throw new Error('未知接口模式：' + mode);
    }

    let adapter = make(cfg.mode === 'http' ? 'http' : 'mock');

    async function invoke(name) {
      const args = Array.prototype.slice.call(arguments, 1);
      try {
        return await adapter[name].apply(adapter, args);
      } catch (err) {
        if (err instanceof DetectApiError) throw err;
        throw new DetectApiError(
          err && err.status ? err.status : 500,
          (err && err.code) || 'INTERNAL_ERROR',
          (err && err.message) || String(err),
          err && err.details);
      }
    }

    return {
      get mode() { return adapter.mode; },
      get baseUrl() { return adapter.baseUrl; },
      get config() { return cfg; },

      /** 运行时切换，便于用 ?api=http 联调；正式部署直接改 detect-config.js。 */
      setMode(mode) {
        adapter = make(mode);
        return adapter.mode;
      },

      capabilities: () => invoke('capabilities'),
      createSession: input => invoke('createSession', input),
      detect: req => invoke('detect', req),
      segment: req => invoke('segment', req),
      cancel: jobId => invoke('cancel', jobId)
    };
  }

  global.DetectApiError = DetectApiError;
  global.DetectAPI = createAPI(global.DETECT_CONFIG || { mode: 'mock' });
})(window);
