/* 检测工作台运行时配置。
 *
 * 默认接真实推理服务（A 侧 `backend/vision.py`）。配合 `scripts/serve.py` 同源反代，
 * baseUrl 留空即可；后端独立部署时填完整地址并确保允许 CORS。
 *
 *   mode: 'http' → 真实推理服务，按 api/DETECT_API.md 与 detect-openapi.yaml 实现（默认）
 *         'mock' → 浏览器内启发式实现，离线可跑，只用于演示交互与接口形状
 *
 * 前端不直接调用模型；所有检测 / AI 分割请求都经过 window.DetectAPI。
 */
window.DETECT_CONFIG = {
  mode: 'http',

  // 留空表示与页面同源；跨域部署时填写服务地址，并确保服务端允许 CORS
  baseUrl: '',
  apiPrefix: '/api/v1',

  // 端点模板。{job_id} 由客户端替换，便于后端沿用既有路由风格。
  endpoints: {
    capabilities: '/vision/capabilities',
    sessions: '/vision/sessions',
    detect: '/vision/detect',
    segment: '/vision/segment',
    cancel: '/vision/jobs/{job_id}/cancel'
  },

  timeoutMs: 20000,

  video: {
    // 浏览器拿不到视频真实帧率时使用该值；界面允许手动修改
    fpsFallback: 30,
    // 连续检测的最小间隔（毫秒）。避免播放时把每一帧都送到后端。
    autoIntervalMs: 900,
    // 发送给后端的帧最长边；服务端按 frame / image 尺寸比例还原坐标
    maxPayloadEdge: 1600
  },

  mock: {
    maxDetections: 80,
    jpegQuality: 0.86,
    detectLatencyMs: [55, 140],
    segmentLatencyMs: [120, 260]
  }
};
