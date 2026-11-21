import {esc} from '../../utils/dom.js';
import {statusLabels} from '../../constants/workflow.js';
export function badge(status) {
  return `<span class="status" data-state="${esc(status)}">${esc(statusLabels[status] || status)}</span>`;
}
// A badge whose aria state and visible text differ, e.g. correction status.
export function stateBadge(state, label) {
  return `<span class="status" data-state="${esc(state)}">${esc(label)}</span>`;
}
