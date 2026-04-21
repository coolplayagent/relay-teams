/**
 * components/agentPanel/dom.js
 * DOM helpers for the session-level subagent panel.
 */
export function getDrawer() {
    const existing = document.getElementById('agent-drawer');
    if (existing) {
        return existing;
    }
    if (typeof document?.createElement !== 'function' || !document.body) {
        return null;
    }
    const scratch = document.createElement('div');
    scratch.id = 'agent-drawer';
    scratch.hidden = true;
    scratch.setAttribute('aria-hidden', 'true');
    scratch.style.display = 'none';
    document.body.appendChild(scratch);
    return scratch;
}

export function getSubagentCard() {
    return document.querySelector('.rail-subagent-card');
}

export function openDrawerUi() {
    const drawer = getDrawer();
    if (drawer) drawer.classList.add('open');
    const card = getSubagentCard();
    if (card) card.classList.add('open');
}

export function closeDrawerUi() {
    const drawer = getDrawer();
    if (drawer) drawer.classList.remove('open');
    const card = getSubagentCard();
    if (card) card.classList.remove('open');
}
