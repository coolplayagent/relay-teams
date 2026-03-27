
/**
 * components/observability.js
 */
import { fetchObservabilityBreakdowns, fetchObservabilityOverview } from '../core/api.js';
import { state } from '../core/state.js';
import { hideProjectView } from './projectView.js';
import { getCurrentLanguage, t } from '../utils/i18n.js';
import { sysLog } from '../utils/logger.js';

let currentScope = 'global';
const chartInstances = new Map();
const CHART_LABEL_COLOR = '#cbd5e1';
const CHART_MUTED_COLOR = '#94a3b8';
const CHART_GRID_COLOR = 'rgba(148, 163, 184, 0.14)';

export function initializeObservability() {
    const button = document.getElementById('observability-btn');
    const backButton = document.getElementById('observability-back-btn');
    const globalButton = document.getElementById('observability-global-btn');
    const sessionButton = document.getElementById('observability-session-btn');
    const sessionLabel = document.getElementById('observability-session-label');

    if (button) {
        button.onclick = () => {
            const nextVisible = !isVisible();
            if (nextVisible) {
                currentScope = state.currentSessionId ? 'session' : 'global';
            }
            setVisible(nextVisible);
            if (nextVisible) {
                renderScopeState();
                void refreshObservability();
            }
        };
    }
    if (backButton) {
        backButton.onclick = () => setVisible(false);
    }
    if (globalButton) {
        globalButton.onclick = () => {
            currentScope = 'global';
            renderScopeState();
            void refreshObservability();
        };
    }
    if (sessionButton) {
        sessionButton.onclick = () => {
            if (!state.currentSessionId) {
                return;
            }
            currentScope = 'session';
            renderScopeState();
            void refreshObservability();
        };
    }
    if (sessionLabel) {
        sessionLabel.onclick = () => {
            if (!state.currentSessionId) {
                return;
            }
            focusCurrentSessionMainView();
        };
    }

    document.addEventListener('agent-teams-session-selected', () => {
        renderScopeState();
        if (isVisible()) {
            void refreshObservability();
        }
    });
    document.addEventListener('agent-teams-language-changed', () => {
        renderScopeState();
        if (isVisible()) {
            void refreshObservability();
        }
    });
    window.addEventListener('resize', handleWindowResize);

    renderScopeState();
}

export async function refreshObservability() {
    const safeScope = currentScope === 'session' && state.currentSessionId ? 'session' : 'global';
    const scopeId = safeScope === 'session' ? state.currentSessionId : '';
    renderLoadingState();
    try {
        const [overview, breakdowns] = await Promise.all([
            fetchObservabilityOverview({ scope: safeScope, scopeId, timeWindowMinutes: 1440 }),
            fetchObservabilityBreakdowns({ scope: safeScope, scopeId, timeWindowMinutes: 1440 }),
        ]);
        renderOverview(overview, breakdowns, safeScope);
        renderBreakdowns(breakdowns, overview);
    } catch (error) {
        renderErrorState(String(error?.message || t('observability.load_failed')));
        sysLog(`Failed to load observability metrics: ${error?.message || error}`, 'log-error');
    }
}

function renderLoadingState() {
    destroyCharts();
    renderPlaceholder('observability-overview', t('observability.loading'));
    renderPlaceholder('observability-trends', t('observability.loading'));
    renderPlaceholder('observability-breakdowns', t('observability.loading'));
}

function renderErrorState(message) {
    destroyCharts();
    renderPlaceholder('observability-overview', message);
    renderPlaceholder('observability-trends', t('observability.load_failed'));
    renderPlaceholder('observability-breakdowns', t('observability.load_failed'));
}

function renderPlaceholder(id, message) {
    const host = document.getElementById(id);
    if (!host) {
        return;
    }
    host.innerHTML = `<div class="observability-empty">${escapeHtml(message)}</div>`;
}

function renderOverview(payload, breakdownPayload, safeScope) {
    const host = document.getElementById('observability-overview');
    const trendsHost = document.getElementById('observability-trends');
    if (!host || !trendsHost) {
        return;
    }

    const kpis = payload?.kpis || {};
    const trends = Array.isArray(payload?.trends) ? payload.trends : [];
    const rows = Array.isArray(breakdownPayload?.rows) ? breakdownPayload.rows : [];

    host.innerHTML = `
        <section class="observability-summary-card">
            <div>
                <div class="observability-summary-eyebrow">${escapeHtml(t(`observability.scope.${safeScope}`))}</div>
                <h4 class="observability-summary-title">${escapeHtml(t('observability.summary_title'))}</h4>
                <p class="observability-summary-copy">${escapeHtml(resolveUpdatedAtCopy(payload?.updated_at))}</p>
            </div>
            <div class="observability-summary-pill">${escapeHtml(t('observability.window_24h'))}</div>
        </section>
        <section class="observability-metric-grid">
            ${buildMetricChartCard('observability-metric-steps-chart', t('observability.kpi.steps'), formatNumber(kpis.steps), t('observability.metric.note.steps'))}
            ${buildMetricChartCard('observability-metric-input-chart', t('observability.kpi.input_tokens'), formatCompactNumber(kpis.input_tokens), t('observability.metric.note.input_tokens'))}
            ${buildMetricChartCard('observability-metric-cached-input-chart', t('observability.kpi.cached_input_tokens'), formatCompactNumber(kpis.cached_input_tokens), t('observability.metric.note.cached_input_tokens'))}
            ${buildMetricChartCard('observability-metric-uncached-input-chart', t('observability.kpi.uncached_input_tokens'), formatCompactNumber(kpis.uncached_input_tokens), t('observability.metric.note.uncached_input_tokens'))}
            ${buildMetricChartCard('observability-metric-output-chart', t('observability.kpi.output_tokens'), formatCompactNumber(kpis.output_tokens), t('observability.metric.note.output_tokens'))}
            ${buildMetricChartCard('observability-metric-tool-calls-chart', t('observability.kpi.tool_calls'), formatNumber(kpis.tool_calls), t('observability.metric.note.tool_calls'))}
            ${buildMetricChartCard('observability-metric-cached-chart', t('observability.kpi.cached_ratio'), formatPercent(kpis.cached_token_ratio), t('observability.metric.note.cached_ratio'))}
            ${buildMetricChartCard('observability-metric-success-chart', t('observability.kpi.tool_success'), formatPercent(kpis.tool_success_rate), t('observability.metric.note.tool_success'))}
            ${buildMetricChartCard('observability-metric-duration-chart', t('observability.kpi.avg_tool_ms'), formatNumber(kpis.tool_avg_duration_ms), t('observability.metric.note.avg_duration'))}
            ${buildMetricChartCard('observability-metric-retrieval-searches-chart', t('observability.kpi.retrieval_searches'), formatNumber(kpis.retrieval_searches), t('observability.metric.note.retrieval_searches'))}
            ${buildMetricChartCard('observability-metric-retrieval-failures-chart', t('observability.kpi.retrieval_failure_rate'), formatPercent(kpis.retrieval_failure_rate), t('observability.metric.note.retrieval_failure_rate'))}
            ${buildMetricChartCard('observability-metric-retrieval-duration-chart', t('observability.kpi.avg_retrieval_ms'), formatNumber(kpis.retrieval_avg_duration_ms), t('observability.metric.note.avg_retrieval_ms'))}
            ${buildMetricChartCard('observability-metric-retrieval-documents-chart', t('observability.kpi.retrieval_document_count'), formatNumber(kpis.retrieval_document_count), t('observability.metric.note.retrieval_document_count'))}
            ${buildMetricChartCard('observability-metric-integrations-chart', `${t('observability.kpi.skill_calls')} / ${t('observability.kpi.mcp_calls')}`, `${formatNumber(kpis.skill_calls)} / ${formatNumber(kpis.mcp_calls)}`, `${t('observability.source.skill')} + ${t('observability.source.mcp')}`)}
        </section>
    `;

    renderMetricCharts({ kpis, trends, rows, safeScope });

    trendsHost.innerHTML = `
        <section class="observability-section-card">
            <div class="observability-section-heading">
                <div>
                    <h4>${escapeHtml(t('observability.section.trends'))}</h4>
                    <p>${escapeHtml(t('observability.section.trends_copy'))}</p>
                </div>
            </div>
            ${trends.length === 0 ? `<div class="observability-empty">${escapeHtml(t('observability.trends.empty'))}</div>` : `
                <div class="observability-chart-grid">
                    ${buildChartCard('observability-steps-chart', t('observability.trends.steps'), resolveBucketRange(trends))}
                    ${buildChartCard('observability-input-chart', t('observability.kpi.input_tokens'), resolveBucketRange(trends))}
                    ${buildChartCard('observability-output-chart', t('observability.kpi.output_tokens'), resolveBucketRange(trends))}
                    ${buildChartCard('observability-tools-chart', t('observability.trends.tools'), resolveBucketRange(trends))}
                </div>
            `}
        </section>
    `;

    if (trends.length > 0) {
        renderTrendCharts(trends);
    }
}
function renderBreakdowns(payload, overview) {
    const host = document.getElementById('observability-breakdowns');
    if (!host) {
        return;
    }

    const rows = Array.isArray(payload?.rows) ? payload.rows.slice(0, 8) : [];
    const roleRows = Array.isArray(payload?.role_rows) ? payload.role_rows.slice(0, 8) : [];
    host.innerHTML = `
        <section class="observability-section-card">
            <div class="observability-section-heading">
                <div>
                    <h4>${escapeHtml(t('observability.section.breakdowns'))}</h4>
                    <p>${escapeHtml(t('observability.section.breakdowns_copy'))}</p>
                </div>
            </div>
            ${rows.length === 0 ? `<div class="observability-empty">${escapeHtml(t('observability.breakdowns.empty'))}</div>` : `
                <div class="observability-breakdown-grid">
                    ${buildChartCard('observability-breakdown-calls-chart', t('observability.breakdowns.chart_title'), resolveUpdatedAtCopy(overview?.updated_at), 'observability-breakdown-stage')}
                    ${buildChartCard('observability-breakdown-success-chart', t('observability.breakdowns.success_chart_title'), resolveUpdatedAtCopy(overview?.updated_at), 'observability-breakdown-stage')}
                    ${buildChartCard('observability-breakdown-duration-chart', t('observability.breakdowns.duration_chart_title'), resolveUpdatedAtCopy(overview?.updated_at), 'observability-breakdown-stage')}
                    ${buildChartCard('observability-breakdown-source-chart', t('observability.breakdowns.source_chart_title'), resolveUpdatedAtCopy(overview?.updated_at), 'observability-breakdown-stage')}
                </div>
            `}
        </section>
        <section class="observability-section-card">
            <div class="observability-section-heading">
                <div>
                    <h4>${escapeHtml(t('observability.section.role_breakdowns'))}</h4>
                    <p>${escapeHtml(t('observability.section.role_breakdowns_copy'))}</p>
                </div>
            </div>
            ${roleRows.length === 0 ? `<div class="observability-empty">${escapeHtml(t('observability.role_breakdowns.empty'))}</div>` : `
                <div class="observability-breakdown-grid">
                    ${buildChartCard('observability-role-breakdown-input-chart', t('observability.role_breakdowns.input_chart_title'), resolveUpdatedAtCopy(overview?.updated_at), 'observability-breakdown-stage')}
                    ${buildChartCard('observability-role-breakdown-cache-chart', t('observability.role_breakdowns.cache_chart_title'), resolveUpdatedAtCopy(overview?.updated_at), 'observability-breakdown-stage')}
                    ${buildChartCard('observability-role-breakdown-failures-chart', t('observability.role_breakdowns.failures_chart_title'), resolveUpdatedAtCopy(overview?.updated_at), 'observability-breakdown-stage')}
                </div>
            `}
        </section>
    `;

    if (rows.length > 0) {
        renderBreakdownCharts(rows);
    }
    if (roleRows.length > 0) {
        renderRoleBreakdownCharts(roleRows);
    }
}

function buildChartCard(id, title, axisLabel, stageClass = '') {
    return `
        <article class="observability-chart-card">
            <div class="observability-chart-header">
                <h5>${escapeHtml(title)}</h5>
                <div class="observability-chart-axis">${escapeHtml(axisLabel)}</div>
            </div>
            <div class="observability-chart-stage ${escapeHtml(stageClass)}">
                <canvas id="${escapeHtml(id)}"></canvas>
            </div>
        </article>
    `;
}

function buildMetricChartCard(id, title, value, note) {
    return `
        <article class="observability-chart-card observability-metric-chart-card">
            <div class="observability-chart-header observability-metric-chart-header">
                <div>
                    <h5>${escapeHtml(title)}</h5>
                    <div class="observability-metric-chart-value">${escapeHtml(value)}</div>
                </div>
                <div class="observability-chart-axis">${escapeHtml(note)}</div>
            </div>
            <div class="observability-chart-stage observability-metric-chart-stage">
                <canvas id="${escapeHtml(id)}"></canvas>
            </div>
        </article>
    `;
}

function renderMetricCharts({ kpis, trends, rows, safeScope }) {
    const ChartCtor = getChartConstructor();
    const ids = [
        'observability-metric-steps-chart',
        'observability-metric-input-chart',
        'observability-metric-cached-input-chart',
        'observability-metric-uncached-input-chart',
        'observability-metric-output-chart',
        'observability-metric-tool-calls-chart',
        'observability-metric-cached-chart',
        'observability-metric-success-chart',
        'observability-metric-duration-chart',
        'observability-metric-retrieval-searches-chart',
        'observability-metric-retrieval-failures-chart',
        'observability-metric-retrieval-duration-chart',
        'observability-metric-retrieval-documents-chart',
        'observability-metric-integrations-chart',
    ];
    if (!ChartCtor) {
        showChartUnavailable(ids);
        return;
    }

    const trendLabels = Array.isArray(trends) && trends.length > 0
        ? trends.map(row => formatBucketLabel(row.bucket_start))
        : [];
    const scopeLabel = t(`observability.scope.${safeScope}`);
    const durationMax = Math.max(
        Number(kpis.tool_avg_duration_ms || 0),
        ...rows.map(row => Number(row.avg_duration_ms || 0)),
        1000,
    );
    const retrievalDurationMax = Math.max(Number(kpis.retrieval_avg_duration_ms || 0), 1000);

    createSeriesMetricChart('observability-metric-steps-chart', {
        labels: trendLabels,
        values: trends.map(row => Number(row.steps || 0)),
        color: [37, 99, 235],
        type: 'line',
        label: t('observability.kpi.steps'),
        scopeLabel,
        yTitle: t('observability.kpi.steps'),
        fallbackValue: Number(kpis.steps || 0),
    });
    createSeriesMetricChart('observability-metric-input-chart', {
        labels: trendLabels,
        values: trends.map(row => Number(row.input_tokens || 0)),
        color: [13, 148, 136],
        type: 'line',
        label: t('observability.kpi.input_tokens'),
        scopeLabel,
        yTitle: t('observability.kpi.input_tokens'),
        fallbackValue: Number(kpis.input_tokens || 0),
    });
    createChart('observability-metric-cached-input-chart', buildSingleMetricBarChartConfig({
        label: t('observability.kpi.cached_input_tokens'),
        categoryLabel: scopeLabel,
        value: Number(kpis.cached_input_tokens || 0),
        color: [8, 145, 178],
        xTitle: resolveScopeAxisTitle(),
        yTitle: t('observability.kpi.cached_input_tokens'),
        tickMode: 'compact',
    }));
    createChart('observability-metric-uncached-input-chart', buildSingleMetricBarChartConfig({
        label: t('observability.kpi.uncached_input_tokens'),
        categoryLabel: scopeLabel,
        value: Number(kpis.uncached_input_tokens || 0),
        color: [225, 29, 72],
        xTitle: resolveScopeAxisTitle(),
        yTitle: t('observability.kpi.uncached_input_tokens'),
        tickMode: 'compact',
    }));
    createSeriesMetricChart('observability-metric-output-chart', {
        labels: trendLabels,
        values: trends.map(row => Number(row.output_tokens || 0)),
        color: [217, 119, 6],
        type: 'line',
        label: t('observability.kpi.output_tokens'),
        scopeLabel,
        yTitle: t('observability.kpi.output_tokens'),
        fallbackValue: Number(kpis.output_tokens || 0),
    });
    createSeriesMetricChart('observability-metric-tool-calls-chart', {
        labels: trendLabels,
        values: trends.map(row => Number(row.tool_calls || 0)),
        color: [124, 58, 237],
        type: 'bar',
        label: t('observability.kpi.tool_calls'),
        scopeLabel,
        yTitle: t('observability.kpi.tool_calls'),
        fallbackValue: Number(kpis.tool_calls || 0),
    });
    createChart('observability-metric-cached-chart', buildSingleMetricBarChartConfig({
        label: t('observability.kpi.cached_ratio'),
        categoryLabel: scopeLabel,
        value: Number(kpis.cached_token_ratio || 0) * 100,
        color: [124, 58, 237],
        xTitle: resolveScopeAxisTitle(),
        yTitle: resolvePercentageAxisTitle(),
        maxValue: 100,
        tickMode: 'percentage',
    }));
    createChart('observability-metric-success-chart', buildSingleMetricBarChartConfig({
        label: t('observability.kpi.tool_success'),
        categoryLabel: scopeLabel,
        value: Number(kpis.tool_success_rate || 0) * 100,
        color: [22, 163, 74],
        xTitle: resolveScopeAxisTitle(),
        yTitle: resolvePercentageAxisTitle(),
        maxValue: 100,
        tickMode: 'percentage',
    }));
    createChart('observability-metric-duration-chart', buildSingleMetricBarChartConfig({
        label: t('observability.kpi.avg_tool_ms'),
        categoryLabel: scopeLabel,
        value: Number(kpis.tool_avg_duration_ms || 0),
        color: [225, 29, 72],
        xTitle: resolveScopeAxisTitle(),
        yTitle: resolveDurationAxisTitle(),
        maxValue: durationMax,
        tickMode: 'number',
    }));
    createChart('observability-metric-retrieval-searches-chart', buildSingleMetricBarChartConfig({
        label: t('observability.kpi.retrieval_searches'),
        categoryLabel: scopeLabel,
        value: Number(kpis.retrieval_searches || 0),
        color: [14, 116, 144],
        xTitle: resolveScopeAxisTitle(),
        yTitle: t('observability.kpi.retrieval_searches'),
        tickMode: 'compact',
    }));
    createChart('observability-metric-retrieval-failures-chart', buildSingleMetricBarChartConfig({
        label: t('observability.kpi.retrieval_failure_rate'),
        categoryLabel: scopeLabel,
        value: Number(kpis.retrieval_failure_rate || 0) * 100,
        color: [190, 24, 93],
        xTitle: resolveScopeAxisTitle(),
        yTitle: t('observability.kpi.retrieval_failure_rate'),
        maxValue: 100,
        tickMode: 'percentage',
    }));
    createChart('observability-metric-retrieval-duration-chart', buildSingleMetricBarChartConfig({
        label: t('observability.kpi.avg_retrieval_ms'),
        categoryLabel: scopeLabel,
        value: Number(kpis.retrieval_avg_duration_ms || 0),
        color: [124, 58, 237],
        xTitle: resolveScopeAxisTitle(),
        yTitle: t('observability.kpi.avg_retrieval_ms'),
        maxValue: retrievalDurationMax,
        tickMode: 'number',
    }));
    createChart('observability-metric-retrieval-documents-chart', buildSingleMetricBarChartConfig({
        label: t('observability.kpi.retrieval_document_count'),
        categoryLabel: scopeLabel,
        value: Number(kpis.retrieval_document_count || 0),
        color: [22, 163, 74],
        xTitle: resolveScopeAxisTitle(),
        yTitle: t('observability.kpi.retrieval_document_count'),
        tickMode: 'compact',
    }));
    createChart('observability-metric-integrations-chart', buildGroupedMetricBarChartConfig({
        labels: [t('observability.kpi.skill_calls'), t('observability.kpi.mcp_calls')],
        values: [Number(kpis.skill_calls || 0), Number(kpis.mcp_calls || 0)],
        colors: [[8, 145, 178], [79, 70, 229]],
        datasetLabel: `${t('observability.kpi.skill_calls')} / ${t('observability.kpi.mcp_calls')}`,
        xTitle: resolveSourceAxisTitle(),
        yTitle: resolveCallsAxisTitle(),
    }));
}
function renderTrendCharts(trends) {
    const ChartCtor = getChartConstructor();
    if (!ChartCtor) {
        showChartUnavailable([
            'observability-steps-chart',
            'observability-input-chart',
            'observability-output-chart',
            'observability-tools-chart',
        ]);
        return;
    }

    const labels = trends.map(row => formatBucketLabel(row.bucket_start));
    createChart('observability-steps-chart', buildLineChartConfig({
        labels,
        datasets: [buildLineDataset(t('observability.legend.steps'), trends.map(row => Number(row.steps || 0)), [37, 99, 235])],
        xTitle: resolveTimeAxisTitle(),
        yTitle: t('observability.kpi.steps'),
    }));
    createChart('observability-input-chart', buildLineChartConfig({
        labels,
        datasets: [buildLineDataset(t('observability.legend.input_tokens'), trends.map(row => Number(row.input_tokens || 0)), [13, 148, 136])],
        xTitle: resolveTimeAxisTitle(),
        yTitle: t('observability.kpi.input_tokens'),
    }));
    createChart('observability-output-chart', buildLineChartConfig({
        labels,
        datasets: [buildLineDataset(t('observability.legend.output_tokens'), trends.map(row => Number(row.output_tokens || 0)), [217, 119, 6])],
        xTitle: resolveTimeAxisTitle(),
        yTitle: t('observability.kpi.output_tokens'),
    }));
    createChart('observability-tools-chart', buildBarChartConfig({
        labels,
        datasets: [{
            label: t('observability.legend.tool_calls'),
            data: trends.map(row => Number(row.tool_calls || 0)),
            backgroundColor: 'rgba(124, 58, 237, 0.84)',
            hoverBackgroundColor: 'rgba(139, 92, 246, 0.96)',
            borderRadius: 8,
            maxBarThickness: 28,
        }],
        xTitle: resolveTimeAxisTitle(),
        yTitle: t('observability.kpi.tool_calls'),
    }));
}

function renderBreakdownCharts(rows) {
    const ChartCtor = getChartConstructor();
    if (!ChartCtor) {
        showChartUnavailable([
            'observability-breakdown-calls-chart',
            'observability-breakdown-success-chart',
            'observability-breakdown-duration-chart',
            'observability-breakdown-source-chart',
        ]);
        return;
    }

    const labels = rows.map(row => String(row.tool_name || 'unknown'));
    createChart('observability-breakdown-calls-chart', buildHorizontalBarChartConfig({
        labels,
        values: rows.map(row => Number(row.calls || 0)),
        seriesLabel: t('observability.metric.calls'),
        colorValues: rows.map((_, index) => pickPalette(index, 0.88)),
        xTitle: resolveCallsAxisTitle(),
        yTitle: resolveToolAxisTitle(),
        tickMode: 'compact',
    }));
    createChart('observability-breakdown-success-chart', buildHorizontalBarChartConfig({
        labels,
        values: rows.map(row => Number(row.success_rate || 0) * 100),
        seriesLabel: t('observability.metric.success'),
        colorValues: rows.map((_, index) => pickPalette(index + 2, 0.84)),
        xTitle: resolvePercentageAxisTitle(),
        yTitle: resolveToolAxisTitle(),
        tickMode: 'percentage',
        maxValue: 100,
    }));
    createChart('observability-breakdown-duration-chart', buildHorizontalBarChartConfig({
        labels,
        values: rows.map(row => Number(row.avg_duration_ms || 0)),
        seriesLabel: t('observability.metric.avg_duration'),
        colorValues: rows.map((_, index) => pickPalette(index + 4, 0.84)),
        xTitle: resolveDurationAxisTitle(),
        yTitle: resolveToolAxisTitle(),
        tickMode: 'number',
    }));
    createChart('observability-breakdown-source-chart', buildGroupedMetricBarChartConfig({
        labels: rows.reduce((accumulator, row) => {
            const nextLabel = resolveSourceLabel(row.tool_source);
            if (!accumulator.includes(nextLabel)) {
                accumulator.push(nextLabel);
            }
            return accumulator;
        }, []),
        values: buildSourceValues(rows),
        colors: [[37, 99, 235], [8, 145, 178], [124, 58, 237]],
        datasetLabel: t('observability.breakdowns.source_chart_title'),
        xTitle: resolveSourceAxisTitle(),
        yTitle: resolveCallsAxisTitle(),
    }));
}

function renderRoleBreakdownCharts(rows) {
    const ChartCtor = getChartConstructor();
    if (!ChartCtor) {
        showChartUnavailable([
            'observability-role-breakdown-input-chart',
            'observability-role-breakdown-cache-chart',
            'observability-role-breakdown-failures-chart',
        ]);
        return;
    }

    const labels = rows.map(row => resolveRoleLabel(row.role_id));
    createChart('observability-role-breakdown-input-chart', buildHorizontalBarChartConfig({
        labels,
        values: rows.map(row => Number(row.input_tokens || 0)),
        seriesLabel: t('observability.kpi.input_tokens'),
        colorValues: rows.map((_, index) => pickPalette(index, 0.88)),
        xTitle: t('observability.kpi.input_tokens'),
        yTitle: resolveRoleAxisTitle(),
        tickMode: 'compact',
    }));
    createChart('observability-role-breakdown-cache-chart', buildHorizontalBarChartConfig({
        labels,
        values: rows.map(row => Number(row.cached_token_ratio || 0) * 100),
        seriesLabel: t('observability.kpi.cached_ratio'),
        colorValues: rows.map((_, index) => pickPalette(index + 2, 0.84)),
        xTitle: resolvePercentageAxisTitle(),
        yTitle: resolveRoleAxisTitle(),
        tickMode: 'percentage',
        maxValue: 100,
    }));
    createChart('observability-role-breakdown-failures-chart', buildHorizontalBarChartConfig({
        labels,
        values: rows.map(row => Number(row.tool_failures || 0)),
        seriesLabel: t('observability.metric.failures'),
        colorValues: rows.map((_, index) => pickPalette(index + 4, 0.84)),
        xTitle: t('observability.metric.failures'),
        yTitle: resolveRoleAxisTitle(),
        tickMode: 'compact',
    }));
}

function createSeriesMetricChart(id, { labels, values, color, type, label, scopeLabel, yTitle, fallbackValue }) {
    if (labels.length > 1 && values.length > 1) {
        if (type === 'bar') {
            createChart(id, buildBarChartConfig({
                labels,
                datasets: [{
                    label,
                    data: values,
                    backgroundColor: `rgba(${color.join(', ')}, 0.84)`,
                    hoverBackgroundColor: `rgba(${color.join(', ')}, 0.96)`,
                    borderRadius: 8,
                    maxBarThickness: 28,
                }],
                xTitle: resolveTimeAxisTitle(),
                yTitle,
            }));
            return;
        }
        createChart(id, buildLineChartConfig({
            labels,
            datasets: [buildLineDataset(label, values, color)],
            xTitle: resolveTimeAxisTitle(),
            yTitle,
        }));
        return;
    }
    createChart(id, buildSingleMetricBarChartConfig({
        label,
        categoryLabel: scopeLabel,
        value: fallbackValue,
        color,
        xTitle: resolveScopeAxisTitle(),
        yTitle,
        tickMode: 'compact',
    }));
}

function buildLineDataset(label, data, color) {
    return {
        label,
        data,
        borderColor: `rgba(${color.join(', ')}, 0.96)`,
        backgroundColor(context) {
            const chart = context.chart;
            const area = chart.chartArea;
            if (!area) {
                return `rgba(${color.join(', ')}, 0.18)`;
            }
            const gradient = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
            gradient.addColorStop(0, `rgba(${color.join(', ')}, 0.34)`);
            gradient.addColorStop(1, `rgba(${color.join(', ')}, 0.03)`);
            return gradient;
        },
        fill: true,
        tension: 0.36,
        borderWidth: 2.5,
        pointRadius: 0,
        pointHoverRadius: 4,
    };
}

function buildLineChartConfig({ labels, datasets, xTitle, yTitle }) {
    return {
        type: 'line',
        data: { labels, datasets },
        options: buildCartesianOptions({
            xScale: buildCategoryScale({ title: xTitle, maxRotation: 0 }),
            yScale: buildNumericScale({ title: yTitle, mode: 'compact' }),
            showLegend: datasets.length > 1,
        }),
    };
}

function buildBarChartConfig({ labels, datasets, xTitle, yTitle }) {
    return {
        type: 'bar',
        data: { labels, datasets },
        options: buildCartesianOptions({
            xScale: buildCategoryScale({ title: xTitle, maxRotation: 0 }),
            yScale: buildNumericScale({ title: yTitle, mode: 'compact' }),
            showLegend: false,
        }),
    };
}
function buildSingleMetricBarChartConfig({ label, categoryLabel, value, color, xTitle, yTitle, maxValue = null, tickMode }) {
    return {
        type: 'bar',
        data: {
            labels: [categoryLabel],
            datasets: [{
                label,
                data: [value],
                backgroundColor: `rgba(${color.join(', ')}, 0.84)`,
                hoverBackgroundColor: `rgba(${color.join(', ')}, 0.96)`,
                borderRadius: 10,
                maxBarThickness: 40,
            }],
        },
        options: buildCartesianOptions({
            xScale: buildCategoryScale({ title: xTitle, maxRotation: 0 }),
            yScale: buildNumericScale({ title: yTitle, mode: tickMode, maxValue }),
            showLegend: false,
        }),
    };
}

function buildGroupedMetricBarChartConfig({ labels, values, colors, datasetLabel, xTitle, yTitle }) {
    return {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: datasetLabel,
                data: values,
                backgroundColor: values.map((_, index) => `rgba(${colors[index % colors.length].join(', ')}, 0.86)`),
                hoverBackgroundColor: values.map((_, index) => `rgba(${colors[index % colors.length].join(', ')}, 0.98)`),
                borderRadius: 10,
                maxBarThickness: 44,
            }],
        },
        options: buildCartesianOptions({
            xScale: buildCategoryScale({ title: xTitle, maxRotation: 0 }),
            yScale: buildNumericScale({ title: yTitle, mode: 'compact' }),
            showLegend: false,
        }),
    };
}

function buildHorizontalBarChartConfig({ labels, values, seriesLabel, colorValues, xTitle, yTitle, tickMode, maxValue = null }) {
    return {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: seriesLabel,
                data: values,
                backgroundColor: colorValues,
                borderRadius: 10,
                borderSkipped: false,
            }],
        },
        options: buildCartesianOptions({
            indexAxis: 'y',
            xScale: buildNumericScale({ title: xTitle, mode: tickMode, maxValue }),
            yScale: buildCategoryScale({ title: yTitle, maxRotation: 0 }),
            showLegend: false,
        }),
    };
}

function buildCartesianOptions({ xScale, yScale, showLegend, indexAxis = 'x' }) {
    return {
        indexAxis,
        maintainAspectRatio: false,
        responsive: true,
        animation: { duration: 500 },
        interaction: { intersect: false, mode: 'index' },
        plugins: {
            legend: {
                display: showLegend,
                position: 'bottom',
                labels: {
                    color: CHART_LABEL_COLOR,
                    usePointStyle: true,
                    boxWidth: 10,
                    boxHeight: 10,
                    padding: 16,
                },
            },
            tooltip: {
                callbacks: {
                    label(context) {
                        return `${context.dataset.label}: ${formatAxisValue(context.raw, resolveTooltipMode(context.chart.options.indexAxis, yScale, xScale))}`;
                    },
                },
            },
        },
        scales: {
            x: xScale,
            y: yScale,
        },
    };
}

function resolveTooltipMode(indexAxis, yScale, xScale) {
    if (indexAxis === 'y') {
        return xScale.metricMode || 'compact';
    }
    return yScale.metricMode || 'compact';
}

function buildCategoryScale({ title, maxRotation }) {
    return {
        title: {
            display: true,
            text: title,
            color: CHART_MUTED_COLOR,
            font: { size: 11, weight: '600' },
        },
        grid: { display: false },
        ticks: {
            color: CHART_MUTED_COLOR,
            font: { size: 11 },
            maxRotation,
            minRotation: 0,
        },
        border: { display: false },
    };
}

function buildNumericScale({ title, mode, maxValue = null }) {
    return {
        metricMode: mode,
        beginAtZero: true,
        max: maxValue,
        title: {
            display: true,
            text: title,
            color: CHART_MUTED_COLOR,
            font: { size: 11, weight: '600' },
        },
        grid: {
            color: CHART_GRID_COLOR,
            drawBorder: false,
        },
        ticks: {
            color: CHART_MUTED_COLOR,
            font: { size: 11 },
            callback(value) {
                return formatAxisValue(value, mode);
            },
        },
        border: { display: false },
    };
}

function buildSourceValues(rows) {
    const totals = new Map();
    rows.forEach(row => {
        const source = resolveSourceLabel(row.tool_source);
        totals.set(source, (totals.get(source) || 0) + Number(row.calls || 0));
    });
    return Array.from(totals.values());
}

function getChartConstructor() {
    if (typeof window === 'undefined' || typeof window.Chart !== 'function') {
        return null;
    }
    return window.Chart;
}

function createChart(id, config) {
    const ChartCtor = getChartConstructor();
    const canvas = document.getElementById(id);
    if (!ChartCtor || !(canvas instanceof HTMLCanvasElement)) {
        return;
    }
    destroyChart(id);
    const context = canvas.getContext('2d');
    if (!context) {
        return;
    }
    const instance = new ChartCtor(context, config);
    chartInstances.set(id, instance);
}

function destroyChart(id) {
    const chart = chartInstances.get(id);
    if (chart) {
        chart.destroy();
        chartInstances.delete(id);
    }
}

function destroyCharts() {
    chartInstances.forEach(chart => chart.destroy());
    chartInstances.clear();
}

function showChartUnavailable(ids) {
    ids.forEach(id => {
        const canvas = document.getElementById(id);
        const stage = canvas?.parentElement;
        if (stage) {
            stage.innerHTML = `<div class="observability-chart-notice">${escapeHtml(t('observability.chart_library_missing'))}</div>`;
        }
    });
}

function handleWindowResize() {
    chartInstances.forEach(chart => chart.resize());
}

function pickPalette(index, alpha) {
    const colors = [
        [37, 99, 235],
        [15, 118, 110],
        [217, 119, 6],
        [124, 58, 237],
        [225, 29, 72],
        [8, 145, 178],
        [79, 70, 229],
        [22, 163, 74],
    ];
    const [r, g, b] = colors[index % colors.length];
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
function setVisible(visible) {
    const view = document.getElementById('observability-view');
    const chat = document.querySelector('.chat-container');
    const button = document.getElementById('observability-btn');
    const body = document.body;
    if (visible && state.currentMainView === 'project') {
        hideProjectView();
        document.querySelectorAll('.project-title-btn.is-active').forEach(button => {
            button.classList.remove('is-active');
            button.setAttribute('aria-current', 'false');
        });
    }
    if (view) {
        view.style.display = visible ? 'block' : 'none';
    }
    if (chat) {
        chat.style.display = visible ? 'none' : 'flex';
    }
    if (button) {
        button.classList.toggle('active', visible);
    }
    if (body) {
        body.classList.toggle('observability-mode', visible);
    }
    if (!visible) {
        destroyCharts();
    }
}

function isVisible() {
    const view = document.getElementById('observability-view');
    return !!view && view.style.display !== 'none';
}

function focusCurrentSessionMainView() {
    const sessionId = String(state.currentSessionId || '').trim();
    if (!sessionId) {
        return;
    }
    setVisible(false);
    const sessionItem = document.querySelector(`.session-item[data-session-id="${CSS.escape(sessionId)}"]`);
    if (sessionItem instanceof HTMLElement) {
        sessionItem.scrollIntoView({ block: 'nearest' });
    }
}

function renderScopeState() {
    const globalButton = document.getElementById('observability-global-btn');
    const sessionButton = document.getElementById('observability-session-btn');
    const sessionLabel = document.getElementById('observability-session-label');
    const scopeIndicator = document.getElementById('observability-scope-indicator');
    const safeScope = currentScope === 'session' && state.currentSessionId ? 'session' : 'global';
    if (globalButton) {
        const isActive = safeScope === 'global';
        globalButton.classList.toggle('active', isActive);
        globalButton.setAttribute('aria-pressed', String(isActive));
    }
    if (sessionButton) {
        const isActive = safeScope === 'session';
        sessionButton.classList.toggle('active', isActive);
        sessionButton.setAttribute('aria-pressed', String(isActive));
        sessionButton.disabled = !state.currentSessionId;
    }
    if (scopeIndicator) {
        scopeIndicator.textContent = t('observability.scope.' + safeScope);
        scopeIndicator.setAttribute('data-scope', safeScope);
    }
    if (sessionLabel) {
        const hasSession = !!state.currentSessionId;
        sessionLabel.textContent = hasSession
            ? formatTemplate(t('observability.session_label'), { session_id: state.currentSessionId })
            : t('observability.no_session');
        sessionLabel.disabled = !hasSession;
        sessionLabel.title = hasSession ? t('observability.actions.back') : '';
    }
}

function resolveUpdatedAtCopy(updatedAt) {
    if (!updatedAt) {
        return t('observability.updated_pending');
    }
    return formatTemplate(t('observability.updated_at'), { timestamp: formatDateTime(updatedAt) });
}

function resolveBucketRange(trends) {
    if (!Array.isArray(trends) || trends.length === 0) {
        return '';
    }
    if (trends.length === 1) {
        return formatBucketLabel(trends[0].bucket_start);
    }
    return `${formatBucketLabel(trends[0].bucket_start)} - ${formatBucketLabel(trends[trends.length - 1].bucket_start)}`;
}

function resolveSourceLabel(source) {
    if (source === 'skill') return t('observability.source.skill');
    if (source === 'mcp') return t('observability.source.mcp');
    return t('observability.source.local');
}

function resolveRoleLabel(roleId) {
    const normalizedRoleId = String(roleId || '').trim();
    if (!normalizedRoleId || normalizedRoleId === 'unknown') {
        return t('observability.role.unknown');
    }
    return normalizedRoleId;
}

function resolveTimeAxisTitle() {
    return getCurrentLanguage() === 'zh-CN' ? '\u65f6\u95f4' : 'Time';
}

function resolveScopeAxisTitle() {
    return getCurrentLanguage() === 'zh-CN' ? '\u8303\u56f4' : 'Scope';
}

function resolveSourceAxisTitle() {
    return getCurrentLanguage() === 'zh-CN' ? '\u6765\u6e90' : 'Source';
}

function resolveToolAxisTitle() {
    return getCurrentLanguage() === 'zh-CN' ? '\u5de5\u5177' : 'Tool';
}

function resolveRoleAxisTitle() {
    return getCurrentLanguage() === 'zh-CN' ? '\u89d2\u8272' : 'Role';
}

function resolveCallsAxisTitle() { return t('observability.metric.calls'); }

function resolvePercentageAxisTitle() {
    return getCurrentLanguage() === 'zh-CN' ? '\u767e\u5206\u6bd4' : 'Percentage';
}

function resolveDurationAxisTitle() {
    return getCurrentLanguage() === 'zh-CN' ? '\u6beb\u79d2' : 'Milliseconds';
}

function formatTemplate(template, values) {
    return Object.entries(values).reduce((result, [key, value]) => result.replace(`{${key}}`, String(value)), String(template || ''));
}

function formatAxisValue(value, mode) {
    if (mode === 'percentage') {
        return `${formatNumber(value)}%`;
    }
    if (mode === 'number') {
        return formatNumber(value);
    }
    return formatCompactNumber(value);
}

function formatNumber(value) {
    const num = Number(value || 0);
    if (!Number.isFinite(num)) return '0';
    return new Intl.NumberFormat(getCurrentLanguage(), { minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(num);
}

function formatCompactNumber(value) {
    const num = Number(value || 0);
    if (!Number.isFinite(num)) return '0';
    if (Math.abs(num) >= 1000) {
        return new Intl.NumberFormat(getCurrentLanguage(), { notation: 'compact', maximumFractionDigits: 1 }).format(num);
    }
    return formatNumber(num);
}

function formatPercent(value) {
    const num = Number(value || 0);
    if (!Number.isFinite(num)) return '0.0%';
    return `${new Intl.NumberFormat(getCurrentLanguage(), { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(num * 100)}%`;
}

function formatDateTime(value) {
    try { return new Date(value).toLocaleString(getCurrentLanguage()); } catch (_) { return String(value); }
}

function formatBucketLabel(value) {
    if (!value) return '';
    try {
        const date = new Date(value);
        return date.toLocaleTimeString(getCurrentLanguage(), { hour: '2-digit', minute: '2-digit' });
    } catch (_) {
        return String(value);
    }
}

function escapeHtml(value) {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}
