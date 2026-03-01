/**
 * components/workflow.js
 * Renders the Execution Graph DAG.
 * DAG nodes are clickable and open the subagent panel in the right drawer.
 */
import { els } from '../utils/dom.js';
import { sysLog } from '../utils/logger.js';
import { state } from '../core/state.js';
import { fetchSessionWorkflows } from '../core/api.js';
import { openAgentPanel } from './agentPanel.js';

export let currentWorkflows = [];

export async function loadSessionWorkflows(sessionId) {
    try {
        const workflows = await fetchSessionWorkflows(sessionId);
        currentWorkflows = workflows || [];
        renderNativeDAG(currentWorkflows.length > 0 ? currentWorkflows[currentWorkflows.length - 1] : null);
    } catch (e) {
        console.error('Failed loading workflows', e);
    }
}

export function updateDagActiveNode() {
    document.querySelectorAll('.dag-node').forEach(node => {
        if (node.dataset.role === state.activeAgentRoleId) {
            node.classList.add('running');
        } else {
            node.classList.remove('running');
        }
    });
}

export function renderNativeDAG(workflow) {
    const canvas = document.getElementById('workflow-canvas');
    if (!canvas) return;
    canvas.innerHTML = '';

    if (!workflow?.tasks) return;

    const container = document.createElement('div');
    container.className = 'dag-container';

    // ── Compute topological levels ──────────────────────────────────────────
    const tasks = workflow.tasks;
    const nodeLevels = {};
    let maxLevel = 0;
    let changed = true;
    while (changed) {
        changed = false;
        for (const t in tasks) {
            const deps = tasks[t].depends_on || [];
            let maxDep = 0;
            deps.forEach(d => { if (nodeLevels[d] !== undefined) maxDep = Math.max(maxDep, nodeLevels[d]); });
            const newLevel = maxDep + 1;
            if (nodeLevels[t] !== newLevel) { nodeLevels[t] = newLevel; changed = true; }
        }
    }
    for (const t in nodeLevels) if (nodeLevels[t] > maxLevel) maxLevel = nodeLevels[t];

    // ── Build layers ────────────────────────────────────────────────────────
    const layers = [
        [{ id: 'coordinator', title: 'Coordinator', role: 'coordinator_agent', icon: '🤖', deps: [] }]
    ];
    for (let i = 1; i <= maxLevel; i++) {
        const layerNodes = [];
        for (const t in nodeLevels) {
            if (nodeLevels[t] === i) {
                layerNodes.push({
                    id: t,
                    title: t,
                    role: tasks[t].role_id || t,
                    icon: '⚡',
                    deps: tasks[t].depends_on || [],
                });
            }
        }
        if (layerNodes.length > 0) layers.push(layerNodes);
    }

    // ── Render nodes ────────────────────────────────────────────────────────
    layers.forEach((layer) => {
        const col = document.createElement('div');
        col.className = 'dag-layer';

        layer.forEach(node => {
            const el = document.createElement('div');
            el.className = 'dag-node';
            el.id = `node-${node.id}`;
            el.dataset.role = node.role;

            // Resolve instance ID from state map (populated by model_step_started events)
            const instanceId = _instanceForRole(node.role);
            if (instanceId) el.dataset.instanceId = instanceId;

            if (state.activeAgentRoleId === node.role) el.classList.add('running');

            el.innerHTML = `
                <div class="node-icon">${node.icon}</div>
                <div class="node-title">${node.title}</div>
                <div class="node-role">${node.role}</div>
            `;

            // Click → open agent panel (or coordinator chat)
            el.onclick = () => {
                if (node.role === 'coordinator_agent') {
                    // Just scroll back to top of coordinator chat
                    if (els.chatMessages) els.chatMessages.scrollTop = 0;
                    return;
                }
                const iid = el.dataset.instanceId || instanceId;
                if (iid) {
                    openAgentPanel(iid, node.role);
                } else {
                    // Instance not yet running — open a placeholder panel
                    openAgentPanel(`pending-${node.role}`, node.role);
                }
            };

            col.appendChild(el);
        });
        container.appendChild(col);
    });

    canvas.appendChild(container);

    // ── Draw SVG edges ──────────────────────────────────────────────────────
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'dag-edges');
    const defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
    const marker = document.createElementNS('http://www.w3.org/2000/svg', 'marker');
    marker.setAttribute('id', 'arrow');
    marker.setAttribute('viewBox', '0 0 10 10');
    marker.setAttribute('refX', '8');
    marker.setAttribute('refY', '5');
    marker.setAttribute('markerWidth', '6');
    marker.setAttribute('markerHeight', '6');
    marker.setAttribute('orient', 'auto-start-reverse');
    const pathArrow = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    pathArrow.setAttribute('d', 'M 0 0 L 10 5 L 0 10 z');
    pathArrow.setAttribute('fill', 'var(--border-color)');
    marker.appendChild(pathArrow);
    defs.appendChild(marker);
    svg.appendChild(defs);
    container.appendChild(svg);

    requestAnimationFrame(() => {
        requestAnimationFrame(() => {
            const contRect = container.getBoundingClientRect();
            layers.forEach((layer, lvlIndex) => {
                if (lvlIndex === 0) return;
                layer.forEach(node => {
                    const sources = node.deps.length > 0 ? node.deps : ['coordinator'];
                    sources.forEach(srcId => {
                        const srcEl = document.getElementById(`node-${srcId}`);
                        const dstEl = document.getElementById(`node-${node.id}`);
                        if (srcEl && dstEl) {
                            const srcRect = srcEl.getBoundingClientRect();
                            const dstRect = dstEl.getBoundingClientRect();
                            const startX = srcRect.right - contRect.left;
                            const startY = srcRect.top + srcRect.height / 2 - contRect.top;
                            const endX = dstRect.left - contRect.left;
                            const endY = dstRect.top + dstRect.height / 2 - contRect.top;
                            const curve = Math.abs(endX - startX) * 0.5;
                            const d = `M ${startX} ${startY} C ${startX + curve} ${startY}, ${endX - curve} ${endY}, ${endX} ${endY}`;
                            const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
                            path.setAttribute('d', d);
                            path.setAttribute('class', 'dag-edge-path');
                            path.setAttribute('marker-end', 'url(#arrow)');
                            svg.appendChild(path);
                        }
                    });
                });
            });
        });
    });
}

// Look up instance ID from the role→instance map built during SSE events
function _instanceForRole(roleId) {
    if (!state.instanceRoleMap) return null;
    for (const [iid, rid] of Object.entries(state.instanceRoleMap)) {
        if (rid === roleId) return iid;
    }
    return null;
}
