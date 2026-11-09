'use strict';

const $ = id => document.getElementById(id);
const labels = { unassigned:'待分派', editing:'待复核', submitted:'待签收', accepted:'已签收', failed:'检测失败' };
const people = { lin:'林遥', chen:'陈帆' };
const initial = ['unassigned','editing','editing','submitted','accepted','failed'].map((status, index) => ({
  id:index + 1, name:'北港_' + String(index + 1).padStart(2,'0') + '.tif', status,
  owner:index > 0 && index < 5 ? 'lin' : '', reason:index === 2 ? '请复查东侧泊位重叠的两个目标。' : '',
  targets:[{id:1,x:350,y:162,angle:-15},{id:2,x:486,y:282,angle:13}],
  history:index === 4 ? ['周宁已签收 · v1'] : []
}));
const storageKey = 'ship-pilot-prototype-v1';
let images;
try { images = JSON.parse(localStorage.getItem(storageKey)) || structuredClone(initial); }
catch { images = structuredClone(initial); }
let role = 'review', page = 'hall', active = null, selected = null, zoom = 1, pan = [0,0], undo = [];
const screen = $('screen');
const dialog = $('dialog');
const html = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const button = (label, action, extra = '') => '<button type="button" data-action="' + action + '" ' + extra + '>' + label + '</button>';
const running = () => images.find(i => i.owner === 'lin' && i.running);
const low = target => target.id % 2 === 0;
const pending = image => image.targets.filter(target => !target.confirmed);
const editable = () => role === 'review' && active?.owner === 'lin' && active.status === 'editing' && active.running;

function notify(message) { $('feedback').textContent = message; }
function save(message) {
  try { localStorage.setItem(storageKey, JSON.stringify(images)); }
  catch { message += '；浏览器存储不可用，刷新会丢失模拟进度。'; }
  notify(message);
  render();
}
function navigate(next) {
  page = next;
  active = null;
  render();
  screen.querySelector('h1')?.focus();
}
function openImage(image) {
  active = image;
  selected = image.targets[0]?.id ?? null;
  zoom = 1; pan = [0,0]; undo = [];
  page = 'work';
  render();
  screen.querySelector('h1')?.focus();
}
function queue() {
  return images.filter(image => image.owner === 'lin' && image.status === 'editing')
    .sort((a,b) => Number(Boolean(b.reason)) - Number(Boolean(a.reason)));
}
function row(image, action, title) {
  return '<div class="row"><div><strong>' + html(image.name) + '</strong><small>' +
    (image.reason ? '<span class="warning">退回 · ' + html(image.reason) + '</span>' : image.history.length ? html(image.history.at(-1)) : '北港巡查 01') +
    '</small></div><div><span class="tag">' + labels[image.status] + '</span></div><div>' +
    (people[image.owner] || '未分派') + '</div><div>' +
    button(title, action + ':' + image.id) +
    (page === 'batch' && ['unassigned','editing'].includes(image.status) && image.owner ? button('重新分派','assign:' + image.id) : '') + '</div></div>';
}
function showDetails(image) {
  active = image;
  page = 'detail';
  render();
}
function renderDetails() {
  const image = active;
  const canStart = !running() || running() === image;
  screen.innerHTML = button('返回列表','back') + '<h1>' + html(image.name) +
    '</h1><section class="details"><h2>任务说明</h2><p>范围：北港泊位整张影像。模型预标注 ' + image.targets.length +
    ' 个，低置信度待确认 ' + pending(image).filter(low).length +
    ' 个。</p><p>补画漏检、调整错位框、删除误检。逐个处理低置信度框，检查整张影像后批量确认高置信度框。</p>' +
    (image.reason ? '<p class="warning">退回原因 · ' + html(image.reason) + '</p>' : '') +
    (image.releaseRequest ? '<p>交还申请待主管处理。</p>' : '') + '</section><div class="tools">' +
    (image.status === 'unassigned' ? button('领取任务','claim:' + image.id,running() ? 'disabled' : 'class="primary"') : '') +
    (image.owner === 'lin' && image.status === 'editing' && !image.releaseRequest ?
      button(image.started ? '继续标注' : '开始标注','start',canStart ? 'class="primary"' : 'disabled') +
      (!image.started ? button('释放任务','release') : '') : '') + '</div>' +
    (!canStart ? '<p class="muted">请先提交或暂停当前任务。</p>' : '');
}
function renderHall() {
  const available = images.filter(i => i.status === 'unassigned');
  const current = running();
  const returned = queue().filter(i => i.reason);
  screen.innerHTML = '<h1 tabindex="-1">任务大厅</h1>' +
    (returned.length ? '<p class="warning">' + returned.length + ' 单退回待修改。' + button('查看我的任务','nav:mine') + '</p>' : '') +
    (current ? '<section class="details"><h2>当前任务</h2><p>' + html(current.name) + '</p>' +
      button(current.started ? '继续标注' : '查看任务说明','open:' + current.id,'class="primary"') + '</section>' : '') +
    '<section class="details"><h2>推荐任务</h2>' + (available.length ? '<p>' + html(available[0].name) + '</p>' +
      button('推荐领取','claim:' + available[0].id,current ? 'disabled' : 'class="primary"') : '<p>暂无可领取任务。</p>') +
    (current ? '<p>完成或暂停当前任务后可继续领取。</p>' : '') +
    '</section><h2>可领取任务</h2><div class="rows">' +
    available.map(i => row(i,'details','查看任务')).join('') + '</div>';
}
function renderList() {
  if (page === 'hall') { renderHall(); return; }
  const batch = page === 'batch';
  const items = batch ? images : page === 'sign' ? images.filter(image => image.status === 'submitted') : queue();
  const heading = batch ? '批次管理' : page === 'sign' ? '待签收' : '我的待办';
  screen.innerHTML = '<div class="bar"><div><h1 tabindex="-1">' + heading + '</h1><p class="muted">' +
    (batch ? '北港巡查 01 · 按张分派与交付' : page === 'sign' ? '检查已提交的影像成果' : '优先处理退回件，再处理新分派的影像') + '</p></div>' +
    (batch ? '<div class="tools">' + button('批量上传','upload') + button('查看交付范围','delivery','class="primary"') + '</div>' : '') + '</div>' +
    (batch ? '<div class="counts">' + Object.entries(labels).map(([key,label]) =>
      '<div><strong>' + images.filter(image => image.status === key).length + '</strong>' + label + '</div>').join('') + '</div>' : '') +
    (items.length ? '<div class="rows">' + items.map(image => {
      if (batch && image.status === 'unassigned') return row(image,'assign','分派');
      if (batch && image.status === 'failed') return row(image,'retry','重试检测');
      if (batch && image.releaseRequest) return row(image,'resolve','处理交还');
      return row(image,'open',page === 'sign' ? '检查成果' :
        image.releaseRequest ? '查看交还申请' : image.started && image.running ? '继续标注' : '查看任务');
    }).join('') + '</div>' : '<p class="details">当前没有待处理影像。</p>');
}
function mapSvg(image) {
  return '<svg id="map" viewBox="0 0 800 500" aria-label="模拟港区影像及船舶旋转框" role="img"><defs><pattern id="grid" width="50" height="50" patternUnits="userSpaceOnUse"><path d="M 50 0 H 0 V 50" fill="none" stroke="#b7d9dd" opacity=".2"/></pattern></defs><rect width="800" height="500" fill="#203e4b"/><g transform="translate(' +
    pan.join(' ') + ') translate(400 250) scale(' + zoom + ') translate(-400 -250)"><path fill="#536362" d="M0 0H800V88H520L460 122H180V500H0Z"/><path fill="#8b9281" d="M35 125H145V465H35Z M220 60H440V94H220Z"/><path fill="#798777" d="M180 245H370V275H180Z M180 365H405V394H180Z"/><rect width="800" height="500" fill="url(#grid)"/>' +
    image.targets.map(target => '<g data-target="' + target.id + '" transform="translate(' + target.x + ' ' + target.y + ') rotate(' + target.angle + ')"><path d="M-23 -7H17L24 0L17 7H-23Z" fill="#dce5da"/><rect x="-29" y="-12" width="58" height="24" fill="transparent" stroke="' +
      (target.id === selected ? '#ffd26d' : '#77eadb') + '" stroke-width="3"/></g>').join('') +
    '</g><path d="M752 55V20L745 31M752 20L759 31" stroke="white" fill="none"/><text x="747" y="76" fill="white">N</text></svg>';
}
function renderWork() {
  const image = active;
  const target = image.targets.find(item => item.id === selected);
  const tools = [['放大','zoom-in'],['缩小','zoom-out'],['适配','fit'],['向左平移','pan-left'],['向右平移','pan-right']];
  if (editable()) tools.push(['加框','add'],['改框','edit'],['撤销','undo']);
  screen.innerHTML = '<div class="bar"><div>' + button('返回列表','back') + '<h1 tabindex="-1">' + html(image.name) +
    '</h1><p class="muted">北港巡查 01 / ' + labels[image.status] + ' / 本机保存模拟进度</p></div><div class="tools">' +
    (editable() ? button('保存并暂停','pause') + button('申请交还','request-release') +
      button('提交审核','submit',pending(image).length ? 'disabled' : 'class="primary"') : role === 'lead' && image.status === 'submitted' ?
      button('退回','return') + button('签收','accept','class="primary"') : '') + '</div></div>' +
    (image.reason ? '<p class="warning">退回原因 · ' + html(image.reason) + '</p>' : '') +
    '<div class="workspace"><aside><details open><summary>疑难目标（' + image.targets.length + '）</summary>' +
    [...image.targets].sort((a,b) => Number(!b.confirmed && low(b)) - Number(!a.confirmed && low(a))).map(item =>
      button('船舶 ' + item.id + (item.confirmed ? ' · 已确认' : low(item) ? ' · 低置信度' : ' · 高置信度'),'select:' + item.id,'class="target" aria-pressed="' + (selected === item.id) + '"')).join('') +
    '</details>' + (editable() ? '<p>待确认 ' + pending(image).length + ' 个</p>' +
      button('批量确认高置信度','confirm-high',pending(image).some(item => !low(item)) ? '' : 'disabled') : '') +
    '</aside><div class="map"><div class="tools">' +
    tools.map(([label,action]) => button(label,action,action === 'undo' && !undo.length || action === 'edit' && !target ? 'disabled' : '')).join('') +
    '<span>' + Math.round(zoom * 100) + '%</span></div>' + mapSvg(image) +
    '</div><aside><h2>目标属性</h2>' + (target ? '<p>船舶 ' + target.id + '</p><p>像素中心 ' + target.x + ', ' + target.y +
    '</p><p>角度 ' + target.angle + '°</p>' : '<p class="muted">请选择一个目标。</p>') +
    (editable() && target ? button('确认此框','confirm',target.confirmed ? 'disabled' : '') + button('删除此框','delete') : '') +
    '</aside></div><details class="details"><summary>统计与修订历史</summary><p>当前船舶数量 ' + image.targets.length +
    '</p>' + image.history.map(entry => '<p>' + html(entry) + '</p>').join('') + '</details>';
}
function render() {
  const entries = role === 'lead' ? [['批次管理','batch'],['待签收','sign']] : [['任务大厅','hall'],['我的任务','mine']];
  $('nav').innerHTML = entries.map(([label,key]) => button(label,'nav:' + key,page === key ? 'aria-current="page"' : '')).join('');
  if (page === 'work') renderWork(); else if (page === 'detail') renderDetails(); else renderList();
}
function promptAction(title, content, callback) {
  $('dialog-title').textContent = title;
  $('dialog-content').innerHTML = content;
  $('dialog-form').onsubmit = event => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    if (callback(data) !== false) dialog.close();
  };
  dialog.showModal();
}
function moveTo(next, entry, message, nextPage) {
  active.status = next;
  active.history.push(entry);
  if (nextPage) navigate(nextPage);
  save(message);
}
function onAction(action, id) {
  const image = images.find(item => item.id === Number(id));
  switch (action) {
    case 'nav': navigate(id); break;
    case 'back': navigate(role === 'review' ? 'mine' : active.status === 'submitted' ? 'sign' : 'batch'); break;
    case 'details': if (image) showDetails(image); break;
    case 'open':
      if (!image) break;
      if (role === 'lead' || (image.running && image.started)) openImage(image);
      else if (image.issueTarget && !running() && !image.releaseRequest) {
        image.started = true; image.running = true;
        openImage(image); selected = image.issueTarget; save('已定位退回标注框');
      } else showDetails(image);
      break;
    case 'claim':
      if (role === 'review' && !running() && image?.status === 'unassigned') {
        image.owner = 'lin'; image.status = 'editing'; image.running = true;
        image.history.push('林遥领取任务'); showDetails(image); save('领取成功，请先阅读任务说明');
      }
      break;
    case 'start':
      if (role === 'review' && active?.owner === 'lin' && active.status === 'editing' &&
          (!running() || running() === active) && !active.releaseRequest) {
        active.started = true; active.running = true;
        openImage(active); if (active.issueTarget) selected = active.issueTarget;
        save('已开始标注');
      }
      break;
    case 'release':
      if (role === 'review' && active?.owner === 'lin' && !active.started && active.status === 'editing') {
        active.owner = ''; active.status = 'unassigned'; active.running = false;
        active.history.push('林遥释放未开始任务'); navigate('hall'); save('任务已回到大厅');
      }
      break;
    case 'pause':
      if (editable()) { active.running = false; navigate('mine'); save('已保存并暂停'); }
      break;
    case 'request-release':
      if (editable()) promptAction('申请交还任务','<label>原因<textarea name="reason" required maxlength="200"></textarea></label>',data => {
        const reason = data.get('reason').trim();
        if (!reason) return false;
        active.releaseRequest = reason; active.running = false; navigate('mine'); save('交还申请已提交，等待主管处理');
      });
      break;
    case 'resolve':
      if (role === 'lead' && image?.releaseRequest) promptAction('处理交还','<p>' + html(image.releaseRequest) +
        '</p><p>确认后取消当前分派，保留已保存标注。</p>',() => {
        image.releaseRequest = ''; image.owner = ''; image.status = 'unassigned'; save('已交回任务大厅');
      });
      break;
    case 'confirm':
    case 'confirm-high':
    case 'delete':
      if (editable()) {
        undo.push(structuredClone(active.targets));
        if (action === 'delete') {
          active.targets = active.targets.filter(item => item.id !== selected);
          selected = active.targets[0]?.id;
        } else for (const item of active.targets) {
          if (action === 'confirm' ? item.id === selected : !low(item)) item.confirmed = true;
        }
        save('复核稿已保存到此浏览器');
      }
      break;
    case 'assign':
      if (role !== 'lead' || !image || !['unassigned','editing'].includes(image.status)) break;
      promptAction('分派 ' + image.name,'<label>复核员<select name="owner"><option value="lin">林遥</option><option value="chen">陈帆</option></select></label>',data => {
        image.running = false; image.owner = data.get('owner'); image.status = 'editing'; image.history.push('周宁分派给 ' + people[image.owner]); save('分派已保存');
      });
      break;
    case 'retry':
      if (role === 'lead' && image?.status === 'failed') { image.status = 'unassigned'; image.history.push('模拟重试成功'); save('模拟检测完成，可分派'); }
      break;
    case 'upload':
      promptAction('批量上传影像','<p>选择用于演练的 TIFF 文件名。原型只登记名称，文件留在你的设备上。</p><label>模拟影像<input type="file" name="files" accept=".tif,.tiff" multiple required></label><p id="upload-error" role="alert"></p>',data => {
        const files = data.getAll('files').filter(file => file.name);
        if (!files.length || images.length + files.length > 20 || files.some(file => !/\.tiff?$/i.test(file.name))) {
          $('upload-error').textContent = '请上传 TIFF 文件，当前批次合计最多 20 张。';
          return false;
        }
        for (const file of files) images.push({id:Math.max(...images.map(item => item.id)) + 1,name:file.name,status:'unassigned',owner:'',reason:'',targets:[],history:['模拟检测完成']});
        save('已登记 ' + files.length + ' 张模拟影像');
      });
      break;
    case 'delivery': {
      const accepted = images.filter(item => item.status === 'accepted');
      const lists = '<p>纳入 ' + accepted.length + ' 张</p><ul>' + accepted.map(item => '<li>' + html(item.name) + '</li>').join('') +
        '</ul><p>未纳入 ' + (images.length - accepted.length) + ' 张</p><ul>' + images.filter(item => item.status !== 'accepted').map(item =>
          '<li>' + html(item.name) + ' · ' + labels[item.status] + '</li>').join('') + '</ul><p>此处演练交付范围，确认后记录模拟交付。</p>';
      promptAction('确认交付范围',lists,() => notify(accepted.length ? '已确认 ' + accepted.length + ' 张的模拟交付范围' : '没有可交付的已签收影像'));
      break;
    }
    case 'submit':
      if (editable() && !pending(active).length) {
        active.running = false; active.issueTarget = null; active.reason = '';
        moveTo('submitted','林遥提交签收','已提交签收','mine');
      }
      break;
    case 'accept':
      if (role === 'lead' && active?.status === 'submitted') moveTo('accepted','周宁已签收 · v1','已签收，可纳入交付','sign');
      break;
    case 'return':
      if (role === 'lead' && active?.status === 'submitted') promptAction('退回复核','<label>退回原因<textarea name="reason" required maxlength="200"></textarea></label><label><input type="checkbox" name="target">关联当前选中的船舶框</label><p id="reason-error" role="alert"></p>',data => {
        const reason = data.get('reason').trim();
        if (!reason) { $('reason-error').textContent = '请输入退回原因。'; return false; }
        active.issueTarget = data.has('target') ? selected : null;
        const target = active.targets.find(item => item.id === active.issueTarget);
        if (target) target.confirmed = false;
        active.reason = reason; moveTo('editing','周宁退回 · ' + reason,'已退回给原复核员','sign');
      });
      break;
    case 'zoom-in': zoom = Math.min(3,zoom * 1.25); render(); break;
    case 'zoom-out': zoom = Math.max(.5,zoom / 1.25); render(); break;
    case 'fit': zoom = 1; pan = [0,0]; render(); break;
    case 'pan-left': pan[0] -= 45; render(); break;
    case 'pan-right': pan[0] += 45; render(); break;
    case 'select': selected = Number(id); render(); screen.querySelector('[data-action="select:' + id + '"]')?.focus(); break;
    case 'add':
    case 'edit': {
      if (!editable()) break;
      const target = action === 'edit' ? active.targets.find(item => item.id === selected) : null;
      if (action === 'edit' && !target) break;
      const box = target || {id:Math.max(0,...active.targets.map(item => item.id)) + 1,x:400,y:250,angle:0};
      promptAction(target ? '调整旋转框' : '添加船舶框',
        [['x','中心 X',0,800],['y','中心 Y',0,500],['angle','旋转角度',-180,180]].map(([key,label,min,max]) =>
          '<label>' + label + '<input type="number" name="' + key + '" min="' + min + '" max="' + max + '" value="' + box[key] + '" required></label>').join(''),data => {
          undo.push(structuredClone(active.targets));
          for (const key of ['x','y','angle']) box[key] = Number(data.get(key));
          box.confirmed = true;
          if (!target) active.targets.push(box);
          selected = box.id;
          save('复核稿已保存到此浏览器');
        });
      break;
    }
    case 'undo':
      if (editable() && undo.length) { active.targets = undo.pop(); selected = active.targets[0]?.id; save('已撤销上次修改'); }
      break;
  }
}
document.addEventListener('click',event => {
  const trigger = event.target.closest('[data-action]');
  if (trigger) {
    const [action,id] = trigger.dataset.action.split(':');
    onAction(action,id);
  } else {
    const target = event.target.closest('[data-target]');
    if (target) onAction('select',target.dataset.target);
  }
});
$('role').addEventListener('change',event => {
  role = event.target.value;
  navigate(role === 'lead' ? 'batch' : 'hall');
});
$('role').value = role;
$('cancel').addEventListener('click',() => dialog.close());
render();
