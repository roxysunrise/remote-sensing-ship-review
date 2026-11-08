/* Mock 后端：在浏览器内实现与 HTTP 后端相同的接口语义。
 *
 * 目的：前端只依赖 api/client.js 暴露的方法；接真实后端时把
 * config.js 的 apiMode 改成 'http' 即可，UI 代码零改动。
 *
 * 这里的“服务端状态”全部在内存中，刷新即丢失——真实后端必须持久化。
 */
'use strict';

(function (global) {
  const G = global.GEOM;

  const delay = ms => new Promise(r => setTimeout(r, ms));
  const net = () => delay(25 + Math.random() * 65);

  function apiError(status, code, message, details) {
    const e = new Error(message);
    e.status = status;
    e.code = code;
    e.details = details;
    return e;
  }

  const clone = o => JSON.parse(JSON.stringify(o));

  function createMockBackend(SCENE, options) {
    const getImage = (options && options.getImage) || (() => null);

    /* ---------------- 服务端状态 ---------------- */
    const db = {
      users: {
        'lin.analyst': { id: 'u-1', name: 'lin.analyst', role: '项目分析员', password: 'demo' },
        'roxysunrise': { id: 'u-2', name: 'roxysunrise', role: '项目负责人', password: 'demo' }
      },
      projects: {
        'harbor-a': {
          id: 'harbor-a', name: '港口巡查 A 区', code: 'PRJ-A-0271',
          members: [
            { id: 'u-2', name: 'roxysunrise', role: '项目负责人' },
            { id: 'u-1', name: 'lin.analyst', role: '项目分析员' }
          ]
        },
        'harbor-b': {
          id: 'harbor-b', name: '港口巡查 B 区', code: 'PRJ-B-0272',
          members: [{ id: 'u-3', name: 'other.user', role: '项目负责人' }]
        }
      },
      uploads: {},
      images: {},
      tasks: {},
      drafts: {},
      versions: {},
      candidates: {},
      exports: {},
      seq: { upload: 0, image: 0, task: 0, version: 0, candidate: 0, export: 0, detection: 0 }
    };

    let session = null;   // {userId, projectId, token}
    const timers = new Map();

    function requireSession() {
      if (!session) throw apiError(401, 'UNAUTHENTICATED', '会话已失效，请重新登录');
      return session;
    }

    function requireProject(projectId) {
      const s = requireSession();
      const project = db.projects[projectId];
      if (!project) throw apiError(404, 'PROJECT_NOT_FOUND', '项目不存在');
      if (!project.members.some(m => m.id === s.userId))
        throw apiError(403, 'PROJECT_FORBIDDEN', '你不是该项目成员，访问被拒绝');
      return project;
    }

    function publicUser() {
      const s = requireSession();
      const u = db.users[s.userId] || Object.values(db.users).find(x => x.id === s.userId);
      const project = db.projects[s.projectId];
      return {
        id: u.id, name: u.name,
        role: project.members.find(m => m.id === u.id).role,
        project: { id: project.id, name: project.name, code: project.code }
      };
    }

    /* ---------------- 几何辅助（服务端职责） ---------------- */
    const TILES = SCENE.tiling.tiles;
    const SEAMS = (() => {
      const xs = [...new Set(TILES.flatMap(t => [t.x, t.x + t.width]))]
        .filter(v => v > 0 && v < SCENE.image.width);
      const ys = [...new Set(TILES.flatMap(t => [t.y, t.y + t.height]))]
        .filter(v => v > 0 && v < SCENE.image.height);
      return { xs, ys };
    })();

    function seamDistance(poly) {
      let best = Infinity;
      for (const p of poly) {
        for (const s of SEAMS.xs) best = Math.min(best, Math.abs(p[0] - s));
        for (const s of SEAMS.ys) best = Math.min(best, Math.abs(p[1] - s));
      }
      return best;
    }

    function duplicatePartner(d, list) {
      const c0 = G.centroid(d.polygon);
      for (const o of list) {
        if (o.id === d.id || o.review_state === 'deleted') continue;
        if (G.pointInPolygon(c0, o.polygon) ||
            G.pointInPolygon(G.centroid(o.polygon), d.polygon)) {
          return { id: o.id, iou: G.polyIoU(o.polygon, d.polygon) };
        }
      }
      return null;
    }

    function reasonsFor(d, list) {
      const out = [];
      if (d.confidence != null && d.confidence < 0.45)
        out.push({ code: 'LOW_CONFIDENCE', message: '模型把握较低' });
      if (seamDistance(d.polygon) < 12)
        out.push({ code: 'TILE_BOUNDARY', message: '靠近处理边界' });
      const edge = Math.min(...d.polygon.map(p =>
        Math.min(p[0], p[1], SCENE.image.width - p[0], SCENE.image.height - p[1])));
      if (edge < 20)
        out.push({ code: 'IMAGE_EDGE', message: '靠近影像边缘' });
      const dup = duplicatePartner(d, list);
      if (dup)
        out.push({ code: 'DUPLICATE', message: '疑似重复检出', partner_id: dup.id, iou: +dup.iou.toFixed(4) });
      return out;
    }

    const SCORE = { LOW_CONFIDENCE: 3, TILE_BOUNDARY: 2, DUPLICATE: 2, IMAGE_EDGE: 1 };

    /* ---------------- 上传 ---------------- */
    function uploadView(u) {
      return {
        id: u.id, status: u.status, stage: u.stage,
        chunk_size: u.chunk_size, chunks_total: u.chunks_total,
        chunks_received: u.chunks_received, pct: Math.round(u.chunks_received / u.chunks_total * 100),
        image_id: u.image_id || null, error: u.error || null,
        expires_at: u.expires_at
      };
    }

    function finishUpload(u) {
      u.status = 'verifying'; u.stage = 'merge';
      setTimeout(() => { u.stage = 'inspect'; }, 700);
      setTimeout(() => { u.stage = 'preview'; }, 1350);
      setTimeout(() => {
        const id = 'img-' + (++db.seq.image);
        db.images[id] = {
          id, project_id: u.project_id, file_name: u.file_name, status: 'ready',
          sha256: SCENE.image.sha256, width: SCENE.image.width, height: SCENE.image.height,
          mode: 'RGB', bytes: u.size || null,
          file_url: 'assets/harbor.png',
          preview_url: 'assets/harbor.png',
          created_at: new Date().toISOString()
        };
        u.status = 'ready'; u.stage = 'ready'; u.image_id = id;
      }, 2000);
    }

    /* ---------------- 任务 ---------------- */
    function taskView(t) {
      return {
        id: t.id, project_id: t.project_id, image_id: t.image_id,
        status: t.status, attempt: t.attempt,
        tiles_total: TILES.length, tiles_done: t.tiles_done,
        temp_count: t.temp_count, final_count: t.final_count,
        parameters: t.parameters, started_at: t.started_at, finished_at: t.finished_at,
        cancel_requested: t.cancel_requested, error: t.error || null
      };
    }

    function tickTask(t) {
      if (t.status !== 'running') return;
      if (t.cancel_requested) {
        t.status = 'cancelled'; t.finished_at = new Date().toISOString();
        timers.delete(t.id);
        return;
      }
      t.tiles_done += 1;
      t.temp_count += TILES[t.tiles_done - 1].candidate_count;
      if (t.tiles_done >= TILES.length) {
        t.status = 'merging';
        setTimeout(() => {
          t.final_count = SCENE.count;
          t.status = 'done';
          t.finished_at = new Date().toISOString();
          timers.delete(t.id);
        }, 500);
      }
    }

    function startTaskTimer(t) {
      if (timers.has(t.id)) clearInterval(timers.get(t.id));
      timers.set(t.id, setInterval(() => tickTask(t), 160));
    }

    /* ---------------- 草稿与版本 ---------------- */
    function draftView(taskId) {
      return { task_id: taskId, detections: clone(db.drafts[taskId]) };
    }

    function versionCounts(dets) {
      const c = { total: dets.length, pending: 0, confirmed: 0, modified: 0, deleted: 0, added: 0 };
      for (const d of dets) {
        c[d.review_state] = (c[d.review_state] || 0) + 1;
        if (d.origin !== 'model') c.added += 1;
      }
      return c;
    }

    function versionView(v, withDetections) {
      const out = {
        id: v.id, task_id: v.task_id, label: v.label, author: v.author,
        created_at: v.created_at, readonly: v.readonly, summary: v.summary,
        counts: versionCounts(v.detections)
      };
      if (withDetections) out.detections = clone(v.detections);
      return out;
    }

    function diffSummary(a, b) {
      const mapA = new Map(a.map(d => [d.id, d]));
      const mapB = new Map(b.map(d => [d.id, d]));
      let added = 0, removed = 0, changed = 0, confirmed = 0, deleted = 0;
      for (const [id, d] of mapB) {
        if (!mapA.has(id)) { added += 1; continue; }
        const o = mapA.get(id);
        if (o.review_state !== d.review_state) {
          if (d.review_state === 'deleted') deleted += 1;
          if (d.review_state === 'confirmed') confirmed += 1;
        }
        if (o.polygon.some((p, i) => Math.hypot(p[0] - d.polygon[i][0], p[1] - d.polygon[i][1]) > 0.5))
          changed += 1;
      }
      for (const id of mapA.keys()) if (!mapB.has(id)) removed += 1;
      return { added, removed, changed, confirmed, deleted };
    }

    /* ---------------- 导出 ---------------- */
    function buildExport(v, formats) {
      const list = v.detections;
      const files = [];

      if (formats.includes('csv')) {
        const rows = [['id', 'class', 'confidence', 'x1', 'y1', 'x2', 'y2', 'x3', 'y3', 'x4', 'y4',
          'review_state', 'origin', 'source_tile_id', 'note']];
        for (const d of list) {
          rows.push([d.id, d.class_name, d.confidence == null ? '' : d.confidence,
            ...d.polygon.flat().map(x => Math.round(x * 100) / 100),
            d.review_state, d.origin, d.source_tile_id, (d.note || '').replace(/[\r\n]+/g, ' ')]);
        }
        const blob = new Blob(['\uFEFF' + rows.map(r => r.join(',')).join('\r\n')],
          { type: 'text/csv;charset=utf-8' });
        files.push({ format: 'csv', name: `ship-detection-${v.id}.csv`, url: URL.createObjectURL(blob), bytes: blob.size });
      }

      if (formats.includes('json')) {
        const c = versionCounts(list);
        const report = {
          schema_version: SCENE.schema_version, exported_at: new Date().toISOString(),
          version: v.id, image: SCENE.image, model: SCENE.model, parameters: SCENE.parameters,
          coordinates: SCENE.coordinates,
          tiling: {
            tile_count: SCENE.tiling.tile_count, candidate_count: SCENE.tiling.candidate_count,
            suppressed_count: SCENE.tiling.suppressed_count, merge_method: SCENE.tiling.merge_method
          },
          counts: {
            model_candidates: list.filter(d => d.origin === 'model').length,
            effective: c.total - c.deleted, deleted: c.deleted, added: c.added,
            by_state: { pending: c.pending, confirmed: c.confirmed, modified: c.modified, deleted: c.deleted }
          },
          detections: list.map(d => ({
            id: d.id, polygon: d.polygon.map(p => [Math.round(p[0] * 100) / 100, Math.round(p[1] * 100) / 100]),
            class_id: d.class_id, class_name: d.class_name, confidence: d.confidence,
            source_tile_id: d.source_tile_id, review_state: d.review_state, origin: d.origin, note: d.note || ''
          }))
        };
        const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
        files.push({ format: 'json', name: `report-${v.id}.json`, url: URL.createObjectURL(blob), bytes: blob.size });
      }

      if (formats.includes('png')) {
        const img = getImage();
        try {
          if (!img || !img.naturalWidth) throw new Error('image not loaded');
          const off = document.createElement('canvas');
          off.width = SCENE.image.width; off.height = SCENE.image.height;
          const ctx = off.getContext('2d');
          ctx.drawImage(img, 0, 0);
          ctx.lineWidth = 2;
          for (const d of list) {
            if (d.review_state === 'deleted') continue;
            ctx.beginPath();
            d.polygon.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]));
            ctx.closePath();
            ctx.strokeStyle = d.origin === 'model' ? '#38b2a3' : '#a274ff';
            ctx.stroke();
          }
          ctx.font = '600 14px sans-serif';
          ctx.fillStyle = 'rgba(0,0,0,.55)';
          ctx.fillRect(12, 12, 340, 30);
          ctx.fillStyle = '#d9e4ee';
          const c = versionCounts(list);
          ctx.fillText(`${v.id} · 有效 ${c.total - c.deleted} · 模型候选 ${list.length}`, 22, 33);
          const url = off.toDataURL('image/png');
          files.push({ format: 'png', name: `annotation-${v.id}.png`, url, bytes: Math.round(url.length * 0.75) });
        } catch (e) {
          files.push({ format: 'png', name: `annotation-${v.id}.png`, url: null, error: 'CANVAS_TAINTED' });
        }
      }
      return files;
    }

    /* ---------------- 对外接口 ---------------- */
    return {
      /* --- auth --- */
      async login(username, password, projectId) {
        await net();
        const u = db.users[username];
        if (!u || u.password !== password) throw apiError(401, 'INVALID_CREDENTIALS', '账号或密码不正确');
        const project = db.projects[projectId];
        if (!project) throw apiError(404, 'PROJECT_NOT_FOUND', '项目不存在');
        if (!project.members.some(m => m.id === u.id))
          throw apiError(403, 'PROJECT_FORBIDDEN', '你不是该项目成员，访问被拒绝');
        session = { userId: u.id, projectId, token: 'mock-token-' + u.id };
        return { token: session.token, user: publicUser() };
      },

      /* 刷新页面后用已保存的演示令牌恢复内存会话；
       * 仅用于 mock，真实后端仍走 AuthSession + Bearer。 */
      async resumeSession(token, projectId) {
        await net();
        const m = /^mock-token-(.+)$/.exec(String(token || ''));
        const userId = m && m[1];
        const u = userId && (db.users[userId] || Object.values(db.users).find(x => x.id === userId));
        const project = db.projects[projectId];
        if (!u || !project || !project.members.some(x => x.id === u.id)) {
          session = null;
          throw apiError(401, 'UNAUTHENTICATED', '会话已失效，请重新登录');
        }
        session = { userId: u.id, projectId, token: 'mock-token-' + u.id };
        return { user: publicUser() };
      },

      async logout() {
        await net();
        session = null;
        return { ok: true };
      },

      async me() {
        await net();
        return publicUser();
      },

      /* --- projects --- */
      async listProjects() {
        await net();
        requireSession();
        return Object.values(db.projects).map(p => ({
          id: p.id, name: p.name, code: p.code,
          role: (p.members.find(m => m.id === session.userId) || {}).role || null,
          member: p.members.some(m => m.id === session.userId)
        }));
      },

      async getProject(projectId) {
        await net();
        const p = requireProject(projectId);
        return { id: p.id, name: p.name, code: p.code, members: clone(p.members) };
      },

      /* --- uploads --- */
      async createUpload(projectId, body) {
        await net();
        requireProject(projectId);
        const id = 'up-' + (++db.seq.upload);
        const chunks = 24;
        db.uploads[id] = {
          id, project_id: projectId, file_name: body.file_name, size: body.size || null,
          chunk_size: 1048576, chunks_total: chunks, chunks_received: 0,
          status: 'uploading', stage: 'transport', image_id: null,
          expires_at: new Date(Date.now() + 3600e3).toISOString()
        };
        return uploadView(db.uploads[id]);
      },

      async putChunk(uploadId, index, bytes) {
        await net();
        const u = db.uploads[uploadId];
        if (!u) throw apiError(404, 'UPLOAD_NOT_FOUND', '上传会话不存在或已过期');
        requireProject(u.project_id);
        if (u.status !== 'uploading') throw apiError(409, 'UPLOAD_CLOSED', '上传会话已关闭');
        if (index < 0 || index >= u.chunks_total) throw apiError(400, 'CHUNK_OUT_OF_RANGE', '分片序号越界');
        // 服务端以已接收分片为准：重复提交不追加
        if (index === u.chunks_received) u.chunks_received += 1;
        else if (index > u.chunks_received) throw apiError(409, 'CHUNK_OUT_OF_ORDER', '分片顺序错误');
        return { upload_id: u.id, index, chunks_received: u.chunks_received, pct: Math.round(u.chunks_received / u.chunks_total * 100) };
      },

      async getUpload(uploadId) {
        await net();
        const u = db.uploads[uploadId];
        if (!u) throw apiError(404, 'UPLOAD_NOT_FOUND', '上传会话不存在或已过期');
        requireProject(u.project_id);
        return uploadView(u);
      },

      async completeUpload(uploadId, body) {
        await net();
        const u = db.uploads[uploadId];
        if (!u) throw apiError(404, 'UPLOAD_NOT_FOUND', '上传会话不存在或已过期');
        requireProject(u.project_id);
        if (u.status === 'ready') return uploadView(u);
        if (u.chunks_received < u.chunks_total)
          throw apiError(409, 'UPLOAD_INCOMPLETE', `还有 ${u.chunks_total - u.chunks_received} 个分片未收到`);
        if (body && body.sha256 && body.sha256 !== SCENE.image.sha256)
          throw apiError(422, 'CHECKSUM_MISMATCH', '文件校验和不一致');
        if (u.status === 'uploading') finishUpload(u);
        return uploadView(u);
      },

      /* --- images --- */
      async getImage(imageId) {
        await net();
        const img = db.images[imageId];
        if (!img) throw apiError(404, 'IMAGE_NOT_FOUND', '影像不存在');
        requireProject(img.project_id);
        return clone(img);
      },

      /* --- tasks --- */
      async createTask(projectId, body) {
        await net();
        requireProject(projectId);
        const img = db.images[body.image_id];
        if (!img) throw apiError(404, 'IMAGE_NOT_FOUND', '影像不存在');
        if (img.status !== 'ready')
          throw apiError(409, 'IMAGE_NOT_READY', '影像尚未通过验证，不能创建检测任务');
        const id = 'task-' + (++db.seq.task);
        const t = {
          id, project_id: projectId, image_id: img.id, status: 'running', attempt: 1,
          tiles_done: 0, temp_count: 0, final_count: null,
          parameters: body.parameters || SCENE.parameters,
          started_at: new Date().toISOString(), finished_at: null,
          cancel_requested: false, error: null
        };
        db.tasks[id] = t;

        // 结果一旦产生就固定下来；v0 版本由服务端在任务完成时写入
        const dets = SCENE.detections.map(d => ({
          id: d.id, polygon: clone(d.polygon), class_id: d.class_id, class_name: d.class_name,
          confidence: d.confidence, source_tile_id: d.source_tile_id,
          review_state: 'pending', origin: 'model', note: ''
        }));
        db.drafts[id] = clone(dets);
        const v0 = {
          id: 'v0', task_id: id, label: '模型原始结果', author: 'YOLOv8l-OBB',
          created_at: new Date().toISOString(), readonly: true,
          summary: null, detections: clone(dets)
        };
        db.versions[id] = [v0];
        db.seq.detection = Math.max(...dets.map(d => d.id));

        startTaskTimer(t);
        return taskView(t);
      },

      async getTask(taskId) {
        await net();
        const t = db.tasks[taskId];
        if (!t) throw apiError(404, 'TASK_NOT_FOUND', '任务不存在');
        requireProject(t.project_id);
        return taskView(t);
      },

      async cancelTask(taskId) {
        await net();
        const t = db.tasks[taskId];
        if (!t) throw apiError(404, 'TASK_NOT_FOUND', '任务不存在');
        requireProject(t.project_id);
        if (t.status === 'running') t.cancel_requested = true;
        return taskView(t);
      },

      async retryTask(taskId) {
        await net();
        const t = db.tasks[taskId];
        if (!t) throw apiError(404, 'TASK_NOT_FOUND', '任务不存在');
        requireProject(t.project_id);
        if (['running', 'queued'].includes(t.status))
          throw apiError(409, 'TASK_BUSY', '任务正在运行，不能重试');
        t.attempt += 1;
        t.status = 'running';
        t.tiles_done = 0; t.temp_count = 0; t.final_count = null;
        t.cancel_requested = false; t.finished_at = null; t.error = null;
        t.started_at = new Date().toISOString();
        db.drafts[taskId] = clone(db.versions[taskId][0].detections);
        startTaskTimer(t);
        return taskView(t);
      },

      async getTaskResult(taskId) {
        await net();
        const t = db.tasks[taskId];
        if (!t) throw apiError(404, 'TASK_NOT_FOUND', '任务不存在');
        requireProject(t.project_id);
        if (t.status !== 'done')
          throw apiError(409, 'RESULT_NOT_READY', '任务尚未完成，最终结果未发布');
        return {
          schema_version: SCENE.schema_version, created_at: SCENE.created_at,
          image: SCENE.image, model: SCENE.model, parameters: t.parameters,
          coordinates: SCENE.coordinates, tiling: SCENE.tiling,
          count: t.final_count, detections: clone(db.versions[taskId][0].detections)
        };
      },

      /* --- draft --- */
      async getDraft(taskId) {
        await net();
        const t = db.tasks[taskId];
        if (!t) throw apiError(404, 'TASK_NOT_FOUND', '任务不存在');
        requireProject(t.project_id);
        return draftView(taskId);
      },

      async patchDetection(taskId, detectionId, patch) {
        await net();
        const t = db.tasks[taskId];
        if (!t) throw apiError(404, 'TASK_NOT_FOUND', '任务不存在');
        requireProject(t.project_id);
        const d = db.drafts[taskId].find(x => x.id === detectionId);
        if (!d) throw apiError(404, 'DETECTION_NOT_FOUND', '目标不存在');
        if (patch.polygon) {
          if (patch.polygon.length !== 4) throw apiError(422, 'INVALID_POLYGON', 'polygon 必须是 4 个顶点');
          d.polygon = G.canonicalPolygon(patch.polygon).map(p => [+p[0].toFixed(2), +p[1].toFixed(2)]);
        }
        if (patch.review_state) {
          if (!['pending', 'confirmed', 'modified', 'deleted'].includes(patch.review_state))
            throw apiError(422, 'INVALID_STATE', '未知的复核状态');
          d.review_state = patch.review_state;
        }
        if (patch.note != null) d.note = String(patch.note).slice(0, 500);
        return clone(d);
      },

      async addDetection(taskId, body) {
        await net();
        const t = db.tasks[taskId];
        if (!t) throw apiError(404, 'TASK_NOT_FOUND', '任务不存在');
        requireProject(t.project_id);
        if (!body.polygon || body.polygon.length !== 4)
          throw apiError(422, 'INVALID_POLYGON', 'polygon 必须是 4 个顶点');
        const id = ++db.seq.detection;
        const d = {
          id, polygon: G.canonicalPolygon(body.polygon).map(p => [+p[0].toFixed(2), +p[1].toFixed(2)]),
          class_id: 1, class_name: 'ship', confidence: body.confidence == null ? null : body.confidence,
          source_tile_id: body.source_tile_id || 1,
          review_state: body.review_state || 'modified',
          origin: body.origin || 'manual', note: body.note || ''
        };
        db.drafts[taskId].push(d);
        return clone(d);
      },

      async resetDraft(taskId, versionId) {
        await net();
        const t = db.tasks[taskId];
        if (!t) throw apiError(404, 'TASK_NOT_FOUND', '任务不存在');
        requireProject(t.project_id);
        const v = db.versions[taskId].find(x => x.id === versionId);
        if (!v) throw apiError(404, 'VERSION_NOT_FOUND', '版本不存在');
        db.drafts[taskId] = clone(v.detections);
        return draftView(taskId);
      },

      /* --- versions --- */
      async listVersions(taskId) {
        await net();
        const t = db.tasks[taskId];
        if (!t) throw apiError(404, 'TASK_NOT_FOUND', '任务不存在');
        requireProject(t.project_id);
        return db.versions[taskId].map(v => versionView(v, false));
      },

      async getVersion(versionId, taskId) {
        await net();
        const t = db.tasks[taskId];
        if (!t) throw apiError(404, 'TASK_NOT_FOUND', '任务不存在');
        requireProject(t.project_id);
        const v = db.versions[taskId].find(x => x.id === versionId);
        if (!v) throw apiError(404, 'VERSION_NOT_FOUND', '版本不存在');
        return versionView(v, true);
      },

      async commitVersion(taskId, body) {
        await net();
        const t = db.tasks[taskId];
        if (!t) throw apiError(404, 'TASK_NOT_FOUND', '任务不存在');
        requireProject(t.project_id);
        const list = db.versions[taskId];
        const base = list[list.length - 1];
        const draft = db.drafts[taskId];
        const summary = diffSummary(base.detections, draft);
        if (!summary.added && !summary.removed && !summary.changed && !summary.confirmed && !summary.deleted)
          throw apiError(409, 'NOTHING_TO_COMMIT', '草稿与当前版本一致，没有需要保存的修改');
        const id = 'v' + list.length;
        const v = {
          id, task_id: taskId, label: body && body.label ? body.label : '人工修订',
          author: publicUser().name, created_at: new Date().toISOString(),
          readonly: false, summary, detections: clone(draft)
        };
        list.push(v);
        return versionView(v, true);
      },

      /* --- review queue --- */
      async getReviewQueue(taskId) {
        await net();
        const t = db.tasks[taskId];
        if (!t) throw apiError(404, 'TASK_NOT_FOUND', '任务不存在');
        requireProject(t.project_id);
        const list = db.drafts[taskId];
        const items = [];
        for (const d of list) {
          if (d.review_state === 'deleted') continue;
          const reasons = reasonsFor(d, list);
          if (!reasons.length) continue;
          items.push({
            detection_id: d.id,
            score: reasons.reduce((s, r) => s + (SCORE[r.code] || 0), 0),
            reasons
          });
        }
        items.sort((a, b) => b.score - a.score);

        // 空区域抽样：按 512 网格找出无任何目标的区块
        const step = 512, sampling = [];
        for (let y = 0; y < SCENE.image.height; y += step) {
          for (let x = 0; x < SCENE.image.width; x += step) {
            const hit = list.some(d => d.review_state !== 'deleted' &&
              G.centroid(d.polygon)[0] >= x && G.centroid(d.polygon)[0] < x + step &&
              G.centroid(d.polygon)[1] >= y && G.centroid(d.polygon)[1] < y + step);
            if (!hit) sampling.push({ x, y, width: step, height: step });
          }
        }
        return { items, sampling: sampling.slice(0, 6) };
      },

      /* --- regions / stats --- */
      async createRegion(taskId, body) {
        await net();
        const t = db.tasks[taskId];
        if (!t) throw apiError(404, 'TASK_NOT_FOUND', '任务不存在');
        requireProject(t.project_id);
        const poly = body.polygon;
        if (!Array.isArray(poly) || poly.length < 3)
          throw apiError(422, 'INVALID_REGION', '区域至少需要 3 个顶点');
        const inside = p => G.pointInPolygon(G.centroid(p), poly);
        const draft = db.drafts[taskId];
        const v0 = db.versions[taskId][0].detections;
        const model = v0.filter(d => inside(d.polygon)).length;
        const effective = draft.filter(d => d.review_state !== 'deleted' && inside(d.polygon)).length;
        const deleted = draft.filter(d => d.review_state === 'deleted' && inside(d.polygon)).length;
        const added = draft.filter(d => d.origin !== 'model' && d.review_state !== 'deleted' && inside(d.polygon)).length;
        return {
          id: 'rg-' + Date.now(), name: body.name || '未命名区域',
          polygon: poly, rule: 'centre_point_inside',
          counts: { model, effective, deleted, added, delta: effective - model },
          area_px2: Math.round(G.shoelace(poly))
        };
      },

      /* --- AI 候选 --- */
      async requestAiCandidate(imageId, body) {
        await net();
        const img = db.images[imageId];
        if (!img) throw apiError(404, 'IMAGE_NOT_FOUND', '影像不存在');
        requireProject(img.project_id);
        const id = 'cand-' + (++db.seq.candidate);
        const pt = body.point;
        const hit = SCENE.scene.missed.find(m => G.pointInPolygon(pt, m.polygon));
        if (!hit) {
          db.candidates[id] = { id, image_id: imageId, status: 'failed' };
          throw apiError(422, 'NO_CANDIDATE', '该位置未找到候选目标，请改用手动标注');
        }
        db.candidates[id] = {
          id, image_id: imageId, status: 'preview',
          polygon: clone(hit.polygon),
          source: 'prompt_segmentation',
          width: hit.width, length: hit.length
        };
        return clone(db.candidates[id]);
      },

      async acceptAiCandidate(candidateId) {
        await net();
        const c = db.candidates[candidateId];
        if (!c) throw apiError(404, 'CANDIDATE_NOT_FOUND', '候选不存在');
        if (c.status !== 'preview') throw apiError(409, 'CANDIDATE_CLOSED', '候选已失效');
        const img = db.images[c.image_id];
        requireProject(img.project_id);
        const taskId = Object.keys(db.tasks).find(k => db.tasks[k].image_id === img.id);
        if (!taskId) throw apiError(409, 'NO_TASK', '该影像还没有检测任务');
        const id = ++db.seq.detection;
        const d = {
          id, polygon: clone(c.polygon), class_id: 1, class_name: 'ship',
          confidence: null, source_tile_id: 1,
          review_state: 'confirmed', origin: 'ai', note: 'AI 辅助补框，人工确认'
        };
        db.drafts[taskId].push(d);
        c.status = 'accepted';
        return clone(d);
      },

      async cancelAiCandidate(candidateId) {
        await net();
        const c = db.candidates[candidateId];
        if (!c) throw apiError(404, 'CANDIDATE_NOT_FOUND', '候选不存在');
        c.status = 'cancelled';
        return { id: c.id, status: c.status };
      },

      /* --- export --- */
      async createExport(versionId, body, taskId) {
        await net();
        const t = db.tasks[taskId];
        if (!t) throw apiError(404, 'TASK_NOT_FOUND', '任务不存在');
        requireProject(t.project_id);
        const v = db.versions[taskId].find(x => x.id === versionId);
        if (!v) throw apiError(404, 'VERSION_NOT_FOUND', '版本不存在');
        const id = 'exp-' + (++db.seq.export);
        db.exports[id] = { id, version_id: versionId, status: 'running', files: [] };
        const formats = (body && body.formats) || ['csv', 'json', 'png'];
        setTimeout(() => {
          db.exports[id].files = buildExport(v, formats);
          db.exports[id].status = 'done';
        }, 350);
        return clone(db.exports[id]);
      },

      async getExport(exportId) {
        await net();
        const e = db.exports[exportId];
        if (!e) throw apiError(404, 'EXPORT_NOT_FOUND', '导出任务不存在');
        return clone(e);
      },

      /* --- 其他 --- */
      async health() {
        await net();
        return { status: 'ok', mode: 'mock', version: '1.0.0' };
      }
    };
  }

  global.createMockBackend = createMockBackend;
})(window);
