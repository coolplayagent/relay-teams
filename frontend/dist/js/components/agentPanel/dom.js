/**
 * components/agentPanel/dom.js
 * DOM helpers for agent drawer and DAG highlight state.
 */
export function getDrawer() {
    return document.getElementById('agent-drawer');
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

export function clearDagNodeHighlight() {
    document.querySelectorAll('.dag-node').forEach(n => n.classList.remove('active-tab'));
}

export function highlightNode(roleId, instanceId) {
    document.querySelectorAll('.dag-node').forEach(n => {
        n.classList.remove('active-tab');
        if (instanceId) {
            if (n.dataset.instanceId === instanceId) {
                n.classList.add('active-tab');
            }
            return;
        }
        if (roleId && n.dataset.role === roleId) {
            n.classList.add('active-tab');
        }
    });
}
