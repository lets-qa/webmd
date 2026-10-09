# XSS

This file must render with zero script execution. Each payload sets window.__pwned.

<script>window.__pwned = (window.__pwned||0)+1</script>
<img src=x onerror="window.__pwned=(window.__pwned||0)+1">
[js link](javascript:window.__pwned=1)
<a href="javascript:window.__pwned=1">js a</a>
<a href="JaVaScRiPt&colon;alert(1)">entity</a>
<a href="data:text/html,<script>alert(1)</script>">data</a>
<svg><script>window.__pwned=1</script><a href="javascript:alert(1)"><text>svg link</text></a></svg>
<svg onload="window.__pwned=1"></svg>
<svg><animate onbegin="window.__pwned=1" attributeName="x" dur="1s"/></svg>
<svg><use href="data:image/svg+xml,<svg id='x' xmlns='http://www.w3.org/2000/svg'><image href='1' onerror='window.__pwned=1'/></svg>#x"/></svg>
<math><mtext><table><mglyph><style><img src=x onerror="window.__pwned=1"></style></mglyph></table></mtext></math>
<iframe src="javascript:window.__pwned=1"></iframe>
<iframe srcdoc="<script>parent.__pwned=1</script>"></iframe>
<object data="javascript:alert(1)"></object>
<embed src="data:text/html,<script>alert(1)</script>">
<form action="javascript:alert(1)"><button>go</button></form>
<style>body{display:none}</style>
<p style="position:fixed;top:0;left:0;width:100%;height:100%;background:red">overlay</p>
<base href="https://evil.example/">
<meta http-equiv="refresh" content="0;url=https://evil.example">
<link rel="stylesheet" href="https://evil.example/x.css">
<div onclick="window.__pwned=1" onmouseover="window.__pwned=1">hover</div>
<template><img src=x onerror=window.__pwned=1></template>
<details open ontoggle="window.__pwned=1"><summary>t</summary></details>

```html
<script>window.__pwned=1</script><img src=x onerror="window.__pwned=1">
```

```nosuchlang
<img src=x onerror="window.__pwned=1">
```

<p>SAFE_SENTINEL</p>
