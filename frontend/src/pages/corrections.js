import {heading, panel} from '../components/common/heading.js';
import {records} from '../components/common/records.js';
import {stateBadge} from '../components/common/status.js';
import {topic} from '../constants/project.js';
import {correctionKindLabels, correctionStatusLabels} from '../constants/workflow.js';
import {date, esc} from '../utils/dom.js';
import {submit} from '../hooks/submit.js';

function origins(version) {
  if (version.source==='baseline') return '初始导出';
  return version.source_ref || (version.source==='import' ? '导入快照' : '更正');
}

function rowsFor(corrections, kind) {
  return corrections.filter(row=>row.kind===kind).map(row=>[
    // One fragment is one message; handover fragments share their reference.
    row.handover_ref ? esc(row.handover_ref) : esc(correctionKindLabels[row.kind] || row.kind),
    row.revoke.length ? row.revoke.map(x=>esc(`${x.operator_id} / ${x.zone} / ${x.utility}`)).join('<br>') : '—',
    row.reassign.length ? row.reassign.map(x=>esc(`${x.operator_id} / ${x.zone} / ${x.utility} 至 ${x.valid_until}`)).join('<br>') : '—',
    stateBadge(row.status==='published'?'complete':'queued', correctionStatusLabels[row.status] || row.status),
    row.version ? `第 ${esc(row.version.version_no)} 版` : '—',
    esc(row.actor),
    esc(date(row.created_at)),
  ]);
}

function correctionTable(corrections, empty) {
  const rows = rowsFor(corrections,'urgent_revoke').concat(rowsFor(corrections,'handover'));
  return records(['交接','撤销','续派','状态','发布版本','登记人','登记时间'], rows, empty);
}

function versionTable(state) {
  const rows = (state.versions||[]).map(row=>[
    `第 ${esc(row.version_no)} 版`,
    row.id===state.currentVersionId?'已发布（当前）':'已被取代',
    esc(origins(row)),
    `<code class="digest">${esc(row.digest)}</code>`,
    esc(date(row.published_at)),
  ]);
  return records(['版本','状态','来源','快照校验码','发布时点'], rows, '尚无版本。');
}

function submissionForms() {
  return panel('紧急撤销（单独到达，立即发布）', `<form id="urgent-form" class="field-grid">
      <label class="field"><span>人员编号</span><input name="operator_id" required /></label>
      <label class="field"><span>作业区域</span><input name="zone" required /></label>
      <label class="field"><span>表计类型</span><select name="utility"><option>水表</option><option>电表</option></select></label>
      <div class="actions form-actions"><button>撤销并发布</button></div></form>`)
    + panel('换班交接（撤销与续派收齐后一起发布）', `<form id="handover-form" class="field-grid">
      <label class="field"><span>交接编号</span><input name="handover_ref" required maxlength="64" placeholder="HO-20261121-01" /></label>
      <label class="field"><span>片段类型</span><select name="part"><option value="revoke">撤销</option><option value="reassign">续派</option></select></label>
      <label class="field"><span>人员编号</span><input name="operator_id" required /></label>
      <label class="field"><span>作业区域</span><input name="zone" required /></label>
      <label class="field"><span>表计类型</span><select name="utility"><option>水表</option><option>电表</option></select></label>
      <label class="field"><span>续派生效（含时区）</span><input name="valid_from" placeholder="2026-11-21T08:00:00+08:00" /></label>
      <label class="field"><span>续派到期（含时区）</span><input name="valid_until" placeholder="2026-11-21T12:00:00+08:00" /></label>
      <div class="actions form-actions"><button>登记片段</button></div></form>`);
}

export function correctionsPage(state) {
  const operator = state.session.role==='operator';
  const versions = state.versions || [];
  const current = versions.find(row=>row.id===state.currentVersionId);
  const corrections = state.corrections || [];
  const pending = corrections.filter(row=>row.status==='pending');
  const note = operator
    ? '紧急撤销单独发布；同一次交接的撤销与续派收齐后一起发布。未收齐的片段留在待收集状态，不改变已发布版本。'
    : '经办员可以登记并发布调度更正。这里只能查看版本与已登记的更正。';
  return `${heading(topic.modules[3], note)}
    <div class="spread"><section>${operator?submissionForms():panel('待收集片段', correctionTable(pending, '没有待收集片段。'))}</section>
      <aside class="brief"><h2>当前发布版本</h2>
        ${current?`<dl><dt>版本</dt><dd>第 ${esc(current.version_no)} 版</dd><dt>来源</dt><dd>${esc(origins(current))}</dd><dt>发布时点</dt><dd>${esc(date(current.published_at))}</dd><dt>快照校验码</dt><dd><code class="digest">${esc(current.digest)}</code></dd></dl>`:'<p class="note">尚无发布版本。</p>'}
        <p class="note">新提交的验签在排队时锁定当时的发布版本；之后发布的更正不改变它的验签依据。</p>
        ${operator?`<h2>待收集片段</h2>${correctionTable(pending, '没有待收集片段。')}`:''}
      </aside></div>
    ${operator?panel('已登记的更正', correctionTable(corrections, '还没有登记更正。')):''}
    ${panel('委派版本历史', versionTable(state))}`;
}

export function bindCorrections(api, update, fail) {
  submit('urgent-form', async data=>{
    await api('/corrections', {method:'POST', data:{
      kind:'urgent_revoke',
      revoke:[{operator_id:data.get('operator_id'), zone:data.get('zone'), utility:data.get('utility')}],
      reassign:[],
    }});
    await update();
  }, fail);
  submit('handover-form', async data=>{
    const part = data.get('part');
    const base = {operator_id:data.get('operator_id'), zone:data.get('zone'), utility:data.get('utility')};
    const revoke = part==='revoke' ? [base] : [];
    const reassign = part==='reassign' ? [{...base, valid_from:data.get('valid_from'), valid_until:data.get('valid_until')}] : [];
    if (part==='reassign' && (!data.get('valid_from') || !data.get('valid_until'))) {
      throw new Error('续派片段须填写生效与到期时间。');
    }
    await api('/corrections', {method:'POST', data:{kind:'handover', handover_ref:data.get('handover_ref'), revoke, reassign}});
    await update();
  }, fail);
}
