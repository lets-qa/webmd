// Renders the Markdown embedded in #md-source into #content.
// Every rendering path goes through DOMPurify: Markdown files are treated as untrusted.
(() => {
  "use strict";

  const PURIFY_CONFIG = {
    // HTML and SVG only: MathML is rarely written by hand and has a history of mXSS bugs.
    USE_PROFILES: { html: true, svg: true, svgFilters: true },
    // <style> and style="" could restyle the whole page (e.g. fake login overlays);
    // GitHub strips them too. Forms have no legitimate use in a document, and
    // <template> content is inert but would be live if any later code cloned it.
    FORBID_TAGS: ["style", "form", "template"],
    FORBID_ATTR: ["style"],
    // Hand back a DOM fragment rather than a string, so the sanitized tree is
    // inserted as-is and never re-parsed (avoids mutation-XSS).
    RETURN_DOM_FRAGMENT: true,
  };
  // highlight.js output is only ever <span class="hljs-...">text</span>.
  const HIGHLIGHT_CONFIG = { ALLOWED_TAGS: ["span"], ALLOWED_ATTR: ["class"], RETURN_DOM_FRAGMENT: true };

  const source = document.getElementById("md-source");
  const content = document.getElementById("content");
  if (!source || !content) return;

  function fail(message, err) {
    console.error("webmd:", message, err);
    const box = document.createElement("pre");
    box.className = "webmd-error";
    box.textContent = `webmd: ${message}${err ? `\n${err}` : ""}`;
    content.replaceChildren(box);
  }

  if (!window.marked || !window.DOMPurify) {
    // Fail closed: without the sanitizer, never insert rendered HTML.
    fail("could not load the Markdown renderer or sanitizer; showing nothing rather than unsanitized HTML.");
    return;
  }

  let fragment;
  try {
    const markdown = JSON.parse(source.textContent);
    marked.use({ gfm: true });
    fragment = DOMPurify.sanitize(marked.parse(markdown), PURIFY_CONFIG);
  } catch (err) {
    fail("failed to render this file.", err);
    return;
  }
  content.replaceChildren(fragment);

  if (window.hljs) {
    // Don't use hljs.highlightElement(): it writes innerHTML directly, which would be a
    // second, unsanitized rendering path (and is blocked by the page's Trusted Types CSP).
    // Highlight the plain text, then sanitize the result down to <span class> before inserting.
    content.querySelectorAll("pre code").forEach((block) => {
      const code = block.textContent;
      const lang = [...block.classList].find((c) => c.startsWith("language-"))?.slice("language-".length);
      let result;
      try {
        result = lang && hljs.getLanguage(lang) ? hljs.highlight(code, { language: lang }) : hljs.highlightAuto(code);
      } catch (err) {
        console.warn("webmd: highlighting failed", err);
        return;
      }
      block.replaceChildren(DOMPurify.sanitize(result.value, HIGHLIGHT_CONFIG));
      block.classList.add("hljs");
    });
  }

  // Give headings ids so #anchors work, GitHub-style.
  content.querySelectorAll("h1,h2,h3,h4,h5,h6").forEach((h) => {
    if (!h.id) h.id = h.textContent.trim().toLowerCase().replace(/[^\w\- ]+/g, "").replace(/ /g, "-");
  });
  if (location.hash) document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView();
})();
