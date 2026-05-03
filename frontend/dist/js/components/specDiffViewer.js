/**
 * components/specDiffViewer.js
 * Renders field-level diff between two spec artifact versions.
 */
import { requestJson } from '../core/api/request.js';
import { sysLog } from '../utils/logger.js';

/**
 * SpecDiffViewer: renders field-level diffs for spec artifact versions.
 */
export class SpecDiffViewer {
    /**
     * @param {HTMLElement} hostEl - Container element to render into.
     */
    constructor(hostEl) {
        this._host = hostEl;
        this._taskId = null;
        this._expandedFields = new Set();
    }

    /**
     * Load and render the diff for a given version pair.
     * @param {string} taskId
     * @param {number} toVersion
     * @param {number|null} fromVersion - If null, API defaults to previous version.
     * @param {Array|null} evaluations - Optional evaluation records to render alongside.
     */
    async loadDiff(taskId, toVersion, fromVersion = null, evaluations = null) {
        this._taskId = taskId;
        this._expandedFields.clear();
        this._host.innerHTML = '';

        const panel = document.createElement('div');
        panel.className = 'spec-diff-panel';
        this._host.appendChild(panel);

        try {
            let url = `/api/tasks/${encodeURIComponent(taskId)}/spec-artifacts/${encodeURIComponent(String(toVersion))}/diff`;
            if (fromVersion != null) {
                url += `?from_version=${encodeURIComponent(String(fromVersion))}`;
            }

            const data = await requestJson(url, {}, 'Failed to load spec artifact diff');
            this._renderDiffContent(panel, data, toVersion, fromVersion);

            if (evaluations && evaluations.length > 0) {
                this._renderEvaluations(panel, evaluations);
            }
        } catch (err) {
            panel.innerHTML = `
                <div class="spec-diff-header">
                    <h4>Error</h4>
                    <p>${_escapeHtml(err.message || 'Failed to load diff')}</p>
                </div>
            `;
            sysLog(`spec-diff-viewer: load failed: ${err.message}`);
        }
    }

    /**
     * Render a placeholder when no version is selected.
     */
    renderPlaceholder() {
        this._host.innerHTML = `
            <div class="spec-diff-placeholder">
                <p>Select a version to view the diff</p>
            </div>
        `;
    }

    _renderDiffContent(panel, data, toVersion, fromVersion) {
        const header = document.createElement('div');
        header.className = 'spec-diff-header';

        const fromLabel = fromVersion != null ? fromVersion : (toVersion - 1);
        header.innerHTML = `
            <h4>v${_escapeHtml(String(fromLabel))} &rarr; v${_escapeHtml(String(toVersion))}</h4>
            <p>${data.has_changes ? _escapeHtml(data.summary || 'Changes detected') : 'No changes between versions'}</p>
        `;
        panel.appendChild(header);

        const body = document.createElement('div');
        body.className = 'spec-diff-body';
        panel.appendChild(body);

        const changes = Array.isArray(data.field_changes) ? data.field_changes : [];
        if (!data.has_changes || changes.length === 0) {
            body.innerHTML = '<div class="spec-diff-no-changes">No field-level changes detected between these versions.</div>';
            return;
        }

        const changedFields = changes.filter(c => c.change_type !== 'unchanged');
        const unchangedFields = changes.filter(c => c.change_type === 'unchanged');

        for (const change of changedFields) {
            body.appendChild(this._createFieldRow(change));
        }

        if (unchangedFields.length > 0) {
            const toggle = document.createElement('div');
            toggle.className = 'spec-diff-field';
            toggle.style.cursor = 'pointer';
            toggle.style.padding = '0.4rem 0.65rem';
            toggle.style.fontSize = '0.78rem';
            toggle.style.color = 'var(--text-secondary)';
            toggle.textContent = `${unchangedFields.length} unchanged field${unchangedFields.length > 1 ? 's' : ''}`;
            toggle.title = 'Click to expand';

            const hiddenContainer = document.createElement('div');
            hiddenContainer.style.display = 'none';

            toggle.addEventListener('click', () => {
                const showing = hiddenContainer.style.display !== 'none';
                hiddenContainer.style.display = showing ? 'none' : 'block';
                toggle.textContent = showing
                    ? `${unchangedFields.length} unchanged field${unchangedFields.length > 1 ? 's' : ''}`
                    : 'Hide unchanged fields';
            });

            for (const change of unchangedFields) {
                hiddenContainer.appendChild(this._createFieldRow(change));
            }

            body.appendChild(toggle);
            body.appendChild(hiddenContainer);
        }
    }

    _createFieldRow(change) {
        const field = document.createElement('div');
        field.className = 'spec-diff-field';
        if (this._expandedFields.has(change.field_name)) {
            field.classList.add('expanded');
        }

        const header = document.createElement('div');
        header.className = 'spec-diff-field-header';

        const badge = document.createElement('span');
        badge.className = `spec-diff-change-badge ${change.change_type}`;
        badge.textContent = change.change_type;

        const name = document.createElement('span');
        name.className = 'spec-diff-field-name';
        name.textContent = change.field_label || change.field_name;

        const chevron = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        chevron.setAttribute('viewBox', '0 0 24 24');
        chevron.setAttribute('fill', 'none');
        chevron.classList.add('spec-diff-field-expand-icon');
        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('d', 'M6 9l6 6 6-6');
        path.setAttribute('stroke', 'currentColor');
        path.setAttribute('stroke-width', '2');
        path.setAttribute('stroke-linecap', 'round');
        path.setAttribute('stroke-linejoin', 'round');
        chevron.appendChild(path);

        header.appendChild(badge);
        header.appendChild(name);
        header.appendChild(chevron);

        const detail = document.createElement('div');
        detail.className = 'spec-diff-field-detail';
        this._renderFieldDetail(detail, change);

        header.addEventListener('click', () => {
            field.classList.toggle('expanded');
            if (field.classList.contains('expanded')) {
                this._expandedFields.add(change.field_name);
            } else {
                this._expandedFields.delete(change.field_name);
            }
        });

        field.appendChild(header);
        field.appendChild(detail);
        return field;
    }

    _renderFieldDetail(detail, change) {
        const addedItems = Array.isArray(change.added_items) ? change.added_items : [];
        const removedItems = Array.isArray(change.removed_items) ? change.removed_items : [];

        // Scalar value changes
        if (change.old_value != null || change.new_value != null) {
            if (change.old_value != null) {
                const oldDiv = document.createElement('div');
                oldDiv.className = 'spec-diff-old-value';
                oldDiv.textContent = String(change.old_value);
                detail.appendChild(oldDiv);
            }
            if (change.new_value != null) {
                const newDiv = document.createElement('div');
                newDiv.className = 'spec-diff-new-value';
                newDiv.textContent = String(change.new_value);
                detail.appendChild(newDiv);
            }
        }

        // Item-based changes (requirements, constraints, etc.)
        if (addedItems.length > 0) {
            const h = document.createElement('div');
            h.className = 'spec-diff-items-header';
            h.textContent = `Added (${addedItems.length})`;
            detail.appendChild(h);

            const ul = document.createElement('ul');
            ul.className = 'spec-diff-added-items';
            for (const item of addedItems) {
                const li = document.createElement('li');
                li.textContent = String(item);
                ul.appendChild(li);
            }
            detail.appendChild(ul);
        }

        if (removedItems.length > 0) {
            const h = document.createElement('div');
            h.className = 'spec-diff-items-header';
            h.textContent = `Removed (${removedItems.length})`;
            detail.appendChild(h);

            const ul = document.createElement('ul');
            ul.className = 'spec-diff-removed-items';
            for (const item of removedItems) {
                const li = document.createElement('li');
                li.textContent = String(item);
                ul.appendChild(li);
            }
            detail.appendChild(ul);
        }
    }

    _renderEvaluations(panel, evaluations) {
        const section = document.createElement('div');
        section.className = 'spec-eval-panel';
        section.innerHTML = '<div class="spec-eval-heading">Drift Evaluations</div>';

        for (const evalRecord of evaluations) {
            section.appendChild(this._createEvalCard(evalRecord));
        }

        panel.appendChild(section);
    }

    _createEvalCard(evalRecord) {
        const card = document.createElement('div');
        card.className = 'spec-eval-card';

        const score = typeof evalRecord.overall_score === 'number' ? evalRecord.overall_score : 0;
        const scoreClass = score >= 3.0 ? 'good' : score >= 2.0 ? 'warn' : 'bad';
        const driftClass = evalRecord.drift_detected ? 'drift' : 'clean';
        const driftLabel = evalRecord.drift_detected ? 'Drift Detected' : 'No Drift';

        const header = document.createElement('div');
        header.className = 'spec-eval-card-header';
        header.innerHTML = `
            <span class="spec-eval-score-badge ${scoreClass}">${score.toFixed(1)}</span>
            <span class="spec-eval-drift-badge ${driftClass}">${driftLabel}</span>
            <span class="spec-eval-meta">Checkpoint ${evalRecord.checkpoint_seq != null ? evalRecord.checkpoint_seq : '--'} &middot; ${evalRecord.evaluator || 'llm'}${evalRecord.fallback ? ' (fallback)' : ''}</span>
        `;
        card.appendChild(header);

        // Per-dimension scores
        const scores = Array.isArray(evalRecord.scores) ? evalRecord.scores : [];
        if (scores.length > 0) {
            const scoresDiv = document.createElement('div');
            scoresDiv.className = 'spec-eval-scores';

            for (const s of scores) {
                const rawScore = typeof s.score === 'number' ? s.score : 0;
                const barClass = rawScore >= 3.0 ? 'good' : rawScore >= 2.0 ? 'warn' : 'bad';
                const pct = Math.min(100, Math.max(0, (rawScore / 5.0) * 100));

                const row = document.createElement('div');
                row.className = 'spec-eval-score-row';
                row.innerHTML = `
                    <span class="spec-eval-score-dim" title="${_escapeHtml(s.reasoning || '')}">${_escapeHtml(s.dimension || '')}</span>
                    <span class="spec-eval-score-bar-track"><span class="spec-eval-score-bar-fill ${barClass}" style="width: ${pct}%"></span></span>
                    <span class="spec-eval-score-numeric">${rawScore.toFixed(1)}</span>
                `;
                scoresDiv.appendChild(row);
            }
            card.appendChild(scoresDiv);
        }

        if (evalRecord.summary) {
            const summary = document.createElement('div');
            summary.className = 'spec-eval-summary';
            summary.textContent = evalRecord.summary;
            card.appendChild(summary);
        }

        return card;
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
