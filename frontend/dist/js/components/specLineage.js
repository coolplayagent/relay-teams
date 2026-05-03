/**
 * components/specLineage.js
 * Spec artifact lineage visualization page.
 * Renders a timeline of spec versions with diff viewer and drift evaluations.
 */
import { SpecDiffViewer } from './specDiffViewer.js';
import { requestJson } from '../core/api/request.js';
import { sysLog } from '../utils/logger.js';

let specLineageView = null;
let diffViewer = null;
let currentTaskId = null;
let versionData = null;
let evaluationData = null;
let selectedVersion = null;

/**
 * Initialize the spec lineage view bindings (called once during bootstrap).
 */
export function initializeSpecLineage() {
    specLineageView = document.getElementById('spec-lineage-view');
    if (!specLineageView) {
        return;
    }

    const backBtn = document.getElementById('spec-lineage-back-btn');
    if (backBtn) {
        backBtn.onclick = () => setVisible(false);
    }

    const reloadBtn = document.getElementById('spec-lineage-reload');
    if (reloadBtn) {
        reloadBtn.onclick = () => {
            if (currentTaskId) {
                void load(currentTaskId);
            }
        };
    }
}

/**
 * Show the spec lineage view for a specific task.
 * @param {string} taskId
 */
export function openSpecLineage(taskId) {
    const safeTaskId = String(taskId || '').trim();
    if (!safeTaskId) {
        return;
    }
    currentTaskId = safeTaskId;
    selectedVersion = null;
    versionData = null;
    evaluationData = null;
    setVisible(true);
    void load(safeTaskId);
}

/**
 * Close the spec lineage view.
 */
export function hideSpecLineage() {
    setVisible(false);
    currentTaskId = null;
    selectedVersion = null;
    versionData = null;
    evaluationData = null;
}

/**
 * Check if the spec lineage view is visible.
 * @returns {boolean}
 */
export function isSpecLineageVisible() {
    if (!specLineageView) {
        return false;
    }
    return specLineageView.style.display !== 'none';
}

/**
 * Parse task_id from URL hash/query. Supports ?task_id=... or #spec-lineage?task_id=...
 * @returns {string|null}
 */
export function getTaskIdFromUrl() {
    try {
        const url = new URL(window.location.href);
        return url.searchParams.get('task_id') || null;
    } catch (_) {
        return null;
    }
}

// --- Internal ---

function setVisible(visible) {
    if (!specLineageView) {
        return;
    }
    specLineageView.style.display = visible ? 'block' : 'none';
    if (visible) {
        hideOtherPanels();
    } else {
        showMainPanel();
    }
}

function hideOtherPanels() {
    const chatContainer = document.querySelector('.chat-container');
    const observabilityView = document.getElementById('observability-view');
    const projectView = document.getElementById('project-view');
    if (chatContainer) {
        chatContainer.style.display = 'none';
    }
    if (observabilityView) {
        observabilityView.style.display = 'none';
    }
    if (projectView) {
        projectView.style.display = 'none';
    }
}

function showMainPanel() {
    const chatContainer = document.querySelector('.chat-container');
    if (chatContainer) {
        chatContainer.style.display = '';
    }
}

async function load(taskId) {
    const timelineHost = specLineageView
        ? specLineageView.querySelector('#spec-lineage-timeline')
        : null;
    const diffHost = specLineageView
        ? specLineageView.querySelector('#spec-lineage-diff')
        : null;

    if (!timelineHost || !diffHost) {
        return;
    }

    // Show loading
    timelineHost.innerHTML = '<div class="spec-lineage-loading">Loading versions...</div>';
    diffHost.innerHTML = '';

    if (!diffViewer) {
        diffViewer = new SpecDiffViewer(diffHost);
    } else {
        diffViewer = new SpecDiffViewer(diffHost);
    }

    // Update title
    const titleEl = specLineageView.querySelector('#spec-lineage-title');
    const summaryEl = specLineageView.querySelector('#spec-lineage-summary');
    if (titleEl) {
        titleEl.textContent = 'Spec Lineage';
    }
    if (summaryEl) {
        summaryEl.textContent = `Task: ${taskId}`;
    }

    try {
        const [versionsResult, evalResult] = await Promise.allSettled([
            requestJson(
                `/api/tasks/${encodeURIComponent(taskId)}/spec-artifacts?format=summary`,
                {},
                'Failed to fetch spec artifacts',
            ),
            requestJson(
                `/api/tasks/${encodeURIComponent(taskId)}/spec-checkpoint-evaluations`,
                {},
                'Failed to fetch drift evaluations',
            ),
        ]);

        if (versionsResult.status === 'fulfilled') {
            const data = versionsResult.value;
            const versions = Array.isArray(data.versions) ? data.versions : [];
            versionData = versions;

            if (evalResult.status === 'fulfilled') {
                evaluationData = Array.isArray(evalResult.value.evaluations)
                    ? evalResult.value.evaluations
                    : [];
            } else {
                evaluationData = [];
                sysLog(`spec-lineage: evaluations fetch failed: ${evalResult.reason?.message || 'unknown'}`);
            }

            if (versions.length === 0) {
                timelineHost.innerHTML = '';
                diffHost.innerHTML = '';
                renderEmpty(timelineHost);
                return;
            }

            renderTimeline(timelineHost, versions, evaluationData);
            diffViewer.renderPlaceholder();
        } else {
            timelineHost.innerHTML = '';
            diffHost.innerHTML = '';
            const errorEl = document.createElement('div');
            errorEl.className = 'spec-lineage-empty';
            errorEl.innerHTML = `<p>Failed to load spec artifacts: ${_escapeHtml(versionsResult.reason?.message || 'unknown error')}</p>`;
            timelineHost.appendChild(errorEl);
        }
    } catch (err) {
        sysLog(`spec-lineage: load failed: ${err.message}`);
        timelineHost.innerHTML = '';
        diffHost.innerHTML = '';
        const errorEl = document.createElement('div');
        errorEl.className = 'spec-lineage-empty';
        errorEl.innerHTML = `<p>Error: ${_escapeHtml(err.message)}</p>`;
        timelineHost.appendChild(errorEl);
    }
}

function renderTimeline(host, versions, evaluations) {
    host.innerHTML = '';

    // Build a map from version number to evaluation records
    const evalByVersion = new Map();
    for (const ev of evaluations) {
        const artifactId = String(ev.artifact_id || '').trim();
        // Try to correlate by looking at the version in versionData
        if (versionData) {
            for (const v of versionData) {
                if (String(v.artifact_id || '').trim() === artifactId) {
                    const list = evalByVersion.get(v.version) || [];
                    list.push(ev);
                    evalByVersion.set(v.version, list);
                }
            }
        }
    }

    const heading = document.createElement('div');
    heading.className = 'spec-lineage-timeline-heading';
    heading.textContent = `${versions.length} version${versions.length > 1 ? 's' : ''}`;
    host.appendChild(heading);

    // Render in ascending order (oldest first)
    const sorted = [...versions].sort((a, b) => (a.version || 0) - (b.version || 0));

    for (let i = 0; i < sorted.length; i++) {
        const v = sorted[i];
        const node = document.createElement('div');
        node.className = 'spec-lineage-node';
        node.setAttribute('data-version', String(v.version));

        const dot = document.createElement('div');
        dot.className = 'spec-diff-dot spec-lineage-dot';

        // Add drift indicator if evaluation exists for this version
        const versionEvals = evalByVersion.get(v.version) || [];
        if (versionEvals.length > 0) {
            const latestEval = versionEvals[versionEvals.length - 1];
            if (latestEval.drift_detected) {
                const score = typeof latestEval.overall_score === 'number' ? latestEval.overall_score : 5;
                dot.classList.add(score < 2.0 ? 'drift-high' : 'drift-low');
            } else {
                dot.classList.add('drift-none');
            }
        }

        const info = document.createElement('div');
        info.className = 'spec-lineage-node-info';

        const label = document.createElement('div');
        label.className = 'spec-lineage-version-label';
        label.textContent = `v${v.version}`;

        const time = document.createElement('div');
        time.className = 'spec-lineage-version-time';
        time.textContent = formatTimestamp(v.created_at);

        const summary = document.createElement('div');
        summary.className = 'spec-lineage-version-summary';
        summary.textContent = v.artifact_id ? `ID: ${v.artifact_id.slice(0, 16)}...` : '';

        info.appendChild(label);
        info.appendChild(time);
        if (summary.textContent) {
            info.appendChild(summary);
        }

        node.appendChild(dot);
        node.appendChild(info);

        // Click handler: select this version and show diff
        node.addEventListener('click', () => {
            selectVersion(v.version, sorted);
        });

        host.appendChild(node);
    }
}

function selectVersion(version, sortedVersions) {
    if (!specLineageView) {
        return;
    }

    selectedVersion = version;

    // Update selected state in timeline
    const nodes = specLineageView.querySelectorAll('.spec-lineage-node');
    for (const n of nodes) {
        n.classList.toggle('selected', Number(n.getAttribute('data-version')) === version);
    }

    // Determine from_version (previous version in list)
    const idx = sortedVersions.findIndex(v => v.version === version);
    let fromVersion = null;

    // v1 has no previous version, so diff only works for v2+
    if (version > 1 && idx > 0) {
        fromVersion = sortedVersions[idx - 1].version;
    }

    if (version <= 1) {
        // No diff available for v1
        const diffHost = specLineageView.querySelector('#spec-lineage-diff');
        if (diffHost) {
            diffHost.innerHTML = '';
            const panel = document.createElement('div');
            panel.className = 'spec-diff-panel';
            panel.innerHTML = `
                <div class="spec-diff-header">
                    <h4>Version 1 (Initial)</h4>
                    <p>This is the first spec version. No diff available.</p>
                </div>
            `;
            diffHost.appendChild(panel);
        }
        return;
    }

    // Find evaluations relevant to this version
    const versionEvals = evaluationData
        ? evaluationData.filter(ev => {
            // Match by artifact_id if possible
            const matchingVersion = versionData
                ? versionData.find(v => v.version === version)
                : null;
            if (matchingVersion && ev.artifact_id === matchingVersion.artifact_id) {
                return true;
            }
            return false;
        })
        : [];

    void diffViewer.loadDiff(currentTaskId, version, fromVersion, versionEvals);
}

function renderEmpty(host) {
    const empty = document.createElement('div');
    empty.className = 'spec-lineage-empty';
    empty.innerHTML = `
        <svg class="spec-lineage-empty-icon" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M4 19h16M7 16V9m5 7V5m5 11v-6"
                stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
        </svg>
        <p>No spec artifacts found for this task.</p>
        <p style="font-size:0.78rem;margin-top:0.25rem">Spec artifacts are created when a task has an associated spec definition.</p>
    `;
    host.appendChild(empty);

    // Also put placeholder in diff area
    const diffHost = specLineageView
        ? specLineageView.querySelector('#spec-lineage-diff')
        : null;
    if (diffHost) {
        diffHost.innerHTML = '<div class="spec-diff-placeholder"><p>No data</p></div>';
    }
}

function formatTimestamp(isoString) {
    if (!isoString) {
        return '--';
    }
    try {
        const d = new Date(isoString);
        if (isNaN(d.getTime())) {
            return isoString;
        }
        const year = d.getFullYear();
        const month = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        const hours = String(d.getHours()).padStart(2, '0');
        const mins = String(d.getMinutes()).padStart(2, '0');
        return `${year}-${month}-${day} ${hours}:${mins}`;
    } catch (_) {
        return isoString;
    }
}

function _escapeHtml(value) {
    return String(value || '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
}
