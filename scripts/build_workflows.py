#!/usr/bin/env python3
"""Build the four n8n workflow exports (sanitized) from code + the prompt files.

    python3 scripts/build_workflows.py            # writes workflows/A..D.json
    python3 scripts/localize.py                   # then: raw copies with your ids (gitignored)

Edit prompts in prompts/*.md and re-run. The JSON files are generated, do not hand-edit them.
"""
import json, re, uuid, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "workflows"
TZ = "UTC"                                  # localize.py swaps in your timezone
BASE = "appREPLACE_ME"
T_TOPICS = "tblTOPICS_REPLACE_ME"
T_PROFILE = "tblPROFILE_REPLACE_ME"
SLACK_APPROVALS = "C_APPROVALS_REPLACE_ME"     # localize.py swaps in your channel ids
SLACK_ALERTS = "C_ALERTS_REPLACE_ME"


def between(path, start, end):
    s = (ROOT / path).read_text()
    m = re.search(re.escape(start) + r"\n(.*?)\n" + re.escape(end), s, re.S)
    if not m:
        sys.exit(f"markers {start!r}..{end!r} not found in {path}")
    return m.group(1).strip()


SYSTEM_PROMPT = between("prompts/post-generation-system-prompt.md", "<!-- SYSTEM PROMPT START -->", "<!-- SYSTEM PROMPT END -->")
CLASSIFIER_PROMPT = between("prompts/post-generation-system-prompt.md", "<!-- CLASSIFIER PROMPT START -->", "<!-- CLASSIFIER PROMPT END -->")

# --------------------------------------------------------------------------- helpers
def nid():
    return str(uuid.uuid4())


def pos(col, row):
    return [col * 240, row * 180]


def node(name, type_, version, params, p, **extra):
    n = {"parameters": params, "id": nid(), "name": name, "type": type_, "typeVersion": version, "position": p}
    n.update(extra)
    return n


def sticky(name, content, p, w=520, h=260, color=1):
    return node(name, "n8n-nodes-base.stickyNote", 1, {"content": content, "height": h, "width": w, "color": color}, p)


def noop(name, p):
    return node(name, "n8n-nodes-base.noOp", 1, {}, p)


def code(name, js, p, **extra):
    return node(name, "n8n-nodes-base.code", 2, {"jsCode": js}, p, **extra)


def set_config(name, assignments, p, include_other=False):
    a = [{"id": nid(), "name": k, "value": v, "type": ("boolean" if isinstance(v, bool) else "number" if isinstance(v, (int, float)) else "string")} for k, v in assignments]
    params = {"mode": "manual", "assignments": {"assignments": a}, "options": {}}
    if include_other:
        params["includeOtherFields"] = True
    return node(name, "n8n-nodes-base.set", 3.4, params, p)


def if_true(name, expr, p):
    return node(name, "n8n-nodes-base.if", 2.2, {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                       "conditions": [{"id": nid(), "leftValue": "={{ " + expr + " }}", "rightValue": "", "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                       "combinator": "and"},
        "looseTypeValidation": True, "options": {}}, p)


def _at(table):
    return {"authentication": "airtableOAuth2Api", "base": {"__rl": True, "value": BASE, "mode": "id"}, "table": {"__rl": True, "value": table, "mode": "id"}}


def at_search(name, table, formula, p, always=False):
    params = {**_at(table), "operation": "search", "filterByFormula": formula, "returnAll": True, "options": {}}
    return node(name, "n8n-nodes-base.airtable", 2.1, params, p, retryOnFail=True, **({"alwaysOutputData": True} if always else {}))


def at_get(name, table, id_expr, p):
    return node(name, "n8n-nodes-base.airtable", 2.1, {**_at(table), "operation": "get", "id": id_expr, "options": {}}, p, retryOnFail=True)


def at_update(name, table, values, p, types=None):
    types = types or {}
    schema = [{"id": "id", "displayName": "id", "required": False, "defaultMatch": True, "canBeUsedToMatch": True, "display": True, "type": "string", "readOnly": True, "removed": False}]
    for k in values:
        if k == "id":
            continue
        schema.append({"id": k, "displayName": k, "required": False, "defaultMatch": False, "canBeUsedToMatch": True, "display": True, "type": types.get(k, "string"), "readOnly": False, "removed": False})
    params = {**_at(table), "operation": "update", "columns": {"mappingMode": "defineBelow", "value": values, "matchingColumns": ["id"], "schema": schema}, "options": {"typecast": True}}
    return node(name, "n8n-nodes-base.airtable", 2.1, params, p, retryOnFail=True)


def http_llm(name, p):
    return node(name, "n8n-nodes-base.httpRequest", 4.2, {
        "method": "POST", "url": "={{ $json.url }}", "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
        "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify($json.request_body) }}", "options": {"timeout": 45000}},
        p, retryOnFail=True, onError="continueRegularOutput")


def slack_http(name, url, body_expr, p):
    return node(name, "n8n-nodes-base.httpRequest", 4.2, {
        "method": "POST", "url": url, "authentication": "predefinedCredentialType", "nodeCredentialType": "slackApi",
        "sendBody": True, "specifyBody": "json", "jsonBody": body_expr, "options": {"timeout": 10000}}, p, retryOnFail=True)


def slack_text(name, channel_expr, text, p):
    return node(name, "n8n-nodes-base.slack", 2.3, {
        "select": "channel", "channelId": {"__rl": True, "value": channel_expr, "mode": "id"},
        "otherOptions": {"includeLinkToWorkflow": False}, "text": text}, p, retryOnFail=True)


def wire(conns, src, dst, out=0):
    conns.setdefault(src, {"main": []})
    while len(conns[src]["main"]) <= out:
        conns[src]["main"].append([])
    conns[src]["main"][out].append({"node": dst, "type": "main", "index": 0})


def workflow(name, nodes, conns):
    names = [n["name"] for n in nodes]
    assert len(names) == len(set(names)), "duplicate node names"
    for s, o in conns.items():
        assert s in names, f"connection from unknown node {s}"
        for br in o["main"]:
            for c in br:
                assert c["node"] in names, f"connection to unknown node {c['node']}"
    return {"name": name, "nodes": nodes, "connections": conns, "active": False,
            "settings": {"executionOrder": "v1", "timezone": TZ, "saveManualExecutions": True, "callerPolicy": "workflowsFromSameOwner"},
            "pinData": {}, "tags": []}


# --------------------------------------------------------------------------- shared JS
JS_GUARDRAIL_CORE = r"""
// ---- shared guardrail core: numbers, outlets, hashtags, signature ----
const TOKEN_RE = /(?<![A-Za-z\d])\d[\d,.]*(?:\s?(?:[KkMmBb](?![A-Za-z])|thousand|million|billion))?\+?/gi;
const norm = (tok) => { let t = String(tok).trim().replace(/[#,+]/g, '').replace(/[.,]$/, ''); const m = t.match(/^(\d+(?:\.\d+)?)\s?(k|m|b|thousand|million|billion)?$/i); if (!m) return null; const suf = (m[2] || '').toLowerCase(); const mult = { k: 1e3, thousand: 1e3, m: 1e6, million: 1e6, b: 1e9, billion: 1e9 }[suf] || 1; return Math.round(parseFloat(m[1]) * mult * 1000) / 1000; };
const allowed = new Set(); const addNum = (v) => { const n = norm(v); if (n !== null) allowed.add(n); };
const addText = (s) => { for (const m of String(s || '').matchAll(TOKEN_RE)) { addNum(m[0]); const plain = m[0].replace(/[,+]/g, '').trim(); const lead = plain.match(/^\d+(?:\.\d+)?/); if (lead) { addNum(lead[0]); if (lead[0].includes('.')) addNum(lead[0].split('.')[0]); } } };
const today = DateTime.now().setZone(TZ);
[today.year, today.month, today.day].forEach(addNum); addText(today.toISODate());
addText(SRC.author_text); addText(SRC.topic); addText(SRC.angle);
(SRC.news_items || []).forEach(n => { addText(n.title); addText(n.snippet); addText(n.date); });
const SIG = String(SRC.signature || '').trim();
let body = SIG ? post.replace(SIG, '') : post;
const offending = [];
for (const m of body.matchAll(TOKEN_RE)) { const tok = m[0].trim().replace(/[.,]$/, ''); const n = norm(tok); if (n !== null && !allowed.has(n)) offending.push(tok); }
if (offending.length) return fail('fact_check', { offending: [...new Set(offending)], error_message: 'fact_check: ' + [...new Set(offending)].join(', '), post_text: post });
const OUTLETS = ['techcrunch','the verge','wired','ars technica','reuters','bloomberg','forbes','bbc','cnn','the guardian','new york times','nytimes','wall street journal','wsj','financial times','business insider','axios','hacker news','stack overflow','github','gartner','mckinsey','harvard business review','hbr','linkedin news','the information','venturebeat'];
const hay = ((SRC.news_items || []).map(n => `${n.source || ''} ${n.title || ''} ${n.snippet || ''} ${n.url || ''}`).join(' ') + ' ' + SRC.author_text + ' ' + SRC.angle + ' ' + SRC.topic).toLowerCase();
const bodyLow = body.toLowerCase();
const badOutlets = OUTLETS.filter(o => new RegExp(`(^|[^a-z])${o.replace('.', '\\.')}([^a-z]|$)`).test(bodyLow) && !hay.includes(o));
if (badOutlets.length) return fail('unverified_source', { offending: badOutlets, error_message: 'unverified_source: ' + badOutlets.join(', '), post_text: post });
const tags = body.match(/(^|\s)#[\p{L}\d_]+/gu) || [];
if (tags.length > MAX_TAGS) return fail('too_many_hashtags', { offending: tags.map(t => t.trim()), error_message: `too_many_hashtags: ${tags.length} > ${MAX_TAGS}`, post_text: post });
"""

# --------------------------------------------------------------------------- Workflow A
def build_a():
    N, C = [], {}
    N.append(node("Schedule Mon/Wed/Fri 09:00", "n8n-nodes-base.scheduleTrigger", 1.2, {"rule": {"interval": [{"field": "cronExpression", "expression": "0 9 * * 1,3,5"}]}}, pos(0, 0)))
    N.append(node("Manual Trigger", "n8n-nodes-base.manualTrigger", 1, {}, pos(0, 1)))
    N.append(set_config("CONFIG", [
        ("TIMEZONE", TZ), ("STALE_CLAIM_HOURS", 2), ("CLAIM_SETTLE_SECONDS", 2),
        ("NEWS_MIN_SCORE", 0.5), ("NEWS_MAX_RESULTS", 5), ("NEWS_TIME_RANGE", "month"),
        ("LLM_PRIMARY", "groq"), ("LLM_FALLBACK", "gemini"),
        ("GROQ_MODEL", "openai/gpt-oss-120b"), ("GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions"),
        ("GEMINI_MODEL", "gemini-3.5-flash-lite"), ("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta/models"),
        ("LLM_TEMPERATURE", 0.6), ("LLM_RETRY_TEMPERATURE", 0.2), ("LLM_MAX_ATTEMPTS", 3),
        ("POST_MAX_CHARS", 1300), ("MAX_HASHTAGS", 3), ("POST_SIGNATURE", ""),
        ("SLACK_APPROVALS_CHANNEL", SLACK_APPROVALS), ("SLACK_ALERTS_CHANNEL", SLACK_ALERTS),
    ], pos(1, 0)))
    N.append(at_search("SEARCH STACK", T_TOPICS,
        "=OR({status}='queued', AND({status}='processing', IS_BEFORE({claimed_at}, DATEADD(NOW(), -{{ $('CONFIG').first().json.STALE_CLAIM_HOURS }}, 'hours'))))",
        pos(2, 0), always=True))
    N.append(code("POP TOP", r"""// POP TOP
// The stack: newest queued row first; a pinned row beats everything. Exactly one topic leaves this node per run.
const rows = $input.all().map(i => i.json).filter(r => r && r.id);
const ts = (r) => { const d = DateTime.fromISO(String(r.created_at || r.createdTime || '')); return d.isValid ? d.toMillis() : 0; };
rows.sort((a, b) => (Number(!!b.pinned) - Number(!!a.pinned)) || (ts(b) - ts(a)));
if (!rows.length) return [{ json: { empty: true, stack_size: 0 } }];
const top = rows[0];
return [{ json: { ...top, empty: false, stack_size: rows.length, was_stale_claim: top.status === 'processing' } }];
""", pos(3, 0)))
    N.append(if_true("IF STACK NOT EMPTY", "$json.empty !== true", pos(4, 0)))
    N.append(noop("Stack empty, nothing to do", pos(5, 1)))
    N.append(at_update("CLAIM", T_TOPICS, {"id": "={{ $json.id }}", "status": "processing", "execution_id": "={{ $execution.id }}", "claimed_at": "={{ $now.toUTC().toISO() }}"}, pos(5, 0), {"claimed_at": "dateTime"}))
    N.append(node("CLAIM SETTLE", "n8n-nodes-base.wait", 1.1, {"amount": "={{ $('CONFIG').first().json.CLAIM_SETTLE_SECONDS }}", "unit": "seconds"}, pos(6, 0)))
    N.append(at_get("READ-BACK", T_TOPICS, "={{ $('POP TOP').first().json.id }}", pos(7, 0)))
    N.append(node("IF CLAIM WON", "n8n-nodes-base.if", 2.2, {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2}, "conditions": [
            {"id": nid(), "leftValue": "={{ String($json.fields?.execution_id ?? $json.execution_id ?? '') }}", "rightValue": "={{ String($execution.id) }}", "operator": {"type": "string", "operation": "equals"}},
            {"id": nid(), "leftValue": "={{ $json.fields?.status ?? $json.status }}", "rightValue": "processing", "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"},
        "looseTypeValidation": True, "options": {}}, pos(8, 0)))
    N.append(noop("Lost claim, stop", pos(9, 1)))

    # ---- classification
    N.append(if_true("IF SCOPE SET", "['personal','general'].includes(String($json.fields?.scope ?? $json.scope ?? ''))", pos(9, 0)))
    N.append(code("CLASSIFY REQUEST", "// CLASSIFY REQUEST (Groq, temperature 0, JSON mode). Runs only when the row has no scope.\n"
        "const cfg = $('CONFIG').first().json;\nconst rec = $input.first().json; const f = rec.fields || rec;\n"
        "const CLASSIFIER = " + json.dumps(CLASSIFIER_PROMPT) + ";\n"
        r"""const user = 'Classify this topic:\n' + JSON.stringify({ title: f.topic, notes: f.angle || '', category: f.category || null }, null, 2) + '\n\nReturn the JSON object only.';
const model = String(cfg.GROQ_MODEL || 'openai/gpt-oss-120b');
const request_body = { model, temperature: 0, max_tokens: 300, response_format: { type: 'json_object' }, messages: [{ role: 'system', content: CLASSIFIER }, { role: 'user', content: user }] };
if (model.includes('gpt-oss')) request_body.reasoning_effort = 'low';
return [{ json: { request_body, url: String(cfg.GROQ_API_URL) } }];
""", pos(10, 1)))
    N.append(http_llm("GROQ CLASSIFY", pos(11, 1)))
    N.append(code("PARSE CLASSIFICATION", r"""// PARSE CLASSIFICATION
// LLM answer -> { scope, category }. If the call failed or answered nonsense, a keyword rule decides and the reviewer is told.
const resp = $input.first().json || {};
const rec = $('READ-BACK').first().json; const f = rec.fields || rec;
const CATS = ['job_search','project','fun_fact','dev_stat','opinion','other'];
let scope = null, category = f.category ? String(f.category) : null, scope_source = 'llm', reason = '';
try {
  if (resp.error) throw new Error(typeof resp.error === 'string' ? resp.error : (resp.error.message || JSON.stringify(resp.error)));
  const out = JSON.parse(resp?.choices?.[0]?.message?.content || '');
  scope = out.scope === 'personal' ? 'personal' : out.scope === 'general' ? 'general' : null;
  if (!category && CATS.includes(String(out.category))) category = String(out.category);
  reason = String(out.reason || '').slice(0, 200);
} catch (e) { reason = 'classifier failed: ' + String(e.message || e).slice(0, 200); }
if (!scope) {
  scope_source = 'fallback';
  const hay = (String(f.topic || '') + ' ' + String(f.angle || '')).toLowerCase();
  const personal = /(^|[^a-z])(i|i'm|im|me|my|myself|open to work|looking for|i built|i made|my project|lesson i)([^a-z]|$)/.test(hay) || ['job_search','project'].includes(category || '');
  scope = personal ? 'personal' : 'general';
}
if (!category) category = 'other';
return [{ json: { scope, category, scope_source, classify_reason: reason } }];
""", pos(12, 1)))
    N.append(code("TOPIC READY", r"""// TOPIC READY
// One normalised topic object, whatever decided the scope (the row itself or the classifier).
const rec = $('READ-BACK').first().json; const f = rec.fields || rec;
let cls = null; try { cls = $('PARSE CLASSIFICATION').first().json; } catch (e) { cls = null; }
const rowScope = ['personal','general'].includes(String(f.scope || '')) ? String(f.scope) : null;
const scope = rowScope || (cls && cls.scope) || 'general';
const scope_source = rowScope ? 'row' : (cls ? cls.scope_source : 'fallback');
const category = String(f.category || (cls && cls.category) || 'other');
return [{ json: {
  id: rec.id, topic: String(f.topic || '').trim(), category, scope, scope_source,
  classify_reason: cls ? cls.classify_reason : '',
  angle: String(f.angle || '').trim(),
  news_query: String(f.news_query || f.topic || '').trim(),
  is_personal: scope === 'personal',
  stack_size: (() => { try { return $('POP TOP').first().json.stack_size; } catch (e) { return null; } })(),
} }];
""", pos(13, 0)))
    N.append(at_update("SAVE SCOPE", T_TOPICS, {"id": "={{ $json.id }}", "scope": "={{ $json.scope }}", "category": "={{ $json.category }}", "scope_source": "={{ $json.scope_source }}"}, pos(14, 0)))

    # ---- profile + news
    N.append(at_search("PROFILE LOOKUP", T_PROFILE, "={active}=1", pos(15, 0), always=True))
    N.append(code("PROFILE READY", r"""// PROFILE READY
// Personal post: every active profile row. General post: only rows marked use_for = always (name, headline), so the voice stays the author's.
const t = $('TOPIC READY').first().json;
const rows = $input.all().map(i => i.json).filter(r => r && r.id && r.key);
const keep = rows.filter(r => t.is_personal || String(r.use_for || 'always') === 'always');
const author = {};
keep.forEach(r => { author[String(r.key).trim()] = String(r.value || '').trim(); });
return [{ json: { ...t, author, author_rows: keep.length, profile_rows_total: rows.length,
  author_text: Object.entries(author).map(([k, v]) => `${k}: ${v}`).join('\n') } }];
""", pos(16, 0)))
    N.append(if_true("IF GENERAL", "$json.is_personal !== true", pos(17, 0)))
    N.append(node("TAVILY NEWS SEARCH", "@tavily/n8n-nodes-tavily.tavily", 2.1, {
        "resource": "search", "operation": "query", "query": "={{ $('PROFILE READY').first().json.news_query }}",
        "options": {"topic": "news", "time_range": "={{ $('CONFIG').first().json.NEWS_TIME_RANGE }}", "search_depth": "basic",
                    "max_results": "={{ $('CONFIG').first().json.NEWS_MAX_RESULTS }}", "include_raw_content": False}},
        pos(18, 0), retryOnFail=True, onError="continueRegularOutput"))
    N.append(code("NORMALIZE NEWS", r"""// NORMALIZE NEWS
// General post: input is the Tavily response. Personal post: input is PROFILE READY (no results) -> zero news, no search was made.
// Tavily never returns an empty list, so relevance is computed: score >= NEWS_MIN_SCORE and a query token in title/content.
const cfg = $('CONFIG').first().json;
const t = $('PROFILE READY').first().json;
const resp = $input.first().json || {};
const minScore = Number(cfg.NEWS_MIN_SCORE ?? 0.5);
const STOP = new Set(['the','and','for','with','from','that','this','what','why','how','about','into','your','you','are','is','of','to','in','on','a','an','new','news','2025','2026','2027']);
const tokens = (s) => String(s || '').toLowerCase().replace(/[^\p{L}\d\s]/gu, ' ').split(/\s+/).filter(w => w.length >= 3 && !STOP.has(w));
const want = new Set(tokens(t.news_query));
const mentions = (r) => { const hay = (String(r.title || '') + ' ' + String(r.content || '')).toLowerCase(); for (const w of want) if (hay.includes(w)) return true; return want.size === 0; };
const host = (u) => { const m = String(u || '').match(/^[a-z]+:\/\/([^\/?#]+)/i); return m ? m[1].toLowerCase().replace(/^www\./, '') : ''; };
const isoDate = (d) => { if (!d) return null; let x = DateTime.fromISO(String(d)); if (!x.isValid) x = DateTime.fromRFC2822(String(d)); if (!x.isValid) x = DateTime.fromHTTP(String(d)); if (!x.isValid) { const j = new Date(String(d)); if (!isNaN(j.getTime())) x = DateTime.fromJSDate(j); } return x.isValid ? x.toUTC().toISODate() : String(d); };
const searched = t.is_personal !== true;
let error = null;
if (searched && resp.error) error = typeof resp.error === 'string' ? resp.error : (resp.error.message || JSON.stringify(resp.error));
const results = searched && Array.isArray(resp.results) ? resp.results : [];
const kept = results
  .filter(r => Number(r.score || 0) >= minScore && mentions(r))
  .map(r => ({ title: r.title || '', source: host(r.url), date: isoDate(r.published_date), snippet: String(r.content || '').slice(0, 400), url: r.url, score: Math.round(Number(r.score || 0) * 1000) / 1000 }))
  .sort((a, b) => b.score - a.score);
return [{ json: { id: t.id, searched, news_items: kept, news_json: JSON.stringify(kept), news_count: kept.length, has_news: kept.length > 0, news_raw_count: results.length, news_error: error } }];
""", pos(19, 0)))
    N.append(at_update("SAVE NEWS", T_TOPICS, {"id": "={{ $json.id }}", "news_json": "={{ $json.news_json }}", "news_count": "={{ $json.news_count }}"}, pos(20, 0), {"news_count": "number"}))

    # ---- draft
    N.append(code("BUILD PROMPT", "// BUILD PROMPT\n// First attempt: input is the SAVE NEWS record. Retry: input is the failed guardrail item (guardrail: true).\n"
        "const cfg = $('CONFIG').first().json;\nconst t = $('PROFILE READY').first().json;\nconst nw = $('NORMALIZE NEWS').first().json;\n"
        "const SYSTEM_RAW = " + json.dumps(SYSTEM_PROMPT) + ";\n"
        r"""const SYSTEM = SYSTEM_RAW.replace(/MAX_HASHTAGS/g, String(cfg.MAX_HASHTAGS ?? 3));
const prev = $input.first().json || {};
const isRetry = prev.guardrail === true;
const attempt = isRetry ? Number(prev.attempt || 1) + 1 : 1;
const temperature = isRetry ? Number(cfg.LLM_RETRY_TEMPERATURE ?? 0.2) : Number(cfg.LLM_TEMPERATURE ?? 0.6);
const news = (nw.news_items || []).map(n => ({ title: n.title, source: n.source, date: n.date, snippet: String(n.snippet || '').slice(0, 400) }));
const payload = { author: t.author || {}, topic: { title: t.topic, category: t.category, scope: t.scope, angle: t.angle }, news, has_news: news.length > 0, today: DateTime.now().setZone(cfg.TIMEZONE || 'UTC').toISODate() };
let user = 'Write the post for this input:\n' + JSON.stringify(payload, null, 2);
if (isRetry && Array.isArray(prev.offending) && prev.offending.length) {
  const why = { unverified_source: 'named sources or companies that are not in the input', too_many_hashtags: 'used too many hashtags' }[prev.reason] || 'used figures not present in the input';
  user += '\n\nYour previous draft ' + why + ': ' + prev.offending.join(', ') + '. Do not use them. Rewrite the post using only facts from the input.';
}
else if (isRetry && prev.reason === 'too_long') user += '\n\nYour previous draft was too long (' + (prev.error_message || '') + '). Rewrite it in at most 110 words.';
else if (isRetry) user += '\n\nYour previous answer was not valid JSON or failed. Return the JSON object only.';
const primary = String(cfg.LLM_PRIMARY || 'groq'), fallback = String(cfg.LLM_FALLBACK || 'gemini');
let provider = primary;
if (isRetry) { const prevProv = prev.provider || primary; provider = (prev.reason === 'llm_error') ? (prevProv === primary ? fallback : primary) : prevProv; }
const gemini_body = {
  system_instruction: { parts: [{ text: SYSTEM }] },
  contents: [{ role: 'user', parts: [{ text: user }] }],
  generationConfig: { temperature, maxOutputTokens: 4096, responseMimeType: 'application/json',
    responseSchema: { type: 'OBJECT', required: ['post_text','hook','facts_used','warnings'], properties: {
      post_text: { type: 'STRING' }, hook: { type: 'STRING' }, facts_used: { type: 'ARRAY', items: { type: 'STRING' } }, warnings: { type: 'ARRAY', items: { type: 'STRING' } } } } }
};
const groqModel = String(cfg.GROQ_MODEL || 'openai/gpt-oss-120b');
const groq_body = { model: groqModel, temperature, max_tokens: 4096, response_format: { type: 'json_object' },
  messages: [{ role: 'system', content: SYSTEM }, { role: 'user', content: user + '\n\nReturn the JSON object only.' }] };
if (groqModel.includes('gpt-oss')) groq_body.reasoning_effort = 'low';
const request_body = provider === 'groq' ? groq_body : gemini_body;
const url = provider === 'groq' ? String(cfg.GROQ_API_URL) : `${cfg.GEMINI_API_BASE}/${cfg.GEMINI_MODEL}:generateContent`;
return [{ json: { attempt, provider, temperature, request_body, url, record_id: t.id } }];
""", pos(21, 0)))
    N.append(if_true("IF PROVIDER GEMINI", "$json.provider === 'gemini'", pos(22, 0)))
    N.append(http_llm("GEMINI GENERATE", pos(23, -1)))
    N.append(http_llm("GROQ GENERATE", pos(23, 1)))
    N.append(code("PARSE + FACT GUARDRAIL", "// PARSE + FACT GUARDRAIL\n// 1) Parse the model's JSON. 2) Deterministic checks: length, numbers, named outlets, hashtags. See prompts/guardrail-logic.md.\n"
        r"""const cfg = $('CONFIG').first().json;
const bp = $('BUILD PROMPT').first().json;
const t = $('PROFILE READY').first().json;
const nw = $('NORMALIZE NEWS').first().json;
const resp = $input.first().json || {};
const TZ = cfg.TIMEZONE || 'UTC';
const MAX_TAGS = Number(cfg.MAX_HASHTAGS ?? 3);
const base = { guardrail: true, attempt: bp.attempt, provider: bp.provider, record_id: bp.record_id };
const fail = (reason, extra = {}) => [{ json: { ...base, ok: false, reason, offending: [], ...extra } }];
if (resp.error) return fail('llm_error', { error_message: `llm_error (${bp.provider}): ` + (typeof resp.error === 'string' ? resp.error : (resp.error.message || JSON.stringify(resp.error))).slice(0, 300) });
const text = bp.provider === 'groq' ? (resp?.choices?.[0]?.message?.content || '') : (resp?.candidates?.[0]?.content?.parts?.map(p => p.text || '').join('') || '');
let out;
try { out = JSON.parse(text); } catch (e) { return fail('llm_error', { error_message: 'llm_error: model did not return valid JSON', raw: text.slice(0, 500) }); }
let post = String(out.post_text || '').trim();
if (!post) return fail('llm_error', { error_message: 'llm_error: empty post_text' });
const signature = String(cfg.POST_SIGNATURE || '').trim();
if (signature && !post.includes(signature)) post = post.replace(/\s*$/, '') + '\n\n' + signature;
const maxChars = Number(cfg.POST_MAX_CHARS || 1300);
if (post.length > maxChars) return fail('too_long', { error_message: `too_long: ${post.length} > ${maxChars}`, post_text: post });
const SRC = { author_text: t.author_text || '', topic: t.topic, angle: t.angle, news_items: nw.news_items || [], signature };
""" + JS_GUARDRAIL_CORE + r"""
const warnings = Array.isArray(out.warnings) ? [...out.warnings] : [];
if (nw.searched && !(nw.news_items || []).length && !warnings.some(w => /news/i.test(w))) warnings.push('General post, but no usable news came back from the search' + (nw.news_error ? ` (search error: ${nw.news_error})` : '') + '. The draft is written from the topic notes only.');
if (t.is_personal && !t.author_rows) warnings.push('Personal post, but the profile table has no active rows. The model only had the topic and angle.');
if (t.scope_source === 'fallback') warnings.push(`Scope "${t.scope}" was decided by the keyword fallback, not the classifier: ${t.classify_reason}`);
return [{ json: { ...base, ok: true, reason: 'ok', post_text: post, hook: String(out.hook || '').slice(0, 200),
  model: bp.provider === 'groq' ? cfg.GROQ_MODEL : cfg.GEMINI_MODEL,
  facts_used: Array.isArray(out.facts_used) ? out.facts_used : [], warnings,
  word_count: body.trim().split(/\s+/).length, hashtags: tags.length } }];
""", pos(24, 0)))
    N.append(if_true("IF DRAFT OK", "$json.ok", pos(25, 0)))
    N.append(at_update("SAVE DRAFT", T_TOPICS, {"id": "={{ $json.record_id }}", "draft_text": "={{ $json.post_text }}"}, pos(26, 0)))
    N.append(code("BUILD SLACK MESSAGE", r"""// BUILD SLACK MESSAGE (Block Kit)
// Buttons carry the Airtable record id in `value`; action_id = approve | reject | edit. Workflow B handles the click.
const t = $('PROFILE READY').first().json;
const draft = $('PARSE + FACT GUARDRAIL').first().json;
const nw = $('NORMALIZE NEWS').first().json;
const esc = (s) => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const value = JSON.stringify({ r: t.id });
const newsLine = nw.searched ? `news: ${nw.news_count} item${nw.news_count === 1 ? '' : 's'}` : 'news: not searched (personal)';
/** @type {any[]} */
const blocks = [
  { type: 'header', text: { type: 'plain_text', text: `Post approval: ${t.topic}`.slice(0, 150), emoji: true } },
  { type: 'context', elements: [{ type: 'mrkdwn', text:
    `*${esc(t.scope)}* (scope by ${esc(t.scope_source)}) · ${esc(t.category)} · ${newsLine} · profile facts: ${t.author_rows}\n` +
    `model: ${esc(draft.model)} (attempt ${draft.attempt}) · ${draft.word_count} words · stack left: ${Math.max(0, (t.stack_size || 1) - 1)}` }] },
  { type: 'section', text: { type: 'mrkdwn', text: esc(draft.post_text).slice(0, 2900) } },
];
if (Array.isArray(draft.warnings) && draft.warnings.length) blocks.push({ type: 'section', text: { type: 'mrkdwn', text: ':warning: *Review notes*\n' + draft.warnings.map(w => '• ' + esc(w)).join('\n') } });
if (Array.isArray(draft.facts_used) && draft.facts_used.length) blocks.push({ type: 'context', elements: [{ type: 'mrkdwn', text: ('*Facts used:* ' + draft.facts_used.map(esc).join(' · ')).slice(0, 2900) }] });
if (nw.news_items && nw.news_items.length) blocks.push({ type: 'context', elements: [{ type: 'mrkdwn', text: ('*Sources:* ' + nw.news_items.map(n => `<${n.url}|${esc(n.source)}>`).join(' · ')).slice(0, 2900) }] });
blocks.push({ type: 'divider' });
blocks.push({ type: 'actions', block_id: 'post_approval', elements: [
  { type: 'button', action_id: 'approve', style: 'primary', text: { type: 'plain_text', text: 'Approve & publish', emoji: true }, value },
  { type: 'button', action_id: 'reject', style: 'danger', text: { type: 'plain_text', text: 'Reject', emoji: true }, value,
    confirm: { title: { type: 'plain_text', text: 'Reject this post?' }, text: { type: 'mrkdwn', text: 'The topic will be marked *rejected* and nothing is published. Set it back to queued to retry.' }, confirm: { type: 'plain_text', text: 'Reject' }, deny: { type: 'plain_text', text: 'Cancel' } } },
  { type: 'button', action_id: 'edit', text: { type: 'plain_text', text: 'Edit…', emoji: true }, value },
]});
return [{ json: { record_id: t.id, blocks_json: JSON.stringify({ blocks }), fallback_text: `Post approval needed: ${t.topic}` } }];
""", pos(27, 0)))
    N.append(node("SLACK: SEND FOR APPROVAL", "n8n-nodes-base.slack", 2.3, {
        "select": "channel", "channelId": {"__rl": True, "value": "={{ $('CONFIG').first().json.SLACK_APPROVALS_CHANNEL }}", "mode": "id"},
        "otherOptions": {"includeLinkToWorkflow": False}, "messageType": "block", "blocksUi": "={{ $json.blocks_json }}"}, pos(28, 0), retryOnFail=True))
    N.append(at_update("SET pending_approval + slack ts", T_TOPICS, {"id": "={{ $('BUILD SLACK MESSAGE').first().json.record_id }}", "status": "pending_approval",
        "slack_message_ts": "={{ String($json.ts ?? $json.message_timestamp ?? $json.message?.ts ?? $json.message_ts ?? '') }}"}, pos(29, 0)))
    N.append(noop("Topic handed to reviewer", pos(30, 0)))
    N.append(if_true("IF RETRY LEFT", "$json.attempt < Number($('CONFIG').first().json.LLM_MAX_ATTEMPTS || 3)", pos(26, 2)))
    N.append(at_update("Mark FAILED", T_TOPICS, {"id": "={{ $json.record_id }}", "status": "failed", "error_message": "={{ $json.error_message }}", "draft_text": "={{ $json.post_text || '' }}"}, pos(27, 3)))
    N.append(slack_text("SLACK: ALERT FAILURE", "={{ $('CONFIG').first().json.SLACK_ALERTS_CHANNEL }}",
        "=:rotating_light: *{{ $('TOPIC READY').first().json.topic }}* could not be drafted.\n*Reason:* {{ $json.fields?.error_message ?? $json.error_message ?? '' }}\n*Status:* failed · execution {{ $execution.id }} · set the row back to queued to retry.", pos(28, 3)))

    # ---- notes
    N.append(sticky("Note stack", "## 1. Pop one topic\nRuns Mon/Wed/Fri 09:00 (" + TZ + ") or manually. `SEARCH STACK` reads every `queued` row plus `processing` rows whose claim is older than `STALE_CLAIM_HOURS` (crash safety net). `POP TOP` sorts pinned first, then newest `created_at`, and keeps **one**. Empty stack = quiet run, no alert.\n\n**Claim guard:** write `execution_id` + `claimed_at`, wait, read back, continue only if our id is still there. Airtable has no compare-and-swap; the schedule makes overlap impossible by construction, this is defence in depth.", pos(0, -2), 760, 220))
    N.append(sticky("Note classify", "## 2. Personal or general?\nIf the row already has `scope`, it is used as is. Otherwise `CLASSIFY REQUEST` → `GROQ CLASSIFY` (temperature 0, JSON) decides `scope` and fills `category` if empty. If the call fails, `PARSE CLASSIFICATION` falls back to a keyword rule and the Slack message says so. `SAVE SCOPE` writes the decision to the row so you can see and correct it.", pos(9, -2), 760, 180, 4))
    N.append(sticky("Note sources", "## 3. Sources\n`PROFILE LOOKUP` reads the `profile` table (active rows). **Personal** post: all rows become `author`. **General** post: only `use_for = always` rows (name, headline), then `TAVILY NEWS SEARCH` runs on `news_query` (or the topic). `NORMALIZE NEWS` keeps results with score ≥ `NEWS_MIN_SCORE` that mention the query; a Tavily failure never stops the run.", pos(15, -2), 760, 180, 5))
    N.append(sticky("Note draft", "## 4. Draft + guardrail\n`BUILD PROMPT` = system prompt (prompts/post-generation-system-prompt.md) + JSON input (author, topic, news, today). Primary `GROQ GENERATE`, failover `GEMINI GENERATE` on an llm_error. `PARSE + FACT GUARDRAIL`: length, every number must exist in author/topic/angle/news/today, named outlets must be in the sources, hashtag cap. Fail → retry at low temperature with the offending items named, up to `LLM_MAX_ATTEMPTS`, then `failed` + alert.", pos(21, -2), 760, 200, 6))
    N.append(sticky("Note approval", "## 5. Human approval in Slack (end of A)\nBlock Kit message: scope, category, news count, profile facts, model, the draft, review notes, facts used, source links, and **Approve / Reject / Edit** buttons carrying the record id. Row → `pending_approval` with `slack_message_ts`.\n\nNo Wait node: A ends here. Clicks arrive at Workflow B. State lives in Airtable, so a decision days later is fine.", pos(27, -2), 760, 200, 3))

    # ---- wires
    for s, d, o in [
        ("Schedule Mon/Wed/Fri 09:00", "CONFIG", 0), ("Manual Trigger", "CONFIG", 0), ("CONFIG", "SEARCH STACK", 0), ("SEARCH STACK", "POP TOP", 0),
        ("POP TOP", "IF STACK NOT EMPTY", 0), ("IF STACK NOT EMPTY", "CLAIM", 0), ("IF STACK NOT EMPTY", "Stack empty, nothing to do", 1),
        ("CLAIM", "CLAIM SETTLE", 0), ("CLAIM SETTLE", "READ-BACK", 0), ("READ-BACK", "IF CLAIM WON", 0),
        ("IF CLAIM WON", "IF SCOPE SET", 0), ("IF CLAIM WON", "Lost claim, stop", 1),
        ("IF SCOPE SET", "TOPIC READY", 0), ("IF SCOPE SET", "CLASSIFY REQUEST", 1),
        ("CLASSIFY REQUEST", "GROQ CLASSIFY", 0), ("GROQ CLASSIFY", "PARSE CLASSIFICATION", 0), ("PARSE CLASSIFICATION", "TOPIC READY", 0),
        ("TOPIC READY", "SAVE SCOPE", 0), ("SAVE SCOPE", "PROFILE LOOKUP", 0), ("PROFILE LOOKUP", "PROFILE READY", 0),
        ("PROFILE READY", "IF GENERAL", 0), ("IF GENERAL", "TAVILY NEWS SEARCH", 0), ("IF GENERAL", "NORMALIZE NEWS", 1),
        ("TAVILY NEWS SEARCH", "NORMALIZE NEWS", 0), ("NORMALIZE NEWS", "SAVE NEWS", 0), ("SAVE NEWS", "BUILD PROMPT", 0),
        ("BUILD PROMPT", "IF PROVIDER GEMINI", 0), ("IF PROVIDER GEMINI", "GEMINI GENERATE", 0), ("IF PROVIDER GEMINI", "GROQ GENERATE", 1),
        ("GEMINI GENERATE", "PARSE + FACT GUARDRAIL", 0), ("GROQ GENERATE", "PARSE + FACT GUARDRAIL", 0),
        ("PARSE + FACT GUARDRAIL", "IF DRAFT OK", 0), ("IF DRAFT OK", "SAVE DRAFT", 0), ("IF DRAFT OK", "IF RETRY LEFT", 1),
        ("SAVE DRAFT", "BUILD SLACK MESSAGE", 0), ("BUILD SLACK MESSAGE", "SLACK: SEND FOR APPROVAL", 0),
        ("SLACK: SEND FOR APPROVAL", "SET pending_approval + slack ts", 0), ("SET pending_approval + slack ts", "Topic handed to reviewer", 0),
        ("IF RETRY LEFT", "BUILD PROMPT", 0), ("IF RETRY LEFT", "Mark FAILED", 1), ("Mark FAILED", "SLACK: ALERT FAILURE", 0),
    ]:
        wire(C, s, d, o)
    return workflow("A – Preparer (pop topic, draft, send for approval)", N, C)


# --------------------------------------------------------------------------- Workflow B
def build_b():
    N, C = [], {}
    N.append(node("Slack Interactivity (webhook)", "n8n-nodes-base.webhook", 2, {"httpMethod": "POST", "path": "slack-interactions", "responseMode": "responseNode", "options": {"rawBody": True}}, pos(0, 1), webhookId=nid()))
    N.append(node("ACK 200 (empty body)", "n8n-nodes-base.respondToWebhook", 1.1, {"respondWith": "noData", "options": {"responseCode": 200}}, pos(1, 0)))
    N.append(code("PARSE PAYLOAD", r"""// PARSE PAYLOAD
// Slack posts application/x-www-form-urlencoded with one field: payload=<json>.
// The RAW body is needed for the signature (HMAC of "v0:<timestamp>:<raw body>"), so the webhook keeps it as binary.
const item = $input.first();
const headers = item.json.headers || {};
const slackTs = headers['x-slack-request-timestamp'];
const slackSig = headers['x-slack-signature'];
let raw = null, raw_source = 'none';
try { const buf = await this.helpers.getBinaryDataBuffer(0, 'data'); raw = buf.toString('utf8'); raw_source = 'binary-helper'; } catch (e) {}
if (!raw) { try { const b = item.binary && item.binary.data; if (b && b.data && !b.id) { raw = Buffer.from(b.data, 'base64').toString('utf8'); raw_source = 'binary-inline'; } } catch (e) {} }
let payloadStr = null;
if (raw) { for (const kv of raw.split('&')) { const i = kv.indexOf('='); if (i > 0 && kv.slice(0, i) === 'payload') payloadStr = decodeURIComponent(kv.slice(i + 1).replace(/\+/g, ' ')); } }
if (!payloadStr && item.json.body && item.json.body.payload) { payloadStr = item.json.body.payload; if (typeof payloadStr === 'object') payloadStr = JSON.stringify(payloadStr); raw_source = raw_source === 'none' ? 'parsed-body' : raw_source; }
const drop = (why) => [{ json: { action: 'unknown', dropped: why, raw_source, slack_signature: slackSig || '', slack_ts: slackTs || '', ts_fresh: false, sig_basestring: '' } }];
if (!payloadStr) return drop('no payload field');
if (!raw) raw = 'payload=' + encodeURIComponent(payloadStr);   // reconstructed body: the HMAC will not match, so this fails closed
let p;
try { p = JSON.parse(payloadStr); } catch (e) { return drop('payload is not JSON'); }
if (!p || typeof p !== 'object') return drop('payload is not an object');
const now = Math.floor(Date.now() / 1000);
const ts_fresh = !!slackTs && Math.abs(now - Number(slackTs)) < 300;   // replay protection: 5 minutes
let action = 'unknown', record_id = null, new_text = null, instruction = '', meta = {};
if (p.type === 'block_actions') {
  const a = (p.actions || [])[0] || {};
  action = a.action_id || 'unknown';
  try { const v = JSON.parse(a.value || '{}'); record_id = v.r || null; } catch (e) {}
} else if (p.type === 'view_submission' && p.view && p.view.callback_id === 'edit_post') {
  action = 'edit_submit';
  try { meta = JSON.parse(p.view.private_metadata || '{}'); } catch (e) {}
  record_id = meta.r || null;
  new_text = String(p.view.state?.values?.post_text?.text?.value || '').trim();
  instruction = String(p.view.state?.values?.ai_instruction?.text?.value || '').trim();
}
const user = (p.user && (p.user.username || p.user.name || p.user.id)) || 'unknown';
return [{ json: {
  action, record_id, new_text, instruction, has_instruction: instruction.length > 0, user, user_id: p.user ? p.user.id : null,
  trigger_id: p.trigger_id || null, response_url: p.response_url || null,
  channel: (p.channel && p.channel.id) || meta.channel || null,
  message_ts: (p.message && p.message.ts) || meta.ts || null,
  original_blocks: (p.message && p.message.blocks) || [],
  payload_type: p.type, slack_signature: slackSig, slack_ts: slackTs, ts_fresh, raw_source,
  sig_basestring: `v0:${slackTs}:${raw}`,
}}];
""", pos(2, 1)))
    N.append(set_config("CONFIG", [
        ("VERIFY_SIGNATURE", True), ("PUBLISH_MODE", "simulated"), ("LINKEDIN_PERSON_ID", "REPLACE_ME"),
        ("GROQ_MODEL", "openai/gpt-oss-120b"), ("GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions"),
        ("REWRITE_TEMPERATURE", 0.3), ("POST_MAX_CHARS", 1300), ("MAX_HASHTAGS", 3), ("POST_SIGNATURE", ""), ("TIMEZONE", TZ),
    ], pos(3, 1), include_other=True))
    N.append(node("HMAC (Slack signing secret)", "n8n-nodes-base.crypto", 1, {"action": "hmac", "type": "SHA256", "value": "={{ $json.sig_basestring }}", "dataPropertyName": "computed_sig", "secret": "={{ $vars.slack_signing_secret }}", "encoding": "hex"}, pos(4, 1)))
    N.append(if_true("IF SIGNATURE VALID", "$json.VERIFY_SIGNATURE === false || ($json.ts_fresh === true && ('v0=' + $json.computed_sig) === $json.slack_signature)", pos(5, 1)))
    N.append(noop("Dropped: bad signature or stale", pos(6, 3)))
    rules = []
    for key in ["approve", "reject", "edit", "edit_submit"]:
        rules.append({"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                                     "conditions": [{"id": nid(), "leftValue": "={{ $json.action }}", "rightValue": key, "operator": {"type": "string", "operation": "equals"}}], "combinator": "and"},
                      "renameOutput": True, "outputKey": key})
    N.append(node("Switch ACTION", "n8n-nodes-base.switch", 3.2, {"rules": {"values": rules}, "options": {"fallbackOutput": "extra", "renameFallbackOutput": "other"}}, pos(6, 1)))
    N.append(noop("Ignored: unknown action", pos(7, 5)))

    pending = "['pending_approval','changes_requested'].includes(String($json.fields?.status ?? $json.status))"
    reply = lambda name, text_js, p: node(name, "n8n-nodes-base.httpRequest", 4.2, {"method": "POST", "url": "={{ $('PARSE PAYLOAD').first().json.response_url }}", "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ response_type: 'ephemeral', replace_original: false, text: " + text_js + " }) }}", "options": {}}, p)

    # approve / reject
    N.append(at_get("GET TOPIC", T_TOPICS, "={{ $json.record_id }}", pos(7, 0)))
    N.append(if_true("IF STILL PENDING", pending, pos(8, 0)))
    N.append(reply("Reply: already handled", "`Already handled: this post is *${$json.fields?.status ?? $json.status}*` + (($json.fields?.reviewer ?? $json.reviewer) ? ` (by ${$json.fields?.reviewer ?? $json.reviewer})` : '') + '. No action taken.'", pos(9, 1)))
    N.append(at_update("CLAIM DECISION", T_TOPICS, {"id": "={{ $json.id }}", "status": "processing", "execution_id": "={{ $execution.id }}", "claimed_at": "={{ $now.toUTC().toISO() }}",
        "reviewer": "={{ $('PARSE PAYLOAD').first().json.user }}", "reviewed_at": "={{ $now.toUTC().toISO() }}"}, pos(9, 0), {"claimed_at": "dateTime", "reviewed_at": "dateTime"}))
    N.append(at_get("READ-BACK DECISION", T_TOPICS, "={{ $('GET TOPIC').first().json.id }}", pos(10, 0)))
    N.append(if_true("IF DECISION WON", "String($json.fields?.execution_id ?? $json.execution_id ?? '') === String($execution.id)", pos(11, 0)))
    N.append(reply("Reply: lost the race", "'Another reviewer acted on this post a moment ago. No action taken.'", pos(12, 1)))
    N.append(if_true("IF APPROVE", "$('PARSE PAYLOAD').first().json.action === 'approve'", pos(12, 0)))
    N.append(if_true("IF LINKEDIN MODE", "String($('CONFIG').first().json.PUBLISH_MODE) === 'linkedin'", pos(13, -1)))
    N.append(node("LINKEDIN: create post", "n8n-nodes-base.linkedIn", 1, {
        "postAs": "person", "person": "={{ $('CONFIG').first().json.LINKEDIN_PERSON_ID }}",
        "text": "={{ String($('GET TOPIC').first().json.draft_text || '').trim() }}", "shareMediaCategory": "NONE",
        "additionalFields": {"visibility": "PUBLIC"}}, pos(14, -2), retryOnFail=False))
    N.append(code("SIMULATE PUBLISH", r"""// SIMULATE PUBLISH (PUBLISH_MODE = simulated)
// Nothing leaves n8n. The row gets a fake post id so the status transition is identical to the real thing.
const ev = $('GET TOPIC').first().json;
return [{ json: { simulated: true, id: `sim-${Date.now().toString(36)}-${String(ev.id).slice(-6)}` } }];
""", pos(14, 0)))
    N.append(code("PUBLISH RESULT", r"""// PUBLISH RESULT: one shape whether the LinkedIn node or the simulation ran.
const cfg = $('CONFIG').first().json;
const ev = $('GET TOPIC').first().json;
const r = $input.first().json || {};
const text = String(ev.draft_text || '').trim();
const linkedin_post_id = String(r.id || r.urn || r['x-restli-id'] || r.activity || (r.simulated ? 'sim' : JSON.stringify(r).slice(0, 80)));
return [{ json: { record_id: ev.id, topic: ev.topic, final_text: text, published_at: DateTime.now().toUTC().toISO(), linkedin_post_id, publish_mode: r.simulated ? 'simulated' : String(cfg.PUBLISH_MODE) } }];
""", pos(15, -1)))
    N.append(at_update("SET published", T_TOPICS, {"id": "={{ $json.record_id }}", "status": "published", "final_text": "={{ $json.final_text }}", "published_at": "={{ $json.published_at }}", "linkedin_post_id": "={{ $json.linkedin_post_id }}"}, pos(16, -1), {"published_at": "dateTime"}))
    N.append(at_update("SET rejected", T_TOPICS, {"id": "={{ $('GET TOPIC').first().json.id }}", "status": "rejected"}, pos(14, 1)))
    N.append(code("BUILD RESULT MESSAGE", r"""// BUILD RESULT MESSAGE
// Replace the approval message in place: same content, buttons removed, outcome line added (third idempotency layer).
const cfg = $('CONFIG').first().json;
const pp = $('PARSE PAYLOAD').first().json;
const ev = $('GET TOPIC').first().json;
const approved = pp.action === 'approve';
let pub = null; try { pub = $('PUBLISH RESULT').first().json; } catch (e) {}
const when = DateTime.now().setZone(cfg.TIMEZONE || 'UTC').toFormat('dd LLL yyyy HH:mm');
const outcome = approved
  ? `:white_check_mark: *Approved & published* by @${pp.user} · ${when}` + (pub ? ` · ${pub.publish_mode === 'simulated' ? 'simulated' : 'LinkedIn'} ${pub.linkedin_post_id}` : '')
  : `:x: *Rejected* by @${pp.user} · ${when}`;
/** @type {any[]} */
const blocks = (pp.original_blocks || []).filter(b => b.type !== 'actions' && b.type !== 'divider');
blocks.push({ type: 'divider' });
blocks.push({ type: 'context', elements: [{ type: 'mrkdwn', text: outcome }] });
return [{ json: { slack_body: { channel: pp.channel, ts: pp.message_ts, text: `${approved ? 'Published' : 'Rejected'}: ${ev.topic}`, blocks } } }];
""", pos(17, 0)))
    N.append(slack_http("SLACK: chat.update", "https://slack.com/api/chat.update", "={{ JSON.stringify($json.slack_body) }}", pos(18, 0)))
    N.append(code("ASSERT SLACK OK", r"""// Slack answers HTTP 200 even on failure; surface it so the execution shows red and D can alert.
const r = $input.first().json || {};
if (r.ok === false) throw new Error('Slack API error: ' + (r.error || 'unknown') + (r.response_metadata ? ' ' + JSON.stringify(r.response_metadata) : ''));
return [{ json: { ok: true, ts: r.ts || null } }];
""", pos(19, 0)))
    N.append(noop("Done", pos(20, 0)))

    # edit button -> modal
    N.append(at_get("GET TOPIC (edit)", T_TOPICS, "={{ $json.record_id }}", pos(7, 2)))
    N.append(if_true("IF STILL PENDING (edit)", pending, pos(8, 2)))
    N.append(reply("Reply: cannot edit", "`This post is already *${$json.fields?.status ?? $json.status}*; it can no longer be edited.`", pos(9, 3)))
    N.append(code("BUILD EDIT MODAL", r"""// BUILD EDIT MODAL (views.open must be called within 3 s of the click; the webhook already acked Slack)
const pp = $('PARSE PAYLOAD').first().json;
const ev = $input.first().json; const f = ev.fields || ev;
const draft = String(f.draft_text || '').slice(0, 3000);
const view = {
  type: 'modal', callback_id: 'edit_post',
  private_metadata: JSON.stringify({ r: ev.id, ts: pp.message_ts, channel: pp.channel }),
  title: { type: 'plain_text', text: 'Edit post' }, submit: { type: 'plain_text', text: 'Save & re-review' }, close: { type: 'plain_text', text: 'Cancel' },
  blocks: [
    { type: 'context', elements: [{ type: 'mrkdwn', text: `*${String(f.topic || '').slice(0, 120)}* · edits are saved to the row and the approval message is refreshed with new buttons.` }] },
    { type: 'input', block_id: 'post_text', label: { type: 'plain_text', text: 'Post text' },
      element: { type: 'plain_text_input', action_id: 'text', multiline: true, initial_value: draft, max_length: 3000 } },
    { type: 'input', block_id: 'ai_instruction', optional: true, label: { type: 'plain_text', text: 'Or ask the AI to change it (optional)' },
      hint: { type: 'plain_text', text: 'If filled, the AI rewrites the post per your instruction, the guardrail re-checks it, and the message is refreshed. Your manual edits above are ignored in that case.' },
      element: { type: 'plain_text_input', action_id: 'text', multiline: true, max_length: 500, placeholder: { type: 'plain_text', text: 'e.g. shorter, warmer, drop the hashtags, end with a question' } } },
  ],
};
return [{ json: { slack_body: { trigger_id: pp.trigger_id, view } } }];
""", pos(9, 2)))
    N.append(slack_http("SLACK: views.open", "https://slack.com/api/views.open", "={{ JSON.stringify($json.slack_body) }}", pos(10, 2)))
    N.append(code("ASSERT MODAL OPENED", r"""// views.open fails with ok:false (e.g. expired_trigger_id after 3 s). Make it visible instead of silent.
const r = $input.first().json || {};
if (r.ok === false) throw new Error('views.open failed: ' + (r.error || 'unknown'));
return [{ json: { ok: true } }];
""", pos(11, 2)))

    # modal submit
    N.append(at_get("GET TOPIC (submit)", T_TOPICS, "={{ $json.record_id }}", pos(7, 4)))
    N.append(if_true("IF STILL PENDING (submit)", pending, pos(8, 4)))
    N.append(slack_http("SLACK: edit ignored (thread)", "https://slack.com/api/chat.postMessage",
        "={{ JSON.stringify({ channel: $('PARSE PAYLOAD').first().json.channel, thread_ts: $('PARSE PAYLOAD').first().json.message_ts, text: `:no_entry: Edit not applied: this post is already *${$json.fields?.status ?? $json.status}*.` }) }}", pos(9, 5)))
    N.append(if_true("IF AI INSTRUCTION", "$('PARSE PAYLOAD').first().json.has_instruction === true", pos(9, 4)))
    N.append(at_update("SET edited draft", T_TOPICS, {"id": "={{ $('PARSE PAYLOAD').first().json.record_id }}", "draft_text": "={{ $('PARSE PAYLOAD').first().json.new_text }}", "status": "pending_approval"}, pos(10, 5)))
    N.append(at_search("PROFILE LOOKUP (rewrite)", T_PROFILE, "={active}=1", pos(10, 3), always=True))
    N.append(code("BUILD REWRITE PROMPT", "// BUILD REWRITE PROMPT (same system prompt as Workflow A + the reviewer's instruction)\n"
        "const cfg = $('CONFIG').first().json;\nconst pp = $('PARSE PAYLOAD').first().json;\nconst ev = $('GET TOPIC (submit)').first().json; const f = ev.fields || ev;\n"
        "const SYSTEM = (" + json.dumps(SYSTEM_PROMPT) + ").replace(/MAX_HASHTAGS/g, String(cfg.MAX_HASHTAGS ?? 3));\n"
        r"""const is_personal = String(f.scope || '') === 'personal';
const rows = $input.all().map(i => i.json).filter(r => r && r.id && r.key).filter(r => is_personal || String(r.use_for || 'always') === 'always');
const author = {}; rows.forEach(r => { author[String(r.key).trim()] = String(r.value || '').trim(); });
const author_text = Object.entries(author).map(([k, v]) => `${k}: ${v}`).join('\n');
let news = []; try { news = JSON.parse(f.news_json || '[]'); } catch (e) {}
news = (Array.isArray(news) ? news : []).map(n => ({ title: n.title, source: n.source, date: n.date, snippet: String(n.snippet || '').slice(0, 400), url: n.url }));
const payload = { author, topic: { title: f.topic, category: f.category || 'other', scope: f.scope || 'general', angle: f.angle || '' }, news: news.map(({ url, ...n }) => n), has_news: news.length > 0, today: DateTime.now().setZone(cfg.TIMEZONE || 'UTC').toISODate() };
const user = 'A human reviewer asked for a change to an existing draft. Rewrite the post applying this instruction while keeping every rule above.\n\n'
  + 'REVIEWER INSTRUCTION: ' + pp.instruction + '\n\nCURRENT DRAFT:\n' + String(f.draft_text || '') + '\n\nINPUT DATA (the only allowed source of facts):\n' + JSON.stringify(payload, null, 2) + '\n\nReturn the JSON object only.';
const groqModel = String(cfg.GROQ_MODEL || 'openai/gpt-oss-120b');
const request_body = { model: groqModel, temperature: Number(cfg.REWRITE_TEMPERATURE ?? 0.3), max_tokens: 4096, response_format: { type: 'json_object' }, messages: [{ role: 'system', content: SYSTEM }, { role: 'user', content: user }] };
if (groqModel.includes('gpt-oss')) request_body.reasoning_effort = 'low';
return [{ json: { request_body, url: String(cfg.GROQ_API_URL), record_id: ev.id, topic: f.topic, angle: f.angle || '', author_text, news_items: news } }];
""", pos(11, 3)))
    N.append(http_llm("GROQ REWRITE", pos(12, 3)))
    N.append(code("REWRITE GUARDRAIL", "// REWRITE GUARDRAIL: same deterministic rules as Workflow A, applied to the rewrite.\n"
        r"""const cfg = $('CONFIG').first().json;
const bp = $('BUILD REWRITE PROMPT').first().json;
const resp = $input.first().json || {};
const TZ = cfg.TIMEZONE || 'UTC';
const MAX_TAGS = Number(cfg.MAX_HASHTAGS ?? 3);
const fail = (reason, extra = {}) => [{ json: { ok: false, reason, offending: [], record_id: bp.record_id, ...extra } }];
if (resp.error) return fail('llm_error', { error_message: 'llm_error: ' + (typeof resp.error === 'string' ? resp.error : (resp.error.message || JSON.stringify(resp.error))).slice(0, 300) });
let out; try { out = JSON.parse(resp?.choices?.[0]?.message?.content || ''); } catch (e) { return fail('llm_error', { error_message: 'llm_error: model did not return valid JSON' }); }
let post = String(out.post_text || '').trim();
if (!post) return fail('llm_error', { error_message: 'llm_error: empty post_text' });
const signature = String(cfg.POST_SIGNATURE || '').trim();
if (signature && !post.includes(signature)) post = post.replace(/\s*$/, '') + '\n\n' + signature;
if (post.length > Number(cfg.POST_MAX_CHARS || 1300)) return fail('too_long', { error_message: `too_long: ${post.length}`, post_text: post });
const SRC = { author_text: bp.author_text || '', topic: bp.topic, angle: bp.angle, news_items: bp.news_items || [], signature };
""" + JS_GUARDRAIL_CORE + r"""
return [{ json: { ok: true, reason: 'ok', record_id: bp.record_id, post_text: post, warnings: Array.isArray(out.warnings) ? out.warnings : [], facts_used: Array.isArray(out.facts_used) ? out.facts_used : [] } }];
""", pos(13, 3)))
    N.append(if_true("IF REWRITE OK", "$json.ok === true", pos(14, 3)))
    N.append(at_update("SET rewritten draft", T_TOPICS, {"id": "={{ $json.record_id }}", "draft_text": "={{ $json.post_text }}", "status": "pending_approval"}, pos(15, 3)))
    N.append(slack_http("SLACK: rewrite rejected (thread)", "https://slack.com/api/chat.postMessage",
        "={{ JSON.stringify({ channel: $('PARSE PAYLOAD').first().json.channel, thread_ts: $('PARSE PAYLOAD').first().json.message_ts, text: ':no_entry: AI rewrite not applied (' + $json.reason + '): ' + ($json.error_message || '') + '\\nThe original draft above is unchanged. Edit it manually or try a different instruction.' }) }}", pos(15, 4)))
    N.append(code("REBUILD APPROVAL MESSAGE", r"""// REBUILD APPROVAL MESSAGE after an edit: same layout as Workflow A, with an "edited by" line and fresh buttons.
const cfg = $('CONFIG').first().json;
const pp = $('PARSE PAYLOAD').first().json;
const rec = $input.first().json; const f = rec.fields || rec;
const esc = (s) => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const value = JSON.stringify({ r: rec.id || pp.record_id });
const when = DateTime.now().setZone(cfg.TIMEZONE || 'UTC').toFormat('dd LLL yyyy HH:mm');
let rewrite = null; try { rewrite = $('REWRITE GUARDRAIL').first().json; if (!rewrite || rewrite.ok !== true) rewrite = null; } catch (e) {}
const text = rewrite ? rewrite.post_text : (pp.new_text || f.draft_text);
const how = rewrite
  ? `:robot_face: AI-rewritten on request of @${pp.user} · ${when} · instruction: "${esc(pp.instruction).slice(0, 120)}" · numbers, sources and hashtags re-checked by the guardrail`
  : `:pencil2: edited by @${pp.user} · ${when} · not re-checked by the guardrail (manual edit)`;
/** @type {any[]} */
const blocks = [
  { type: 'header', text: { type: 'plain_text', text: `Post approval: ${f.topic || ''}`.slice(0, 150), emoji: true } },
  { type: 'context', elements: [{ type: 'mrkdwn', text: `*${esc(f.scope)}* · ${esc(f.category)}\n${how}` }] },
];
if (rewrite && rewrite.warnings.length) blocks.push({ type: 'section', text: { type: 'mrkdwn', text: ':warning: *Review notes*\n' + rewrite.warnings.map(w => '• ' + esc(w)).join('\n') } });
blocks.push({ type: 'section', text: { type: 'mrkdwn', text: esc(text).slice(0, 2900) } });
blocks.push({ type: 'divider' });
blocks.push({ type: 'actions', block_id: 'post_approval', elements: [
  { type: 'button', action_id: 'approve', style: 'primary', text: { type: 'plain_text', text: 'Approve & publish', emoji: true }, value },
  { type: 'button', action_id: 'reject', style: 'danger', text: { type: 'plain_text', text: 'Reject', emoji: true }, value,
    confirm: { title: { type: 'plain_text', text: 'Reject this post?' }, text: { type: 'mrkdwn', text: 'The topic will be marked *rejected* and nothing is published.' }, confirm: { type: 'plain_text', text: 'Reject' }, deny: { type: 'plain_text', text: 'Cancel' } } },
  { type: 'button', action_id: 'edit', text: { type: 'plain_text', text: 'Edit…', emoji: true }, value },
]});
return [{ json: { slack_body: { channel: pp.channel, ts: pp.message_ts, text: `Post approval (edited): ${f.topic || ''}`, blocks } } }];
""", pos(16, 4)))

    N.append(sticky("Note B overview", "## Workflow B: Resolver (Slack → decision → state)\nSlack Interactivity posts every button click / modal submit here. `ACK 200` answers Slack first (3 s rule), then the work happens.\n\n**Verify:** HMAC-SHA256 of `v0:<timestamp>:<raw body>` with the signing secret (`$vars.slack_signing_secret`, an n8n Variable), compared to `X-Slack-Signature`, plus a 5-minute freshness window. `VERIFY_SIGNATURE=false` in CONFIG only for local testing.", pos(0, -1), 760, 200))
    N.append(sticky("Note idempotency", "## Three layers against double-handling\n1. **Status gate:** only `pending_approval` / `changes_requested` may proceed; otherwise an ephemeral 'already handled' reply.\n2. **Claim + read-back:** write `processing` + this execution id + reviewer, read back, continue only if our id is still there.\n3. **Buttons removed:** the message is replaced in place with the outcome line, so a stale client cannot click twice.", pos(7, -2), 760, 180, 4))
    N.append(sticky("Note publish", "## Publish\n`PUBLISH_MODE` in CONFIG: `simulated` (default, nothing leaves n8n, a `sim-…` id is stored) or `linkedin` (the LinkedIn node posts `draft_text` as the person in `LINKEDIN_PERSON_ID`, credential `LinkedIn account`, text only). `PUBLISH RESULT` normalises both into `linkedin_post_id`, `final_text`, `published_at`.", pos(13, -3), 760, 160, 3))
    N.append(sticky("Note edit", "## Edit before approval\n`Edit…` and the modal submit both pass the status gate first. **Direct edit:** `draft_text` updated, message refreshed with new buttons (not re-checked; the context line says so). **AI instruction:** profile rows + stored news are re-loaded, the model rewrites, `REWRITE GUARDRAIL` re-checks, then the message is refreshed. A failed rewrite posts the reason in the thread and leaves the draft unchanged.", pos(7, 6), 760, 180, 5))

    for s, d, o in [
        ("Slack Interactivity (webhook)", "ACK 200 (empty body)", 0), ("ACK 200 (empty body)", "PARSE PAYLOAD", 0), ("PARSE PAYLOAD", "CONFIG", 0),
        ("CONFIG", "HMAC (Slack signing secret)", 0), ("HMAC (Slack signing secret)", "IF SIGNATURE VALID", 0),
        ("IF SIGNATURE VALID", "Switch ACTION", 0), ("IF SIGNATURE VALID", "Dropped: bad signature or stale", 1),
        ("Switch ACTION", "GET TOPIC", 0), ("Switch ACTION", "GET TOPIC", 1), ("Switch ACTION", "GET TOPIC (edit)", 2), ("Switch ACTION", "GET TOPIC (submit)", 3), ("Switch ACTION", "Ignored: unknown action", 4),
        ("GET TOPIC", "IF STILL PENDING", 0), ("IF STILL PENDING", "CLAIM DECISION", 0), ("IF STILL PENDING", "Reply: already handled", 1),
        ("CLAIM DECISION", "READ-BACK DECISION", 0), ("READ-BACK DECISION", "IF DECISION WON", 0), ("IF DECISION WON", "IF APPROVE", 0), ("IF DECISION WON", "Reply: lost the race", 1),
        ("IF APPROVE", "IF LINKEDIN MODE", 0), ("IF APPROVE", "SET rejected", 1),
        ("IF LINKEDIN MODE", "LINKEDIN: create post", 0), ("IF LINKEDIN MODE", "SIMULATE PUBLISH", 1),
        ("LINKEDIN: create post", "PUBLISH RESULT", 0), ("SIMULATE PUBLISH", "PUBLISH RESULT", 0), ("PUBLISH RESULT", "SET published", 0),
        ("SET published", "BUILD RESULT MESSAGE", 0), ("SET rejected", "BUILD RESULT MESSAGE", 0),
        ("BUILD RESULT MESSAGE", "SLACK: chat.update", 0), ("SLACK: chat.update", "ASSERT SLACK OK", 0), ("ASSERT SLACK OK", "Done", 0),
        ("GET TOPIC (edit)", "IF STILL PENDING (edit)", 0), ("IF STILL PENDING (edit)", "BUILD EDIT MODAL", 0), ("IF STILL PENDING (edit)", "Reply: cannot edit", 1),
        ("BUILD EDIT MODAL", "SLACK: views.open", 0), ("SLACK: views.open", "ASSERT MODAL OPENED", 0),
        ("GET TOPIC (submit)", "IF STILL PENDING (submit)", 0), ("IF STILL PENDING (submit)", "IF AI INSTRUCTION", 0), ("IF STILL PENDING (submit)", "SLACK: edit ignored (thread)", 1),
        ("IF AI INSTRUCTION", "PROFILE LOOKUP (rewrite)", 0), ("IF AI INSTRUCTION", "SET edited draft", 1),
        ("PROFILE LOOKUP (rewrite)", "BUILD REWRITE PROMPT", 0), ("BUILD REWRITE PROMPT", "GROQ REWRITE", 0), ("GROQ REWRITE", "REWRITE GUARDRAIL", 0),
        ("REWRITE GUARDRAIL", "IF REWRITE OK", 0), ("IF REWRITE OK", "SET rewritten draft", 0), ("IF REWRITE OK", "SLACK: rewrite rejected (thread)", 1),
        ("SET rewritten draft", "REBUILD APPROVAL MESSAGE", 0), ("SET edited draft", "REBUILD APPROVAL MESSAGE", 0), ("REBUILD APPROVAL MESSAGE", "SLACK: chat.update", 0),
    ]:
        wire(C, s, d, o)
    return workflow("B – Resolver (approve / reject / edit / publish)", N, C)


# --------------------------------------------------------------------------- Workflow C
def build_c():
    N, C = [], {}
    N.append(node("Schedule every 4 h", "n8n-nodes-base.scheduleTrigger", 1.2, {"rule": {"interval": [{"field": "hours", "hoursInterval": 4}]}}, pos(0, 0)))
    N.append(node("Manual Trigger", "n8n-nodes-base.manualTrigger", 1, {}, pos(0, 1)))
    N.append(set_config("CONFIG", [("REMIND_AFTER_HOURS", 24), ("REMIND_EVERY_HOURS", 24), ("MENTION", "<!channel>"), ("SLACK_APPROVALS_CHANNEL", SLACK_APPROVALS)], pos(1, 0), include_other=True))
    N.append(at_search("Airtable: pending approvals", T_TOPICS, "={status}='pending_approval'", pos(2, 0)))
    N.append(code("DUE FOR REMINDER", r"""// DUE FOR REMINDER
// pending_approval for more than REMIND_AFTER_HOURS (since last change, Airtable's updated_at), and not reminded in the last REMIND_EVERY_HOURS.
const cfg = $('CONFIG').first().json;
const after = Number(cfg.REMIND_AFTER_HOURS ?? 24), every = Number(cfg.REMIND_EVERY_HOURS ?? 24);
const now = DateTime.now();
const out = [];
for (const item of $input.all()) {
  const r = item.json; if (!r || !r.id || !r.slack_message_ts) continue;
  const changed = r.updated_at ? DateTime.fromISO(String(r.updated_at)) : null;
  const reminded = r.last_reminded_at ? DateTime.fromISO(String(r.last_reminded_at)) : null;
  const waitingH = changed && changed.isValid ? now.diff(changed, 'hours').hours : Infinity;
  const sinceRemindH = reminded && reminded.isValid ? now.diff(reminded, 'hours').hours : Infinity;
  if (waitingH >= after && sinceRemindH >= every) {
    out.push({ json: { id: r.id, topic: r.topic, slack_message_ts: r.slack_message_ts, waiting_hours: Math.round(waitingH),
      reminder_text: `${cfg.MENTION} reminder: *${r.topic}* has been waiting for approval for ${Math.round(waitingH)} h. Please approve, reject or edit above.` } });
  }
}
return out;
""", pos(3, 0)))
    N.append(slack_http("SLACK: thread reminder", "https://slack.com/api/chat.postMessage", "={{ JSON.stringify({ channel: $('CONFIG').first().json.SLACK_APPROVALS_CHANNEL, thread_ts: $json.slack_message_ts, text: $json.reminder_text, reply_broadcast: true }) }}", pos(4, 0)))
    N.append(at_update("SET last_reminded_at", T_TOPICS, {"id": "={{ $('DUE FOR REMINDER').item.json.id }}", "last_reminded_at": "={{ $now.toUTC().toISO() }}"}, pos(5, 0), {"last_reminded_at": "dateTime"}))
    N.append(sticky("Note C", "## Workflow C: reminders\nEvery 4 h: posts still `pending_approval` longer than `REMIND_AFTER_HOURS` get a thread reply under their approval message (broadcast to the channel), at most once per `REMIND_EVERY_HOURS`, tracked in `last_reminded_at`.", pos(0, -1), 760, 120))
    for s, d in [("Schedule every 4 h", "CONFIG"), ("Manual Trigger", "CONFIG"), ("CONFIG", "Airtable: pending approvals"), ("Airtable: pending approvals", "DUE FOR REMINDER"), ("DUE FOR REMINDER", "SLACK: thread reminder"), ("SLACK: thread reminder", "SET last_reminded_at")]:
        wire(C, s, d)
    return workflow("C – Reminders", N, C)


# --------------------------------------------------------------------------- Workflow D
def build_d():
    N, C = [], {}
    N.append(node("Error Trigger", "n8n-nodes-base.errorTrigger", 1, {}, pos(0, 0)))
    N.append(code("EXTRACT ERROR", r"""// EXTRACT ERROR (n8n Error Trigger payload)
const e = $input.first().json || {};
const ex = e.execution || {}, wf = e.workflow || {};
const err = ex.error || {};
const message = String(err.message || err.description || 'unknown error').slice(0, 500);
return [{ json: {
  failed_execution_id: String(ex.id || ''), execution_url: ex.url || '', workflow_name: wf.name || '', workflow_id: wf.id || '',
  last_node: ex.lastNodeExecuted || '', error_message: message, mode: ex.mode || '', when: DateTime.now().setZone('""" + TZ + r"""').toFormat('dd LLL yyyy HH:mm'),
  airtable_error: `crash in ${wf.name || 'workflow'} at "${ex.lastNodeExecuted || '?'}": ${message}`.slice(0, 900),
} }];
""", pos(1, 0)))
    N.append(at_search("Airtable: rows claimed by that run", T_TOPICS, "=AND({status}='processing', {execution_id}='{{ $json.failed_execution_id }}')", pos(2, 0), always=True))
    N.append(if_true("IF any record", "!!$json.id", pos(3, 0)))
    N.append(at_update("SET failed", T_TOPICS, {"id": "={{ $json.id }}", "status": "failed", "error_message": "={{ $('EXTRACT ERROR').first().json.airtable_error }}"}, pos(4, -1)))
    N.append(code("BUILD ALERT", r"""// BUILD ALERT (one message per crash, listing affected topics if any)
const er = $('EXTRACT ERROR').first().json;
let affected = [];
try { affected = $('Airtable: rows claimed by that run').all().map(i => i.json).filter(r => r && r.id).map(r => `• ${r.topic || r.id} → failed`); } catch (e) {}
const lines = [
  `:rotating_light: *Workflow crashed: ${er.workflow_name}*`,
  `*Node:* ${er.last_node || '?'}`,
  `*Error:* ${er.error_message}`,
  affected.length ? `*Topics marked failed:*\n${affected.join('\n')}` : '_No topic was mid-flight (failure before the claim), nothing to update in Airtable._',
  er.execution_url ? `<${er.execution_url}|Open execution ${er.failed_execution_id}>` : `execution ${er.failed_execution_id}`,
];
return [{ json: { slack_body: { channel: '""" + SLACK_ALERTS + r"""', text: lines.join('\n'), unfurl_links: false } } }];
""", pos(5, 0)))
    N.append(slack_http("SLACK: alert", "https://slack.com/api/chat.postMessage", "={{ JSON.stringify($json.slack_body) }}", pos(6, 0)))
    N.append(sticky("Note D", "## Workflow D: global error handler\nSet as *Error workflow* in the settings of A, B and C. Fires on any uncaught crash. Marks rows still `processing` under the crashed execution id as `failed` (so nothing is stranded until the stale-claim re-pick), then posts to the alerts channel.", pos(0, -1), 760, 120))
    for s, d, o in [("Error Trigger", "EXTRACT ERROR", 0), ("EXTRACT ERROR", "Airtable: rows claimed by that run", 0), ("Airtable: rows claimed by that run", "IF any record", 0),
                    ("IF any record", "SET failed", 0), ("IF any record", "BUILD ALERT", 1), ("SET failed", "BUILD ALERT", 0), ("BUILD ALERT", "SLACK: alert", 0)]:
        wire(C, s, d, o)
    return workflow("D – Error handler", N, C)


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    for fname, wf in [("A-preparer.json", build_a()), ("B-resolver.json", build_b()), ("C-reminders.json", build_c()), ("D-error-handler.json", build_d())]:
        (OUT / fname).write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n")
        real = [n for n in wf["nodes"] if not n["type"].endswith("stickyNote")]
        print(f"{fname}: {len(real)} nodes (+{len(wf['nodes']) - len(real)} notes)")
