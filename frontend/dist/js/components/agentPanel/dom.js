/**
 * components/agentPanel/dom.js
 * DOM helpers for the main-workspace subagent panel.
 */
import { els } from '../../utils/dom.js';

export function getDrawer() {
    return els.agentDrawer || document.getElementById('agent-drawer');
}

export function getSubagentCard() {
    return getDrawer();
}

export function openDrawerUi() {
    const workspace = getDrawer();
    if (workspace) {
        workspace.hidden = false;
        workspace.classList.add('open');
    }
}

export function closeDrawerUi() {
    const workspace = getDrawer();
    if (workspace) {
        workspace.hidden = true;
        workspace.classList.remove('open');
    }
}
