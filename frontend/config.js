/* 运行时配置。
 *
 * 默认接真实后端（A 侧 `backend/`）。推荐用 `scripts/start-dev.ps1` 启动：
 * 它用 `scripts/serve.py` 把页面和 `/api/v1` 挂在同一个 origin 下，
 * 因此 `baseUrl` 留空即可，不需要改端口也不需要 CORS。
 *
 * apiMode: 'http' → 真实后端，按 api/openapi.yaml 实现（默认）
 *          'mock' → 浏览器内模拟后端，离线可用，仅用于界面演示
 */
window.APP_CONFIG = {
  apiMode: 'http',

  // 留空表示与页面同源（推荐）；后端独立部署在不同 origin 时填完整地址
  baseUrl: '',
  apiPrefix: '/api/v1',

  // 轮询间隔（毫秒）。任务进度与导出状态都用它。
  pollIntervalMs: 250,

  // 上传分片大小（字节）；真实后端可在 createUpload 响应里覆盖
  chunkSize: 1024 * 1024
};
