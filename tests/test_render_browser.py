"""Rendering and sanitization in a real browser (Chromium via Playwright).

These are the only tests that execute the client-side pipeline: marked ->
DOMPurify -> DOM, plus highlight.js. All network access outside the webmd
server is blocked, which also proves pages render offline.

Locally, tests are skipped if Playwright or its Chromium isn't installed.
CI sets WEBMD_REQUIRE_BROWSER=1 so a missing browser fails instead.
"""

import os

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api") if not os.environ.get("WEBMD_REQUIRE_BROWSER") else None
if playwright_sync is None:
    import playwright.sync_api as playwright_sync


@pytest.fixture(scope="module")
def browser():
    with playwright_sync.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # browser binary not installed
            if os.environ.get("WEBMD_REQUIRE_BROWSER"):
                raise
            pytest.skip(f"Chromium not available: {e}")
        yield b
        b.close()


@pytest.fixture
def open_page(browser, serve):
    """Open a path on a fresh webmd server with every non-webmd request blocked."""
    contexts = []

    def go(path, *serve_args, **serve_kwargs):
        s = serve(*serve_args, **serve_kwargs)
        ctx = browser.new_context(ignore_https_errors=True)
        contexts.append(ctx)
        page = ctx.new_page()
        external = []

        def route(r):
            if r.request.url.startswith(s.url):
                r.continue_()
            else:
                external.append(r.request.url)
                r.abort()

        page.route("**/*", route)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: (errors.append(f"dialog: {d.message}"), d.dismiss()))
        page.goto(s.url + path)
        page.wait_for_function("document.getElementById('content').childElementCount > 0")
        page.external_requests = external
        page.js_errors = errors
        return page

    yield go
    for ctx in contexts:
        ctx.close()


def test_normal_markdown_renders_with_bundled_assets_only(open_page):
    page = open_page("/normal.md")
    info = page.evaluate(
        """() => {
        const c = document.getElementById('content');
        return {
            h1: c.querySelector('h1')?.id,
            tableCells: c.querySelectorAll('table td').length,
            tasks: c.querySelectorAll('input[type=checkbox]').length,
            highlighted: c.querySelectorAll('pre code span[class^="hljs-"]').length,
            codeText: c.querySelector('pre code').textContent,
            boldInCode: !!c.querySelector('pre code b'),
            kbd: c.querySelectorAll('kbd').length,
            details: !!c.querySelector('details > summary'),
            mark: !!c.querySelector('mark'),
            sub: !!c.querySelector('sub'),
            svgCircle: !!c.querySelector('svg circle'),
            img: c.querySelector('img')?.getAttribute('src'),
            link: c.querySelector('a[href="other.md"]')?.textContent,
            styled: getComputedStyle(c.querySelector('table td')).borderTopStyle,
        };
    }"""
    )
    assert info == {
        "h1": "title-here",
        "tableCells": 2,
        "tasks": 2,
        "highlighted": info["highlighted"],
        "codeText": 'def hi():\n    return "<b>not bold</b>"\n',
        "boldInCode": False,
        "kbd": 2,
        "details": True,
        "mark": True,
        "sub": True,
        "svgCircle": True,
        "img": "pic.svg",
        "link": "link",
        "styled": "solid",  # github-markdown-css loaded from the bundle
    }
    assert info["highlighted"] >= 3  # highlight.js ran
    assert page.external_requests == []
    assert page.js_errors == []


def test_xss_payloads_do_not_execute(open_page):
    page = open_page("/xss.md")
    # Fire every event a user (or the page) might trigger on what's left.
    page.evaluate(
        """async () => {
        for (const el of document.querySelectorAll('#content *')) {
            for (const t of ['click', 'mouseover', 'focus', 'toggle', 'load', 'error'])
                el.dispatchEvent(new Event(t, {bubbles: true}));
        }
        await new Promise(r => setTimeout(r, 500));
    }"""
    )
    result = page.evaluate(
        """() => {
        const c = document.getElementById('content');
        const all = [...c.querySelectorAll('*')];
        const urlAttrs = ['href', 'src', 'action', 'formaction', 'data', 'xlink:href', 'srcdoc'];
        return {
            pwned: window.__pwned ?? null,
            bodyVisible: getComputedStyle(document.body).display !== 'none',
            sentinel: c.textContent.includes('SAFE_SENTINEL'),
            handlers: all.flatMap(e => [...e.attributes].filter(a => a.name.startsWith('on')).map(a => a.name)),
            dangerousUrls: all.flatMap(e => urlAttrs.map(n => e.getAttribute(n)))
                .filter(u => u && /^\\s*(javascript|vbscript|data:text)/i.test(u)),
            forbidden: ['script', 'iframe', 'object', 'embed', 'form', 'style', 'base', 'meta', 'link',
                        'math', 'template', 'noscript', 'use', 'animate', 'set', 'foreignObject']
                .filter(t => c.querySelector(t)),
            styleAttrs: c.querySelectorAll('[style]').length,
            codeBlocks: [...c.querySelectorAll('pre code')].map(e => e.textContent.trim()),
            codeChildren: [...c.querySelectorAll('pre code *')].map(e => e.tagName).filter(t => t !== 'SPAN'),
        };
    }"""
    )
    assert result["pwned"] is None
    assert result["bodyVisible"]
    assert result["sentinel"], "content after the payloads must still render"
    assert result["handlers"] == []
    assert result["dangerousUrls"] == []
    assert result["forbidden"] == []
    assert result["styleAttrs"] == 0
    # Code blocks show the payload as text; highlighting adds only <span>s.
    assert result["codeBlocks"][0].startswith("<script>window.__pwned=1</script>")
    assert result["codeBlocks"][1] == '<img src=x onerror="window.__pwned=1">'
    assert result["codeChildren"] == []
    assert page.js_errors == []


@pytest.mark.parametrize(
    "payload",
    [
        "<img src=x onerror=window.__pwned=1>",
        '<a href="javascript:window.__pwned=1" id=t>x</a>',
        "[x](javascript:window.__pwned=1)",
        '<svg><a href="javascript:window.__pwned=1" id=t><text>x</text></a></svg>',
        "<svg onload=window.__pwned=1>",
        '<iframe srcdoc="<script>parent.__pwned=1</script>"></iframe>',
        "<details open ontoggle=window.__pwned=1><summary>x</summary></details>",
        '<object data="data:text/html,<script>parent.__pwned=1</script>"></object>',
        '<form><button formaction="javascript:window.__pwned=1" id=t>x</button></form>',
        # mXSS via parser differential. DOMPurify drops the rest of the document here: safe, if lossy.
        '<noscript><p title="</noscript><img src=x onerror=window.__pwned=1>"></noscript>',
    ],
)
def test_individual_payloads(open_page, site, payload):
    (site / "p.md").write_text(f"# P\n\n{payload}\n\nend\n", encoding="utf-8")
    page = open_page("/p.md")
    if page.locator("#t").count():
        page.locator("#t").click(force=True, no_wait_after=True)
    page.wait_for_timeout(200)
    assert page.evaluate("window.__pwned ?? null") is None
    assert page.url.endswith("/p.md")  # no javascript: navigation happened


def test_csp_blocks_scripts_even_if_sanitizer_were_bypassed(browser, serve):
    """Defense in depth: page script tries the classic injection sinks; the CSP refuses each.

    Uses a real <script> injected by the browser as if it came from the server, via
    Playwright's add_init_script, which runs in the page's main world (unlike evaluate,
    which DevTools runs exempt from CSP eval restrictions).
    """
    s = serve()
    ctx = browser.new_context()
    page = ctx.new_page()
    page.add_init_script(
        """
        window.addEventListener('DOMContentLoaded', () => {
            const attempt = (f) => { try { f(); return 'allowed'; } catch (e) { return e.name; } };
            const content = document.getElementById('content');
            window.__results = {
                scriptText: attempt(() => {
                    const s = document.createElement('script');
                    s.textContent = 'window.__pwned = 1';
                    document.body.appendChild(s);
                }),
                innerHTML: attempt(() => { content.innerHTML = '<img src=x onerror="window.__pwned=1">'; }),
                insertAdjacent: attempt(() => content.insertAdjacentHTML('beforeend', '<img src=x onerror=1>')),
                stringTimer: attempt(() => setTimeout('window.__pwned = 1', 0)),
            };
        });
        """
    )
    page.goto(s.url + "/README.md")
    page.wait_for_function("window.__results !== undefined")
    page.wait_for_timeout(100)
    assert page.evaluate("window.__results") == {
        "scriptText": "TypeError",  # Trusted Types: no raw script text
        "innerHTML": "TypeError",  # Trusted Types: no raw HTML sinks
        "insertAdjacent": "TypeError",
        "stringTimer": "TypeError",
    }
    assert page.evaluate("window.__pwned ?? null") is None
    ctx.close()


def test_fails_closed_without_sanitizer(open_page, browser, serve):
    s = serve()
    ctx = browser.new_context()
    page = ctx.new_page()
    page.route("**/purify.min.js", lambda r: r.abort())
    page.goto(s.url + "/xss.md")
    page.wait_for_selector(".webmd-error")
    assert page.evaluate("document.getElementById('content').querySelector('img, a, svg')") is None
    assert "sanitizer" in page.locator(".webmd-error").text_content()
    ctx.close()


def test_raw_html_file_cannot_run_script(open_page, browser, serve, site):
    (site / "page.html").write_text("<script>document.title = 'RAN'</script><p>hi</p>", encoding="utf-8")
    s = serve()
    ctx = browser.new_context()
    page = ctx.new_page()
    page.goto(s.url + "/page.html")
    assert page.title() != "RAN"  # sandbox CSP
    ctx.close()


def test_renders_over_https_with_auth(open_page, serve, browser):
    s = serve("-b", "0.0.0.0")
    user, password = s.credentials()
    ctx = browser.new_context(ignore_https_errors=True, http_credentials={"username": user, "password": password})
    page = ctx.new_page()
    page.goto(s.url + "/normal.md")
    page.wait_for_selector("#content h1")
    assert page.locator("#content h1").text_content() == "Title Here"
    assert page.locator("pre code span[class^='hljs-']").count() >= 3
    ctx.close()
