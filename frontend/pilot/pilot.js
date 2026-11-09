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
let role = 'lead', page = 'batch', active = null, selected = null, zoom = 1, pan = [0,0], undo = [];
const screen = $('screen');
const dialog = $('dialog');
const html = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const button = (label, action, extra = '') => '<button type="button" data-action="' + action + '" ' + extra + '>' + label + '</button>';
const editable = () => role === 'review' && active?.owner === 'lin' && active.status === 'editing';

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
function renderList() {
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
      return row(image,'open',page === 'sign' ? '检查成果' : '打开影像');
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
    (editable() ? button('提交签收','submit','class="primary"') : role === 'lead' && image.status === 'submitted' ?
      button('退回','return') + button('签收','accept','class="primary"') : '') + '</div></div>' +
    (image.reason ? '<p class="warning">退回原因 · ' + html(image.reason) + '</p>' : '') +
    '<div class="workspace"><aside><details open><summary>疑难目标（' + image.targets.length + '）</summary>' +
    image.targets.map(item => button('船舶 ' + item.id,'select:' + item.id,'class="target" aria-pressed="' + (selected === item.id) + '"')).join('') +
    '</details></aside><div class="map"><div class="tools">' +
    tools.map(([label,action]) => button(label,action,action === 'undo' && !undo.length || action === 'edit' && !target ? 'disabled' : '')).join('') +
    '<span>' + Math.round(zoom * 100) + '%</span></div>' + mapSvg(image) +
    '</div><aside><h2>目标属性</h2>' + (target ? '<p>船舶 ' + target.id + '</p><p>像素中心 ' + target.x + ', ' + target.y +
    '</p><p>角度 ' + target.angle + '°</p>' : '<p class="muted">请选择一个目标。</p>') +
    '</aside></div><details class="details"><summary>统计与修订历史</summary><p>当前船舶数量 ' + image.targets.length +
    '</p>' + image.history.map(entry => '<p>' + html(entry) + '</p>').join('') + '</details>';
}
function render() {
  const entries = role === 'lead' ? [['批次管理','batch'],['待签收','sign']] : [['我的待办','mine']];
  $('nav').innerHTML = entries.map(([label,key]) => button(label,'nav:' + key,page === key ? 'aria-current="page"' : '')).join('');
  if (page === 'work') renderWork(); else renderList();
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
    case 'open': if (image) openImage(image); break;
    case 'assign':
      if (role !== 'lead' || !image || !['unassigned','editing'].includes(image.status)) break;
      promptAction('分派 ' + image.name,'<label>复核员<select name="owner"><option value="lin">林遥</option><option value="chen">陈帆</option></select></label>',data => {
        image.owner = data.get('owner'); image.status = 'editing'; image.history.push('周宁分派给 ' + people[image.owner]); save('分派已保存');
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
      if (editable()) {
        active.reason = '';
        moveTo('submitted','林遥提交签收','已提交签收','mine');
      }
      break;
    case 'accept':
      if (role === 'lead' && active?.status === 'submitted') moveTo('accepted','周宁已签收 · v1','已签收，可纳入交付','sign');
      break;
    case 'return':
      if (role === 'lead' && active?.status === 'submitted') promptAction('退回复核','<label>退回原因<textarea name="reason" required maxlength="200"></textarea></label><p id="reason-error" role="alert"></p>',data => {
        const reason = data.get('reason').trim();
        if (!reason) { $('reason-error').textContent = '请输入退回原因。'; return false; }
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
  navigate(role === 'lead' ? 'batch' : 'mine');
});
$('cancel').addEventListener('click',() => dialog.close());
render();
