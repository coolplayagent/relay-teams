/**
 * utils/markdown.js
 * Configures marked.js to use highlight.js for syntax highlighting.
 */

marked.setOptions({
    // Enable GitHub flavored markdown
    gfm: true,
    // Enable tables
    breaks: true,
    highlight: function (code, lang) {
        const language = hljs.getLanguage(lang) ? lang : 'plaintext';
        return hljs.highlight(code, { language }).value;
    }
});

export const parseMarkdown = marked.parse;
