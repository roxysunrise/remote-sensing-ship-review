/* Annotation task screens reuse the existing project library and workbench. */
const annotationLabels = {
  available: '可领取', assigned: '已分派', claimed: '已领取', active: '标注中',
  paused: '已暂停', returned: '已退回', release_requested: '申请交还',
  submitted: '待审核', signed: '已签收'
};
const workflow = { items: [], current: null, task: null, navigation: 0, ai: null };
const lead = () => state.user?.role === '项目负责人';
const managed = () => workflow.task?.task_id === state.task?.id && Boolean(workflow.task);
const writable = () => !state.viewingVersion && (!managed() || workflow.task.status === 'active' && !lead());
const escapeWorkflow = text => String(text ?? '').replace(/[&<>"']/g, char => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]
));
const annotationButton = (label, handler, disabled = false, primary = false) => {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = primary ? 'btn primary sm' : 'btn sm';
  button.textContent = label;
  button.disabled = disabled;
  button.onclick = () => {
    button.disabled = true;
    action(handler).finally(() => { button.disabled = disabled; });
  };
  return button;
};

async function loadAnnotations() {
  const data = await API.listAnnotations(state.project.id);
  workflow.items = data.items;
  workflow.current = data.current_task_id;
}

function annotationPanel(title, description) {
  const host = $('#annotation-content');
  host.innerHTML = '';
  const heading = document.createElement('h2');
  heading.textContent = title;
  const intro = document.createElement('p');
  intro.className = 'vh-note muted';
  intro.textContent = description;
  const header = document.createElement('div');
  header.className = 'view-head';
  const main = document.createElement('div');
  main.className = 'vh-main';
  main.append(heading);
  header.append(main, intro);
  host.append(header);
  return host;
}

async function annotationPage(name) {
  switchView('annotation');
  const navigation = ++workflow.navigation;
  $$('#rail [data-annotation]').forEach(button => {
    button.classList.toggle('on', button.dataset.annotation === name);
    if (button.dataset.annotation === name) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  if (name === 'tools') return showProjectTools();
  annotationPanel('正在加载任务', '读取项目任务与当前进度。');
  try {
    await loadAnnotations();
    if (lead()) await loadLibrary();
  } catch (error) {
    if (state.view === 'annotation' && navigation === workflow.navigation) annotationPanel('任务加载失败', error.message)
      .append(annotationButton('重试', () => annotationPage(name)));
    return;
  }
  if (state.view !== 'annotation' || navigation !== workflow.navigation) return;
  const host = annotationPanel(
    { hall: '任务大厅', mine: '我的任务', manage: '项目任务', approval: '待审核' }[name],
    { hall: '选择一张影像领取，或领取系统推荐的下一单。',
      mine: '查看已分派、暂停及退回的任务。',
      manage: '检测完成后，将结果发布到大厅或定向分派。',
      approval: '检查标注者已提交的版本。' }[name]);
  function row(title, detail, buttons) {
    const section = document.createElement('div');
    section.className = 'annotation-row';
    const description = document.createElement('div');
    const label = document.createElement('strong');
    label.textContent = title;
    const meta = document.createElement('p');
    meta.textContent = detail;
    description.append(label, meta);
    const actions = document.createElement('div');
    actions.className = 'annotation-actions';
    actions.append(...buttons);
    section.append(description, actions);
    host.append(section);
  }

  const current = workflow.items.find(item => item.task_id === workflow.current);
  if (!lead() && name === 'hall') {
    const returned = workflow.items.filter(item => item.status === 'returned');
    if (returned.length) row('有退回任务待处理', returned.length + ' 单退回；当前单提交或暂停后可返修。',
      [annotationButton('查看我的任务', () => annotationPage('mine'))]);
    if (workflow.current) row('当前任务', current?.file_name || '另一个项目有任务在办',
      current ? [annotationButton(current.status === 'active' ? '继续标注' : '查看任务详情',
        () => enterAnnotation(current), false, true)] : []);
  }
  if (name === 'manage') {
    const known = new Set(workflow.items.map(item => item.task_id));
    host.append(annotationButton('上传影像', () => switchView('upload')));
    for (const task of state.tasks.filter(task => !known.has(task.id))) {
      const buttons = task.status === 'done'
        ? [annotationButton('发布到大厅', async () => {
          await API.changeAnnotation(task.id, 'publish');
          await annotationPage('manage');
        }), annotationButton('定向分派', () => assignAnnotation(task.id))]
        : [annotationButton('查看检测进度', () => openTask(task.id))];
      row(task.image?.file_name || task.image_id, TASK_STATUS_LABEL[task.status] || task.status, buttons);
    }
  }
  const available = workflow.items.filter(item => item.status === 'available');
  if (name === 'hall' && available.length)
    row('推荐任务', available[0].file_name,
      [annotationButton('推荐领取', () => claimAnnotation(available[0]), Boolean(workflow.current), true)]);
  const visible = name === 'hall' ? available : name === 'approval'
    ? workflow.items.filter(item => item.status === 'submitted') : name === 'mine'
      ? workflow.items.filter(item => item.status !== 'available').sort((a, b) =>
        Number(b.status === 'returned') - Number(a.status === 'returned')) : workflow.items;
  for (const item of visible) {
    const buttons = [annotationButton(item.status === 'active' && !lead() ? '继续标注' : '查看详情',
      () => enterAnnotation(item))];
    if (name === 'manage' && ['available', 'assigned', 'paused', 'returned'].includes(item.status))
      buttons.push(annotationButton('定向分派', () => assignAnnotation(item.task_id)));
    if (name === 'manage' && item.status === 'release_requested')
      buttons.push(annotationButton('同意交还', () => resolveRelease(item)));
    row(item.file_name, [annotationLabels[item.status], item.final_count + ' 个模型框', item.reason]
      .filter(Boolean).join(' / '), buttons);
  }
  if (!visible.length && name !== 'manage') {
    const empty = document.createElement('p');
    empty.textContent = '当前没有符合条件的任务。';
    host.append(empty);
  }
}

function aiAvailability() {
  const status = workflow.ai;
  if (!status) return '正在检查本机 SAM 2';
  if (!status.available) return 'SAM 2 不可用，可继续手调';
  if (status.sam2 === 'loading') return 'SAM 2 加载中';
  return status.sam2 === 'ready' ? 'SAM 2 已就绪' : 'SAM 2 首次使用时加载';
}

async function refreshAiAvailability() {
  try { workflow.ai = await API.annotationAiStatus(state.project.id); }
  catch (_) { workflow.ai = { available: false }; }
  if (state.view === 'workbench') updateDirty();
}

async function showProjectTools() {
  const host = annotationPanel('项目工具', '管理本机模型连接，或打开独立的图片和视频检测。');
  const status = document.createElement('p');
  status.setAttribute('role','status');
  const link = document.createElement('a');
  link.href = 'detect.html';
  link.className = 'btn';
  link.textContent = '打开独立智能检测';
  host.append(status, annotationButton('重测 SAM 2', async () => {
    status.textContent = '正在加载本机模型…';
    try { workflow.ai = await API.probeAnnotationAi(state.project.id); }
    catch (error) { status.textContent = error.message; return; }
    update();
  }), link);
  function update() {
    status.textContent = aiAvailability() + ' / ' + (workflow.ai?.vlm === 'local_configured'
      ? '本机 VLM 已配置，仅判断是否为船' : '本机 VLM 未配置，候选由人工判断');
  }
  await refreshAiAvailability();
  update();
}

function workflowDialog(title, fields, submit) {
  const dialog = document.createElement('dialog');
  dialog.className = 'annotation-dialog';
  const form = document.createElement('form');
  form.innerHTML = '<h2>' + escapeWorkflow(title) + '</h2>' + fields +
    '<p role="alert" class="annotation-error"></p><div class="annotation-actions">' +
    '<button type="button" class="btn">取消</button><button class="btn primary">确认</button></div>';
  dialog.append(form);
  document.body.append(dialog);
  dialog.addEventListener('close', () => dialog.remove(), { once: true });
  form.querySelector('[type=button]').onclick = () => dialog.close();
  form.onsubmit = async event => {
    event.preventDefault();
    const confirm = form.querySelector('.primary');
    confirm.disabled = true;
    try {
      await submit(new FormData(form));
      dialog.close();
    } catch (error) {
      form.querySelector('[role=alert]').textContent = error.message;
      confirm.disabled = false;
    }
  };
  dialog.showModal();
}

function assignAnnotation(id) {
  const members = state.project.members.filter(member => member.role === '项目分析员');
  if (!members.length) return toast('项目中暂无可分派的标注者', 'err');
  workflowDialog('定向分派', '<label>标注者<select name="assignee" required>' +
    members.map(member => '<option value="' + escapeWorkflow(member.id) + '">' +
      escapeWorkflow(member.name) + '</option>').join('') + '</select></label>', async data => {
    await API.changeAnnotation(id, 'assign', { assignee: data.get('assignee') });
    await annotationPage('manage');
  });
}

function resolveRelease(item) {
  workflowDialog('同意交还', '<p>' + escapeWorkflow(item.reason) + '</p><p>保留草稿并重新开放领取。</p>', async () => {
    await API.changeAnnotation(item.task_id, 'resolve_release');
    await annotationPage('manage');
  });
}

async function claimAnnotation(item) {
  try {
    await API.changeAnnotation(item.task_id, 'claim');
  } catch (error) {
    await annotationPage('hall');
    throw error;
  }
  await loadAnnotations();
  await enterAnnotation(workflow.items.find(row => row.task_id === item.task_id));
}

async function enterAnnotation(item) {
  const busy = workflow.current && workflow.current !== item.task_id;
  if ((!lead() && item.status === 'active') ||
      (lead() && ['submitted','signed'].includes(item.status))) return openAnnotation(item);
  if (!lead() && item.status === 'returned' && item.target_id != null && !busy)
    return startAnnotation(item);
  switchView('annotation');
  workflow.navigation++;
  const host = annotationPanel(item.file_name, annotationLabels[item.status]);
  const summary = document.createElement('p');
  summary.textContent = item.width + ' × ' + item.height + ' px / ' + item.final_count +
    ' 个模型框 / ' + item.low_pending + ' 个低置信度待处理。';
  host.append(summary);
  if (item.final_count === 0) host.append('模型未检出船舶，请检查整张影像是否有漏检。');
  const instructions = document.createElement('p');
  instructions.textContent = '检查整张影像。补画漏检、调整错位、删除误检。低置信度框逐个处理，高置信度框可在整图检查后批量确认。';
  host.append(instructions);
  if (item.reason) {
    const reason = document.createElement('p');
    reason.textContent = '处理意见：' + item.reason;
    host.append(reason);
  }
  if (busy && !lead()) {
    const notice = document.createElement('p');
    notice.textContent = '请先提交或暂停当前任务。';
    host.append(notice);
  }
  if (!lead() && item.status === 'available')
    host.append(annotationButton('领取任务', () => claimAnnotation(item), busy, true));
  if (!lead() && ['assigned','claimed','returned','paused'].includes(item.status))
    host.append(annotationButton(item.status === 'paused' ? '继续标注' : '开始标注',
      () => startAnnotation(item), busy, true));
  if (!lead() && item.status === 'claimed')
    host.append(annotationButton('释放未开始任务', async () => {
      await API.changeAnnotation(item.task_id, 'release');
      await annotationPage('hall');
    }));
  if (!lead() && item.status === 'paused') host.append(annotationButton('申请交还', () => requestRelease(item)));
  host.append(annotationButton('返回任务列表', () => annotationPage(lead() ? 'manage' : item.status === 'available' ? 'hall' : 'mine')));
}

async function startAnnotation(item) {
  const changed = await API.changeAnnotation(item.task_id, 'start');
  await openAnnotation({ ...item, ...changed });
}

async function openAnnotation(item) {
  await loadTaskContext(item.task_id, item);
  setAnnotationFilter(writable() ? 'low' : 'all');
  if (!writable() && item.version_id)
    state.viewingVersion = await API.getVersion(item.version_id, item.task_id);
  workflow.ai = null;
  switchView('workbench');
  refreshAiAvailability();
  if (item.target_id != null) requestAnimationFrame(() => centerOn(item.target_id));
}

function requestRelease(item) {
  workflowDialog('申请交还', '<label>原因<textarea name="reason" required maxlength="500"></textarea></label>', async data => {
    await API.changeAnnotation(item.task_id, 'request_release', { reason: data.get('reason') });
    await annotationPage('mine');
  });
}

function renderAnnotationToolbar() {
  const host = $('#annotation-workbench');
  host.replaceChildren();
  $('#btn-save').hidden = managed();
  for (const id of ['btn-confirm','btn-delete','btn-undo','btn-redo'])
    $('#' + id).disabled = !writable();
  $$('#tool-group button[data-tool]').forEach(button => {
    button.disabled = !writable() && button.dataset.tool !== 'select' ||
      managed() && button.dataset.tool === 'ai' && (!workflow.ai?.available || workflow.ai.sam2 === 'loading' ||
      ['requesting','preview'].includes(state.ai.status));
  });
  const refine = $('#ai-refine');
  refine.hidden = !managed();
  refine.disabled = !writable() || !selected() || !workflow.ai?.available ||
    workflow.ai.sam2 === 'loading' || ['requesting','preview'].includes(state.ai.status);
  $('#annotation-ai-status').textContent = managed() && writable()
    ? state.ai.status === 'requesting' ? '正在运行本机 SAM 2…' : aiAvailability() : '';
  $('#btn-exit-readonly').hidden = managed() && !writable();
  if (!managed()) return;
  const item = workflow.task;
  const title = document.createElement('strong');
  title.textContent = (state.image?.file_name || item.file_name) + ' / ' + annotationLabels[item.status];
  host.append(title, annotationButton('返回任务列表', () => annotationPage(lead() ? 'manage' : 'mine')));
  const pending = state.draft.filter(box => box.review_state === 'pending');
  if (item.reason) {
    const reason = document.createElement('span');
    reason.textContent = '处理意见：' + item.reason;
    host.append(reason);
  }
  if (writable()) {
    const failed = API.hasDraftFailure(state.task.id);
    const feedback = document.createElement('span');
    feedback.setAttribute('role', 'status');
    feedback.textContent = failed ? '保存失败，请核对后重新打开任务' : '待处理 ' + pending.length + ' 框';
    host.append(feedback,
      annotationButton('检查待处理框', () => setAnnotationFilter('pending')),
      annotationButton('批量确认高置信度', () => workflowDialog('确认剩余高置信度框',
        '<p>确认你已经检查整张影像。低置信度框仍需逐个处理。</p>', async () => {
          const draft = await API.confirmHigh(item.task_id);
          state.draft = draft.detections;
          syncServerDraft();
          refreshWorkbench();
        }), !pending.some(box => box.confidence >= .45)),
      annotationButton('保存并暂停', async () => {
        await API.changeAnnotation(item.task_id, 'pause');
        await annotationPage('mine');
      }, failed),
      annotationButton('申请交还', () => requestRelease(item), failed),
      annotationButton('提交审核', async () => {
        await API.changeAnnotation(item.task_id, 'submit');
        await annotationPage('mine');
      }, pending.length > 0 || failed, true));
  } else if (lead() && item.status === 'submitted') {
    host.append(annotationButton('对照模型原始框', async () => {
      const id = state.viewingVersion?.id === 'v0' ? item.version_id : 'v0';
      state.viewingVersion = await API.getVersion(id, item.task_id);
      refreshWorkbench();
    }), annotationButton('退回', () => {
      const target = selected()?.id;
      workflowDialog('退回标注', '<label>原因<textarea name="reason" required maxlength="500"></textarea></label>' +
        (target != null ? '<label><input type="checkbox" name="target">关联目标 #' + target + '</label>' : ''),
      async data => {
        await API.changeAnnotation(item.task_id, 'return', {
          reason: data.get('reason'), target_id: data.has('target') ? target : undefined
        });
        await annotationPage('approval');
      });
    }), annotationButton('签收', async () => {
      await API.changeAnnotation(item.task_id, 'accept');
      await annotationPage('approval');
    }, false, true));
  }
  for (const [label, view] of [['区域统计','stats'], ['历史','history'], ['导出','export']])
    host.append(annotationButton(label, () => switchView(view), view === 'export' && item.status !== 'signed'));
}

function setAnnotationFilter(filter) {
  state.listFilter = filter;
  $$('#list-filters button').forEach(button => button.classList.toggle('on', button.dataset.f === filter));
  renderList();
}
