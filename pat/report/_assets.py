"""
Static CSS and JS assets embedded in the HTML report.

Kept as plain strings so the HTML writer can remain focused on layout.
The CSS follows a warm, newsprint-inspired palette; the JS adds filter pills,
section jump, search, and lightweight scroll-reveal animation.
"""

from __future__ import annotations


CSS = """\
:root {
  --bg: #f7f5f0;
  --fg: #1c1917;
  --accent: #7c5e2a;
  --accent-light: rgba(124,94,42,.06);
  --ok: #1a6847;
  --minor: #2e6b8a;
  --moderate: #a07214;
  --major: #912626;
  --border: #d6cfc0;
  --card-bg: #fffef9;
  --card-shadow: 0 1px 3px rgba(0,0,0,.04), 0 4px 12px rgba(0,0,0,.03);
  --ui-font: 'DM Sans', system-ui, sans-serif;
  --body-font: 'Libre Baskerville', Georgia, serif;
  --display-font: 'Playfair Display', Georgia, serif;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; scroll-padding-top: 4.5rem; }
body {
  font-family: var(--body-font);
  background: var(--bg); color: var(--fg);
  background-image:
    radial-gradient(ellipse at 15% 50%, rgba(124,94,42,.03) 0%, transparent 70%),
    radial-gradient(ellipse at 85% 15%, rgba(30,70,100,.02) 0%, transparent 60%);
  max-width: 960px; margin: 0 auto;
  padding: 5.5rem 2rem 4rem;
  line-height: 1.75; font-size: 1.02rem;
  min-height: 100vh;
}

/* --- Top Nav --- */
.top-nav {
  position: fixed; top: 0; left: 0; right: 0; z-index: 100;
  background: rgba(247,245,240,.82);
  backdrop-filter: blur(14px); -webkit-backdrop-filter: blur(14px);
  border-bottom: 1px solid rgba(214,207,192,.5);
  padding: .55rem 0;
  transition: box-shadow .3s ease;
}
.top-nav.scrolled {
  box-shadow: 0 2px 16px rgba(0,0,0,.06);
}
.nav-inner {
  max-width: 960px; margin: 0 auto; padding: 0 2rem;
  display: flex; align-items: center; gap: .6rem; flex-wrap: wrap;
}
.pill {
  font-family: var(--ui-font); font-size: .72rem; font-weight: 600;
  padding: .28rem .75rem; border-radius: 1rem;
  border: 1.5px solid var(--border); background: transparent;
  color: var(--fg); cursor: pointer; transition: all .15s ease;
  text-transform: uppercase; letter-spacing: .05em;
  white-space: nowrap;
}
.pill:hover { border-color: var(--accent); color: var(--accent); }
.pill-active { background: var(--fg); color: var(--bg); border-color: var(--fg); }
.pill-active:hover { background: var(--fg); color: var(--bg); }
.pill .pill-count {
  font-size: .62rem; opacity: .7; margin-left: .2rem;
}
.nav-sep {
  width: 1px; height: 1.2rem; background: var(--border); flex-shrink: 0;
}
.section-jump {
  font-family: var(--ui-font); font-size: .78rem;
  padding: .3rem .55rem; border: 1.5px solid var(--border);
  border-radius: .3rem; background: rgba(255,254,249,.8); color: var(--fg);
  cursor: pointer; margin-left: auto;
}
.search-box {
  font-family: var(--ui-font); font-size: .78rem;
  padding: .3rem .7rem; border: 1.5px solid var(--border);
  border-radius: .3rem; background: rgba(255,254,249,.8); color: var(--fg);
  width: 170px; transition: width .2s ease, border-color .2s ease;
}
.search-box:focus { width: 220px; border-color: var(--accent); outline: none; }
.search-box::placeholder { color: #9a8e78; }

/* --- Typography --- */
.report-label {
  font-family: var(--ui-font); font-size: .68rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: .14em;
  color: var(--accent); margin-bottom: .4rem;
}
h1 {
  font-family: var(--display-font); font-size: 2.3rem; font-weight: 700;
  color: var(--fg); margin-bottom: .5rem; letter-spacing: -.03em;
  line-height: 1.15;
}
h2 {
  font-family: var(--display-font); font-size: 1.35rem; font-weight: 600;
  color: var(--fg); margin: 3rem 0 .85rem;
  padding-bottom: .45rem; border-bottom: 1.5px solid var(--border);
  letter-spacing: -.01em;
}
h3 {
  font-family: var(--display-font); font-size: 1.12rem; font-weight: 600;
  color: var(--fg); margin: 1.5rem 0 .5rem;
}
header {
  margin-bottom: 2.5rem; padding-bottom: 2rem;
  border-bottom: 1.5px solid var(--border);
}
.meta {
  font-family: var(--ui-font); font-size: .8rem;
  color: #7a6e5c; line-height: 1.6;
  display: flex; gap: .5rem; flex-wrap: wrap; align-items: center;
}
.meta-sep { color: var(--border); font-size: .7rem; }

/* --- Summary Table --- */
table {
  width: 100%; border-collapse: separate; border-spacing: 0;
  margin: 1.25rem 0; font-family: var(--ui-font); font-size: .84rem;
  table-layout: fixed;
  background: var(--card-bg); border: 1px solid var(--border);
  border-radius: .5rem; overflow: hidden;
  box-shadow: var(--card-shadow);
}
thead th {
  text-align: left; padding: .7rem 1rem;
  border-bottom: 2px solid var(--border);
  font-weight: 600; font-size: .7rem;
  text-transform: uppercase; letter-spacing: .08em;
  color: #7a6e5c; background: rgba(214,207,192,.12);
}
thead th:nth-child(1) { width: 3rem; }
thead th:nth-child(2) { width: 10.5rem; }
thead th:nth-child(3) { width: 6.5rem; }
tbody td {
  padding: .6rem 1rem;
  border-bottom: 1px solid rgba(214,207,192,.45);
  vertical-align: top;
}
tbody td:last-child { word-break: break-word; }
tbody tr { transition: background .12s ease; }
tbody tr:hover { background: rgba(124,94,42,.035); }
tbody tr:last-child td { border-bottom: none; }

/* --- Badges --- */
.badge {
  display: inline-flex; align-items: center; gap: .3rem;
  padding: .18rem .6rem; border-radius: .25rem;
  font-family: var(--ui-font);
  font-size: .65rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: .08em;
  box-shadow: 0 1px 2px rgba(0,0,0,.08);
}
.badge::before {
  content: ''; display: inline-block;
  width: 5px; height: 5px; border-radius: 50%;
  background: currentColor; opacity: .5;
}
.badge-ok { background: var(--ok); color: #fff; }
.badge-minor { background: var(--minor); color: #fff; }
.badge-moderate { background: var(--moderate); color: #fff; }
.badge-major { background: var(--major); color: #fff; }

/* --- Agent Sections --- */
.agent-section {
  margin: 2rem 0; background: var(--card-bg);
  border: 1px solid var(--border); border-radius: .5rem;
  border-left: 4px solid var(--border);
  box-shadow: var(--card-shadow);
  overflow: hidden;
  opacity: 0; transform: translateY(10px);
  transition: opacity .45s ease, transform .45s ease,
              box-shadow .2s ease;
}
.agent-section.visible { opacity: 1; transform: translateY(0); }
.agent-section:hover {
  box-shadow: 0 2px 8px rgba(0,0,0,.06), 0 8px 24px rgba(0,0,0,.04);
}
.agent-section[data-severity="ok"] { border-left-color: var(--ok); }
.agent-section[data-severity="minor"] { border-left-color: var(--minor); }
.agent-section[data-severity="moderate"] { border-left-color: var(--moderate); }
.agent-section[data-severity="major"] { border-left-color: var(--major); }
.agent-header {
  display: flex; align-items: center; gap: .7rem;
  padding: .8rem 1.4rem;
  border-bottom: 1px solid var(--border);
  cursor: pointer; user-select: none;
  transition: background .12s ease;
}
.agent-header:hover { background: var(--accent-light); }
.agent-header .agent-name {
  font-family: var(--display-font); font-size: 1.1rem; font-weight: 600;
}
.toggle-icon {
  margin-left: auto; font-size: .7rem; color: #9a8e78;
  transition: transform .3s ease;
}
.agent-section.collapsed .toggle-icon { transform: rotate(-90deg); }
.agent-section.collapsed .findings { display: none; }
.agent-section.collapsed .agent-header { border-bottom-color: transparent; }
.agent-section.highlight { animation: flash 1.5s ease-out; }
@keyframes flash {
  0% { box-shadow: 0 0 0 3px var(--accent); }
  100% { box-shadow: var(--card-shadow); }
}

/* --- Findings Content --- */
.findings {
  padding: 1.25rem 1.5rem;
  animation: fadeContent .3s ease;
}
@keyframes fadeContent {
  from { opacity: 0; }
  to { opacity: 1; }
}

/* --- Quick Wins --- */
.quick-wins {
  background: rgba(26,104,71,.04); border: 1.5px solid var(--ok);
  border-radius: .5rem; padding: 1.1rem 1.4rem; margin: 1.5rem 0;
  box-shadow: var(--card-shadow);
  opacity: 0; transform: translateY(10px);
  transition: opacity .45s ease, transform .45s ease;
}
.quick-wins.visible { opacity: 1; transform: translateY(0); }
.quick-wins h3 {
  font-family: var(--ui-font); font-size: .78rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .08em;
  color: var(--ok); margin: 0 0 .6rem;
}
.quick-wins ul { list-style: none; padding: 0; }
.quick-wins li {
  font-size: .9rem; margin: .4rem 0; padding-left: 1.1rem;
  position: relative; font-family: var(--ui-font);
}
.quick-wins li::before {
  content: ''; position: absolute; left: 0; top: .5rem;
  width: 5px; height: 5px; border-radius: 50%; background: var(--ok);
}

/* --- Markdown Findings --- */
.findings-markdown {
  font-family: var(--ui-font); font-size: .9rem; line-height: 1.65;
}
.findings-markdown p { margin: .5rem 0; }
.findings-markdown ul, .findings-markdown ol { margin: .4rem 0 .4rem 1.4rem; }
.findings-markdown li { margin: .2rem 0; }
.findings-markdown li > ul, .findings-markdown li > ol { margin-top: .15rem; margin-bottom: .15rem; }
.findings-markdown blockquote {
  border-left: 3px solid var(--accent); padding: .4rem .75rem;
  color: #5a4e3a; margin: .5rem 0; font-style: italic;
  background: rgba(124,94,42,.04); border-radius: 0 .25rem .25rem 0;
}
.findings-markdown code {
  background: rgba(214,207,192,.3); padding: .1rem .35rem;
  border-radius: .2rem; font-size: .84rem;
}
.findings-markdown table {
  font-size: .82rem;
  border: 1px solid var(--border); border-radius: .3rem;
  overflow: hidden; table-layout: auto;
}
.findings-markdown table th {
  background: rgba(214,207,192,.25);
}
.findings-markdown h2 {
  font-family: var(--display-font);
  font-size: 1.05rem; margin: 1.5rem 0 .5rem;
  border-bottom: 1px solid var(--border);
}
.findings-markdown h3 {
  font-family: var(--display-font);
  font-size: .95rem; margin: 1.2rem 0 .4rem;
}
.findings-markdown hr {
  border: none; border-top: 1px solid var(--border); margin: 1.25rem 0;
}
.findings-markdown strong { font-weight: 600; }

/* --- Structured Review Components --- */
.rv-quote {
  border-left: 3px solid var(--accent); padding: .5rem .85rem;
  margin: .5rem 0; font-style: italic; color: #5a4e3a;
  background: rgba(124,94,42,.04); border-radius: 0 .25rem .25rem 0;
  font-size: .95rem;
}
.rv-quote cite {
  display: block; font-style: normal; margin-top: .3rem;
  font-family: var(--ui-font); font-size: .72rem;
  color: #9a8e78; text-transform: uppercase; letter-spacing: .04em;
}
.rv-callout {
  background: rgba(214,207,192,.2); border-radius: .35rem;
  padding: .6rem .9rem; margin: .6rem 0;
  border-left: 3px solid var(--accent);
}
.rv-callout-label {
  font-family: var(--ui-font); font-size: .68rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .07em;
  color: var(--accent); display: block; margin-bottom: .25rem;
}
.rv-field-label {
  font-family: var(--ui-font); font-size: .68rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .06em;
  color: #9a8e78; margin-right: .3rem;
}
.rv-grade {
  display: inline-block; padding: .12rem .45rem; border-radius: .2rem;
  font-family: var(--ui-font); font-size: .65rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .06em; color: #fff;
  vertical-align: middle; margin-left: .15rem;
  box-shadow: 0 1px 2px rgba(0,0,0,.1);
}
.rv-pass { background: var(--ok); }
.rv-warn { background: var(--moderate); }
.rv-fail { background: var(--major); }
.rv-diff {
  background: var(--card-bg); border: 1px solid var(--border);
  border-radius: .35rem; margin: .5rem 0; overflow: hidden;
}
.rv-diff-inline { margin: .4rem 0; }
.rv-before {
  padding: .45rem .75rem; color: #8a7a6a;
  text-decoration: line-through; font-size: .91rem;
  border-bottom: 1px solid var(--border);
}
.rv-after {
  padding: .45rem .75rem; font-size: .91rem; font-weight: 500;
}
.rv-diff-label {
  font-family: var(--ui-font); font-size: .62rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .06em;
  margin-right: .4rem; display: inline-block; min-width: 4.5rem;
}
.rv-before .rv-diff-label { color: #9a8e78; }
.rv-after .rv-diff-label { color: var(--ok); }
.rv-saved {
  font-family: var(--ui-font); font-size: .7rem; font-weight: 600;
  color: var(--ok); margin-left: .3rem;
}
.rv-weakness, .rv-impact, .rv-defense {
  padding: .5rem .75rem; margin: .3rem 0; border-radius: .3rem;
  font-size: .93rem;
}
.rv-weakness { background: rgba(145,38,38,.05); border-left: 3px solid var(--major); }
.rv-impact { background: rgba(160,114,20,.05); border-left: 3px solid var(--moderate); }
.rv-defense { background: rgba(26,104,71,.05); border-left: 3px solid var(--ok); }

/* --- Figure Comparison --- */
.fig-compare {
  display: flex; gap: 1.5rem; margin: 1rem 0; flex-wrap: wrap;
  align-items: flex-start;
}
.fig-compare img {
  max-width: 48%; border: 1px solid var(--border);
  border-radius: .35rem; box-shadow: var(--card-shadow);
}
.fig-label {
  font-family: var(--ui-font); font-size: .68rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .06em;
  color: #7a6e5c; margin-bottom: .3rem;
}
.code-diff {
  background: var(--card-bg); border: 1px solid var(--border);
  border-radius: .35rem; padding: .6rem .8rem; margin: .5rem 0;
  font-family: 'SF Mono', Menlo, Consolas, monospace; font-size: .78rem;
  line-height: 1.5; overflow-x: auto; white-space: pre;
}
.code-diff .diff-add { color: var(--ok); }
.code-diff .diff-remove { color: var(--major); }
.code-diff .diff-header { color: var(--accent); font-weight: 600; }
.iteration-section {
  margin: 1.5rem 0; padding: 1rem 1.4rem;
  background: var(--card-bg); border: 1px solid var(--border);
  border-radius: .5rem; box-shadow: var(--card-shadow);
}
.iteration-header {
  font-family: var(--ui-font); font-size: .82rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .06em;
  color: var(--accent); margin-bottom: .8rem;
}

.hidden { display: none !important; }

/* --- Radar Chart --- */
.radar-container {
  display: flex; align-items: center; justify-content: center;
  margin: 2.5rem 0; gap: 2.5rem; flex-wrap: wrap;
  opacity: 0; transform: translateY(10px);
  transition: opacity .45s ease, transform .45s ease;
}
.radar-container.visible { opacity: 1; transform: translateY(0); }
.radar-container svg { max-width: 380px; flex-shrink: 0; }
.composite-score {
  text-align: center; padding: 2rem;
  background: var(--card-bg); border: 2px solid var(--border);
  border-radius: .6rem; min-width: 190px;
  box-shadow: var(--card-shadow);
}
.composite-score .score-value {
  font-family: var(--display-font); font-size: 3.2rem; font-weight: 700;
  line-height: 1;
}
.composite-score .score-label {
  font-family: var(--ui-font); font-size: .78rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: .08em;
  color: #7a6e5c; margin-top: .6rem;
}

/* --- Comparison Scores --- */
.comparison-scores {
  text-align: center; padding: 2rem;
  background: var(--card-bg); border: 2px solid var(--border);
  border-radius: .6rem; min-width: 220px;
  box-shadow: var(--card-shadow);
}
.comparison-scores .score-row {
  display: flex; align-items: center; justify-content: center;
  gap: .6rem; margin-bottom: .5rem;
}
.comparison-scores .score-old {
  font-family: var(--display-font); font-size: 2rem; font-weight: 700;
  color: #999; text-decoration: line-through;
}
.comparison-scores .score-arrow {
  font-size: 1.4rem; color: #7a6e5c;
}
.comparison-scores .score-new {
  font-family: var(--display-font); font-size: 3.2rem; font-weight: 700;
  line-height: 1;
}
.comparison-scores .score-delta {
  font-family: var(--ui-font); font-size: .9rem; font-weight: 600;
}
.comparison-scores .score-delta.positive { color: var(--ok); }
.comparison-scores .score-delta.negative { color: var(--major); }
.comparison-scores .score-label {
  font-family: var(--ui-font); font-size: .78rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: .08em;
  color: #7a6e5c; margin-top: .6rem;
}

/* --- NLP Metrics Table --- */
.nlp-comparison {
  margin: 1.5rem 0;
  background: var(--card-bg); border: 1px solid var(--border);
  border-radius: .5rem; padding: 1.2rem 1.5rem;
  box-shadow: var(--card-shadow);
}
.nlp-comparison h3 {
  font-family: var(--ui-font); font-size: .78rem; font-weight: 700;
  text-transform: uppercase; letter-spacing: .08em;
  color: var(--accent); margin: 0 0 .8rem;
}
.nlp-comparison table { margin: 0; }
.nlp-comparison td.delta-positive { color: var(--ok); font-weight: 600; }
.nlp-comparison td.delta-negative { color: var(--major); font-weight: 600; }

/* --- Agreement Heatmap --- */
.heatmap-table { margin: 1.5rem 0; }
.heatmap-table td {
  width: 2rem; height: 2rem; text-align: center;
  font-size: .68rem; font-weight: 600; border: 1px solid var(--bg);
}
.heatmap-table th {
  font-size: .7rem; padding: .3rem .5rem;
  font-family: var(--ui-font); white-space: nowrap;
}
.heat-ok { background: var(--ok); color: #fff; }
.heat-minor { background: var(--minor); color: #fff; }
.heat-moderate { background: var(--moderate); color: #fff; }
.heat-major { background: var(--major); color: #fff; }
.heat-none { background: rgba(214,207,192,.15); color: #9a8e78; }

/* --- KaTeX --- */
.katex { font-size: 1em !important; }

/* --- Print --- */
@media print {
  .top-nav { display: none; }
  body { padding-top: 1rem; max-width: 100%; background: #fff; }
  .agent-section {
    break-inside: avoid; page-break-inside: avoid;
    box-shadow: none; opacity: 1 !important; transform: none !important;
  }
  .agent-section.collapsed .findings { display: block !important; }
  .agent-section.collapsed .agent-header { border-bottom: 1px solid var(--border); }
  .quick-wins, .radar-container { opacity: 1 !important; transform: none !important; }
}
"""


JS = """\
<script>
function filterSeverity(sev) {
  document.querySelectorAll('.pill[data-sev]').forEach(function(b) {
    b.classList.remove('pill-active');
  });
  var allBtn = document.querySelector('.pill:not([data-sev])');
  if (!sev) {
    allBtn.classList.add('pill-active');
  } else {
    var active = document.querySelector('.pill[data-sev="'+sev+'"]');
    if (active) active.classList.add('pill-active');
    allBtn.classList.remove('pill-active');
  }
  document.querySelectorAll('.agent-section').forEach(function(el) {
    el.classList.toggle('hidden', !!(sev && el.dataset.severity !== sev));
  });
  document.querySelectorAll('tbody tr[data-severity]').forEach(function(tr) {
    tr.classList.toggle('hidden', !!(sev && tr.dataset.severity !== sev));
  });
}
function jumpToSection(id) {
  if (!id) return;
  var el = document.getElementById(id);
  if (el) {
    if (el.classList.contains('collapsed')) el.classList.remove('collapsed');
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    el.classList.add('highlight');
    setTimeout(function() { el.classList.remove('highlight'); }, 1500);
  }
  document.querySelector('.section-jump').selectedIndex = 0;
}
function searchFindings() {
  var q = document.getElementById('search').value.toLowerCase();
  document.querySelectorAll('.agent-section').forEach(function(el) {
    el.classList.toggle('hidden', !!(q && !el.textContent.toLowerCase().includes(q)));
  });
  document.querySelectorAll('tbody tr[data-severity]').forEach(function(tr) {
    tr.classList.toggle('hidden', !!(q && !tr.textContent.toLowerCase().includes(q)));
  });
}
function toggleSection(header) {
  header.closest('.agent-section').classList.toggle('collapsed');
}
function toggleAll() {
  var sections = document.querySelectorAll('.agent-section');
  var anyExpanded = Array.from(sections).some(function(s) {
    return !s.classList.contains('collapsed');
  });
  sections.forEach(function(s) {
    s.classList.toggle('collapsed', anyExpanded);
  });
}
document.addEventListener('DOMContentLoaded', function() {
  // Auto-collapse OK sections so the reader's attention goes to problems first.
  document.querySelectorAll('.agent-section[data-severity="ok"]').forEach(function(el) {
    el.classList.add('collapsed');
  });
  // Add a subtle shadow to the nav once the user scrolls past the header.
  window.addEventListener('scroll', function() {
    document.querySelector('.top-nav')
      .classList.toggle('scrolled', window.scrollY > 10);
  }, {passive: true});
  // Fade-in observer for agent cards as they enter the viewport.
  if ('IntersectionObserver' in window) {
    var obs = new IntersectionObserver(function(entries) {
      entries.forEach(function(e) {
        if (e.isIntersecting) {
          e.target.classList.add('visible');
          obs.unobserve(e.target);
        }
      });
    }, {threshold: 0.04, rootMargin: '0px 0px -30px 0px'});
    document.querySelectorAll('.agent-section, .quick-wins, .radar-container')
      .forEach(function(el) { obs.observe(el); });
  } else {
    // Fallback for environments without IntersectionObserver.
    document.querySelectorAll('.agent-section, .quick-wins, .radar-container')
      .forEach(function(el) { el.classList.add('visible'); });
  }
});
</script>
"""
