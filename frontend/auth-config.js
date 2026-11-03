/* 登录页配置（真实后端）。
 *
 * baseUrl / apiPrefix 默认沿用 config.js 的 APP_CONFIG，避免两处地址不一致；
 * 只有登录页需要独立指向另一台后端时才在这里覆盖。
 */
window.AUTH_CONFIG = {
  // 留空 = 沿用 APP_CONFIG.baseUrl；同源部署时两者都留空
  // baseUrl: 'https://ship-api.example.com',
  // apiPrefix: '/api/v1',

  appUrl: 'index.html',
  loginUrl: 'login.html',

  // 单次请求超时；登录接口本身不应做重计算，15s 足够
  requestTimeoutMs: 15000,

  // 「记住登录」的有效期（天）
  rememberDays: 7,

  /* 项目编号候选。仅用于输入提示，不构成权限：
   * 服务端仍按账号成员关系逐请求校验，选错项目会返回 403 PROJECT_FORBIDDEN。
   * 部署时替换为真实项目，或留空由使用者手工输入。 */
  projects: [
    { id: 'harbor-a', name: '港口巡查 A 区', code: 'PRJ-A-0271' },
    { id: 'harbor-b', name: '港口巡查 B 区', code: 'PRJ-B-0272' }
  ]
};
