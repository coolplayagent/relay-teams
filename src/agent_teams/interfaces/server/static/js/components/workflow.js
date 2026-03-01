/**
 * components/workflow.js
 * Responsible for parsing workflow dependencies and rendering the Native Topo DAG.
 */
import { els } from '../utils/dom.js';
import { sysLog } from '../utils/logger.js';
import { state } from '../core/state.js';
import { fetchSessionWorkflows } from '../core/api.js';

export let currentWorkflows = [];

export async function loadSessionWorkflows(sessionId) {
    try {
        const workflows = await fetchSessionWorkflows(sessionId);
        currentWorkflows = workflows || [];

        const sel = els.workflowSelect;
        if (!sel) return;

        sel.innerHTML = '';
        sel.onchange = (e) => {
            const idx = parseInt(e.target.value);
            if (idx >= 0 && idx < currentWorkflows.length) {
                renderNativeDAG(currentWorkflows[idx]);
            } else {
                renderNativeDAG(null);
            }
        };

        if (currentWorkflows.length > 0) {
            currentWorkflows.forEach((wf, i) => {
                const opt = document.createElement('option');
                opt.value = i;
                opt.textContent = `Orchestration ${i + 1}`;
                sel.appendChild(opt);
            });
            sel.value = currentWorkflows.length - 1;
            renderNativeDAG(currentWorkflows[currentWorkflows.length - 1]);
        } else {
            const opt = document.createElement('option');
            opt.value = "-1";
            opt.textContent = `Default Coordinator`;
            sel.appendChild(opt);
            renderNativeDAG(null);
        }
    } catch (e) {
        console.error("Failed loading workflows", e);
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
    if (!canvas || !els.workflowPanel) return;

    canvas.innerHTML = '';
    els.workflowPanel.style.display = 'flex';

    const container = document.createElement('div');
    container.className = 'dag-container';

    const nodeLevels = {};
    let maxLevel = 0;

    if (workflow && workflow.tasks) {
        const tasks = workflow.tasks;
        let changed = true;
        while (changed) {
            changed = false;
            for (const t in tasks) {
                const deps = tasks[t].depends_on || [];
                let maxDepLevel = 0;
                deps.forEach(d => {
                    if (nodeLevels[d] !== undefined) {
                        maxDepLevel = Math.max(maxDepLevel, nodeLevels[d]);
                    }
                });
                const newLevel = maxDepLevel + 1;
                if (nodeLevels[t] !== newLevel) {
                    nodeLevels[t] = newLevel;
                    changed = true;
                }
            }
        }
        for (let t in nodeLevels) {
            if (nodeLevels[t] > maxLevel) maxLevel = nodeLevels[t];
        }
    }

    const layers = [];
    layers.push([{ id: 'coordinator', title: 'Coordinator Agent', role: 'coordinator_agent', icon: '🤖' }]);

    if (workflow && workflow.tasks) {
        for (let i = 1; i <= maxLevel; i++) {
            const layerNodes = [];
            for (let t in nodeLevels) {
                if (nodeLevels[t] === i) {
                    layerNodes.push({
                        id: t,
                        title: t,
                        role: workflow.tasks[t].role_id || t,
                        icon: '⚡',
                        deps: workflow.tasks[t].depends_on || []
                    });
                }
            }
            if (layerNodes.length > 0) layers.push(layerNodes);
        }
    }

    layers.forEach((layer, lvlIndex) => {
        const col = document.createElement('div');
        col.className = 'dag-layer';
        layer.forEach(node => {
            const el = document.createElement('div');
            el.className = 'dag-node';
            el.id = `node-${node.id}`;
            el.dataset.role = node.role;
            if (state.activeAgentRoleId === node.role) el.classList.add('running');

            // Route click manually to window global from app.js router
            el.onclick = () => window.switchTabByRole && window.switchTabByRole(node.role);

            el.innerHTML = `
                <div class="node-icon">${node.icon}</div>
                <div class="node-title">${node.title}</div>
                <div class="node-role">${node.role}</div>
            `;
            col.appendChild(el);
        });
        container.appendChild(col);
    });

    canvas.appendChild(container);

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
                    let sources = node.deps.length > 0 ? node.deps : ['coordinator'];
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
