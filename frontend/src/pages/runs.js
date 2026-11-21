import {heading, panel} from '../components/common/heading.js';
import {records} from '../components/common/records.js';
import {badge} from '../components/common/status.js';
import {statusLabels} from '../constants/workflow.js';
import {topic} from '../constants/project.js';
import {date, esc} from '../utils/dom.js';

export function runsPage(state, identity) {
  const selected = state.runs.find(row=>row.id===identity) || state.runs[0];
  const cases = new Map(state.cases.map(row=>[row.id, row.title]));
  const versions = new Map((state.versions||[]).map(row=>[row.id, row]));
  const rows = state.runs.map(row=>[`<a href="#runs/${row.id}">${esc(cases.get(row.case_id) || row.case_id)}</a>`, badge(row.status), esc(row.owner), esc(date(row.created_at))]);
  const result = state.evidence.find(row=>row.run_id===selected?.id);
  const active = selected && ['queued','running'].includes(selected.status);
  const pinned = selected && versions.get(selected.delegation_version_id);
  const detail = selected ? `<h2>${esc(cases.get(selected.case_id) || selected.case_id)}</h2>
    <p data-testid="run-status">${badge(selected.status)}</p>
    <dl><dt>启动时间</dt><dd>${esc(date(selected.started_at))}</dd><dt>完成时间</dt><dd>${esc(date(selected.finished_at))}</dd>
    <dt>锁定委派版本</dt><dd data-testid="pinned-version">${pinned?`第 ${esc(pinned.version_no)} 版`:'未记录'}</dd>
    ${selected.previous_run_id?`<dt>重试自</dt><dd><a href="#runs/${esc(selected.previous_run_id)}">执行 ${esc(selected.previous_run_id)}</a></dd>`:''}</dl>
    ${selected.error ? `<p class="error">${esc(selected.error)}</p>` : ''}
    ${active?'<p class="note">后台处理中，记录会自动刷新。入队时已锁定委派版本，排队期间的更正不会改变本次验签依据。</p>':''}
    ${selected.status==='failed'&&!result?`<button type="button" id="retry-run" data-id="${esc(selected.id)}" class="secondary">按原版本重试</button>`:''}
    ${result?`<a class="button" href="#evidence/${result.id}">查看业务证据</a>`:''}` : '<h2>执行记录</h2><p>从档案创建一条执行记录后，可在此查看进度。</p>';
  const audit = state.audit.filter(x=>selected && x.reference===String(selected.id) && ['enqueue', 'retry', 'complete', 'failed'].includes(x.action));
  return `${heading(topic.modules[2], `${state.spec.entity_labels[1]}与后台处理进度`, '<button class="secondary" type="button" id="refresh">刷新记录</button>')}
    <div class="spread"><section>${records(['档案','状态','经办','提交时间'], rows, '暂无执行记录。')}</section><aside class="brief">${detail}</aside></div>
    ${audit.length?panel('操作记录', records(['操作者','操作','时间'], audit.map(x=>[esc(x.actor),esc(statusLabels[x.action]||x.action),esc(date(x.created_at))]))):''}`;
}

export function bindRuns(state, api, update, fail) {
  document.getElementById('refresh').addEventListener('click', update);
  document.getElementById('retry-run')?.addEventListener('click', async event=>{
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const {id} = await api(`/runs/${button.dataset.id}/retry`, {method:'POST'});
      location.hash = `runs/${id}`;
      await update();
    } catch (error) { button.disabled = false; fail(error); }
  });
  return state.runs.some(row=>['queued','running'].includes(row.status));
}
