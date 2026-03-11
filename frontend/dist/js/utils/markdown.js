/**
 * utils/markdown.js
 * Configures marked.js to use highlight.js for syntax highlighting.
 */
import { showToast } from './feedback.js';

let markdownInteractionsBound = false;

marked.setOptions({
    gfm: true,
    breaks: true,
    highlight: function (code, lang) {
        const language = hljs.getLanguage(lang) ? lang : 'plaintext';
        return hljs.highlight(code, { language }).value;
    }
});

export function parseMarkdown(source = '') {
    ensureMarkdownInteractions();
    const rendered = marked.parse(String(source));
    const template = document.createElement('template');
    template.innerHTML = rendered;

    template.content.querySelectorAll('pre').forEach(pre => {
        if (pre.parentElement?.classList.contains('markdown-code-block')) return;
        const code = pre.querySelector('code');
        if (!code) return;
        const wrapper = document.createElement('div');
        wrapper.className = 'markdown-code-block';
        const language = extractCodeLanguage(code);
        wrapper.dataset.language = language;

        const header = document.createElement('div');
        header.className = 'markdown-code-header';

        const label = document.createElement('span');
        label.className = 'markdown-code-language';
        label.textContent = formatCodeLanguage(language);
        header.appendChild(label);

        const copyButton = document.createElement('button');
        copyButton.type = 'button';
        copyButton.className = 'markdown-code-copy';
        copyButton.dataset.copyLabel = 'Copy';
        copyButton.dataset.copiedLabel = 'Copied';
        copyButton.textContent = 'Copy';
        copyButton.setAttribute('aria-label', `Copy ${formatCodeLanguage(language)} code`);
        header.appendChild(copyButton);

        pre.parentNode?.insertBefore(wrapper, pre);
        wrapper.appendChild(header);
        wrapper.appendChild(pre);
    });

    template.content.querySelectorAll('table').forEach(table => {
        if (table.parentElement?.classList.contains('markdown-table-wrap')) return;
        const wrapper = document.createElement('div');
        wrapper.className = 'markdown-table-wrap';
        table.parentNode?.insertBefore(wrapper, table);
        wrapper.appendChild(table);
    });

    return template.innerHTML;
}

function extractCodeLanguage(codeElement) {
    const classNames = String(codeElement.className || '').split(/\s+/);
    const languageClass = classNames.find(className => className.startsWith('language-'));
    if (!languageClass) return 'text';
    const language = languageClass.slice('language-'.length).trim().toLowerCase();
    return language || 'text';
}

function formatCodeLanguage(language) {
    if (language === 'plaintext' || language === 'text') return 'Text';
    if (language === 'javascript') return 'JavaScript';
    if (language === 'typescript') return 'TypeScript';
    if (language === 'shell' || language === 'bash' || language === 'sh') return 'Shell';
    if (language === 'json') return 'JSON';
    if (language === 'yaml') return 'YAML';
    if (language === 'python') return 'Python';
    return language.charAt(0).toUpperCase() + language.slice(1);
}

function ensureMarkdownInteractions() {
    if (markdownInteractionsBound || typeof document === 'undefined') return;
    document.addEventListener('click', event => {
        const button = event.target instanceof Element
            ? event.target.closest('.markdown-code-copy')
            : null;
        if (!(button instanceof HTMLButtonElement)) return;
        void handleCopyCodeBlock(button);
    });
    markdownInteractionsBound = true;
}

async function handleCopyCodeBlock(button) {
    const codeBlock = button.closest('.markdown-code-block');
    const codeEl = codeBlock?.querySelector('pre code');
    const copyText = codeEl ? String(codeEl.textContent || '').trimEnd() : '';
    if (!copyText) {
        showToast({
            title: 'Copy Failed',
            message: 'No code content was found in this block.',
            tone: 'warning',
        });
        return;
    }

    try {
        await navigator.clipboard.writeText(copyText);
        indicateCopySuccess(button);
        showToast({
            title: 'Code Copied',
            message: 'The code block has been copied to your clipboard.',
            tone: 'success',
            durationMs: 1800,
        });
    } catch (error) {
        showToast({
            title: 'Copy Failed',
            message: 'Clipboard access is not available right now.',
            tone: 'danger',
        });
    }
}

function indicateCopySuccess(button) {
    const defaultLabel = button.dataset.copyLabel || 'Copy';
    const copiedLabel = button.dataset.copiedLabel || 'Copied';
    button.textContent = copiedLabel;
    button.classList.add('is-copied');
    window.setTimeout(() => {
        button.textContent = defaultLabel;
        button.classList.remove('is-copied');
    }, 1400);
}
