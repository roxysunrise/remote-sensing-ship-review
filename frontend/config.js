/* 运行时配置。后端就绪后只改这里。
 *
 * apiMode: 'mock' → 浏览器内模拟后端（默认，离线可用）
 *          'http' → 真实后端，按 api/openapi.yaml 实现
 */
window.APP_CONFIG = {
  apiMode: 'mock',

  // 真实后端地址；留空表示与前端同源（推荐，避免 CORS）
  baseUrl: 'http://127.0.0.1:8000',
  apiPrefix: '/api/v1',

  // 轮询间隔（毫秒）。任务进度与导出状态都用它。
  pollIntervalMs: 250,

  // 上传分片大小（字节）；真实后端可在 createUpload 响应里覆盖
  chunkSize: 1024 * 1024
};
