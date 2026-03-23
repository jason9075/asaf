#!/usr/bin/env python3
"""Local web viewer for ASAF pipeline data."""

import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DEFAULT_DB = Path("db/asaf.db")
DEFAULT_SEGS = Path("data/sessions.jsonl")
DEFAULT_MSGS = Path("data/messages.jsonl")
PAGE_SIZE = 50

# ── Data loaders ──────────────────────────────────────────────────────────────

def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def search_sessions(
    sessions: list[dict], q: str, page: int
) -> tuple[list[dict], int]:
    q = q.lower()
    if q:
        results = [
            s for s in sessions
            if q in s.get("session_id", "").lower()
            or any(q in m.get("content", "").lower() for m in s.get("messages", []))
            or any(q in m.get("display_name", "").lower() for m in s.get("messages", []))
        ]
    else:
        results = sessions
    total = len(results)
    return results[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], total


def search_messages(
    messages: list[dict], q: str, author: str, page: int
) -> tuple[list[dict], int]:
    q = q.lower()
    author = author.lower()
    results = messages
    if q:
        results = [m for m in results if q in m.get("content", "").lower()]
    if author:
        results = [m for m in results if author in m.get("display_name", "").lower()]
    total = len(results)
    return results[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], total


# ── HTTP Handler ───────────────────────────────────────────────────────────────

class ViewerHandler(BaseHTTPRequestHandler):
    # Set by main() before server starts
    conn: sqlite3.Connection
    sessions: list[dict]
    messages: list[dict]

    def log_message(self, fmt: str, *args: object) -> None:  # type: ignore[override]
        pass  # suppress default logging

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        try:
            if path == "/":
                self._handle_index()
            elif path == "/api/fragments":
                self._handle_fragments(qs)
            elif path == "/api/fragments/meta":
                self._handle_fragment_meta()
            elif path.startswith("/api/fragments/"):
                sid = path[len("/api/fragments/"):]
                self._handle_fragment(sid)
            elif path == "/api/sessions":
                self._handle_sessions(qs)
            elif path.startswith("/api/sessions/"):
                sid = path[len("/api/sessions/"):]
                self._handle_session(sid)
            elif path == "/api/messages":
                self._handle_messages(qs)
            else:
                self._send(404, b"Not found")
        except Exception as e:
            self._json({"error": str(e)}, status=500)

    def _handle_index(self) -> None:
        body = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_fragments(self, qs: dict) -> None:
        q = qs.get("q", [""])[0]
        urgency = qs.get("urgency", [""])[0]
        model_filter = qs.get("model", [""])[0]
        conf_sort = qs.get("conf_sort", [""])[0]   # "asc" | "desc" | ""
        start_sort = qs.get("start_sort", [""])[0]  # "asc" | "desc" | ""
        page = int(qs.get("page", ["0"])[0])

        conditions: list[str] = []
        params: list[str] = []

        if q:
            like = f"%{q}%"
            conditions.append(
                "(json_extract(session_metadata,'$.primary_topic') LIKE ?"
                " OR json_extract(indexing_metadata,'$.technologies') LIKE ?"
                " OR json_extract(indexing_metadata,'$.entities') LIKE ?"
                " OR json_extract(indexing_metadata,'$.concepts') LIKE ?)"
            )
            params.extend([like, like, like, like])

        if urgency:
            conditions.append("json_extract(session_metadata,'$.urgency_level') = ?")
            params.append(urgency)

        if model_filter == "__null__":
            conditions.append("model IS NULL")
        elif model_filter:
            conditions.append("model = ?")
            params.append(model_filter)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        if conf_sort == "asc":
            order = "ORDER BY CAST(json_extract(session_metadata,'$.confidence_score') AS REAL) ASC"
        elif conf_sort == "desc":
            order = "ORDER BY CAST(json_extract(session_metadata,'$.confidence_score') AS REAL) DESC"
        elif start_sort == "asc":
            order = "ORDER BY start ASC"
        elif start_sort == "desc":
            order = "ORDER BY start DESC"
        else:
            order = "ORDER BY start DESC"

        sql = (
            f"SELECT session_id, start, end, session_metadata, indexing_metadata, model"
            f" FROM profile_fragments {where} {order}"
        )
        cur = self.conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        total = len(rows)
        page_rows = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        items = []
        for row in page_rows:
            sid, start, end, meta_raw, idx_raw, model = row
            meta = json.loads(meta_raw)
            idx = json.loads(idx_raw)
            items.append({
                "session_id": sid,
                "start": start,
                "end": end,
                "primary_topic": meta.get("primary_topic", ""),
                "confidence": meta.get("confidence_score", ""),
                "urgency": meta.get("urgency_level", ""),
                "model": model or "",
                "technologies": idx.get("technologies", []),
                "entities": idx.get("entities", []),
                "concepts": idx.get("concepts", []),
                "action_items": idx.get("action_items", []),
            })
        self._json({"items": items, "total": total, "page": page})

    def _handle_fragment_meta(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT DISTINCT json_extract(session_metadata,'$.urgency_level')"
            " FROM profile_fragments"
        )
        urgencies = sorted(r[0] for r in cur.fetchall() if r[0])
        cur.execute("SELECT DISTINCT model FROM profile_fragments")
        rows = cur.fetchall()
        models = sorted(r[0] for r in rows if r[0] is not None)
        has_null = any(r[0] is None for r in rows)
        self._json({"urgencies": urgencies, "models": models, "has_null": has_null})

    def _handle_fragment(self, session_id: str) -> None:
        cur = self.conn.cursor()
        cur.execute(
            "SELECT * FROM profile_fragments WHERE session_id = ?", (session_id,)
        )
        row = cur.fetchone()
        if not row:
            self._json({"error": "not found"}, status=404)
            return
        cols = [d[0] for d in cur.description]
        data = dict(zip(cols, row))
        for field in ("session_metadata", "participants", "discussion_outline", "indexing_metadata"):
            try:
                data[field] = json.loads(data[field])
            except Exception:
                pass
        self._json(data)

    def _handle_sessions(self, qs: dict) -> None:
        q = qs.get("q", [""])[0]
        page = int(qs.get("page", ["0"])[0])
        items, total = search_sessions(self.sessions, q, page)
        slim = [
            {
                "session_id": s["session_id"],
                "start": s.get("start", ""),
                "end": s.get("end", ""),
                "message_count": s.get("message_count", len(s.get("messages", []))),
            }
            for s in items
        ]
        self._json({"items": slim, "total": total, "page": page})

    def _handle_session(self, session_id: str) -> None:
        match = next((s for s in self.sessions if s["session_id"] == session_id), None)
        if not match:
            self._json({"error": "not found"}, status=404)
            return
        self._json(match)

    def _handle_messages(self, qs: dict) -> None:
        q = qs.get("q", [""])[0]
        author = qs.get("author", [""])[0]
        page = int(qs.get("page", ["0"])[0])
        items, total = search_messages(self.messages, q, author, page)
        self._json({"items": items, "total": total, "page": page})

    def _json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ── HTML / SPA ─────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ASAF Viewer</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#1a1a1a;color:#d4d4d4;font-family:monospace;font-size:13px}
  a{color:#7cb4f0;text-decoration:none}
  header{background:#111;padding:10px 16px;display:flex;align-items:center;gap:16px;border-bottom:1px solid #333}
  header h1{font-size:16px;color:#e8e8e8;letter-spacing:1px}
  nav button{background:none;border:none;color:#888;cursor:pointer;padding:6px 12px;font-family:monospace;font-size:13px;border-radius:4px}
  nav button.active{background:#2a2a2a;color:#e8e8e8;border:1px solid #444}
  .search-bar{display:flex;gap:8px;padding:10px 16px;background:#111;border-bottom:1px solid #2a2a2a}
  .search-bar input{background:#222;border:1px solid #444;color:#d4d4d4;padding:5px 10px;font-family:monospace;font-size:13px;border-radius:3px;flex:1}
  .search-bar input:focus{outline:none;border-color:#7cb4f0}
  .search-bar button{background:#2a4a6a;border:none;color:#ccc;padding:5px 14px;cursor:pointer;font-family:monospace;font-size:13px;border-radius:3px}
  .search-bar button:hover{background:#3a5a7a}
  main{padding:12px 16px}
  table{width:100%;border-collapse:collapse}
  th{text-align:left;color:#888;padding:6px 8px;border-bottom:1px solid #333;font-weight:normal}
  td{padding:5px 8px;border-bottom:1px solid #222;vertical-align:top}
  tr.clickable:hover td{background:#222;cursor:pointer}
  .tag{display:inline-block;background:#2a3a2a;color:#8fc08f;padding:1px 6px;border-radius:3px;margin:1px;font-size:11px}
  .tag.tech{background:#2a2a3a;color:#8fa8c0}
  .tag.entity{background:#3a2a2a;color:#c08f8f}
  .tag.concept{background:#3a3a2a;color:#c0b88f}
  .tag.action{background:#3a2a3a;color:#b88fc0}
  .detail{background:#1e1e1e;border:1px solid #333;border-radius:4px;padding:12px;margin-top:4px;display:none}
  .detail.open{display:block}
  .detail h3{color:#aaa;font-size:12px;margin-bottom:8px;text-transform:uppercase;letter-spacing:1px}
  .detail-section{margin-bottom:12px}
  .detail-section h4{color:#777;font-size:11px;margin-bottom:4px;text-transform:uppercase}
  .outline-item{border-left:2px solid #444;padding-left:8px;margin-bottom:8px}
  .outline-item .range{color:#666;font-size:11px}
  .outline-item .title{color:#b0c8e0;font-weight:bold;margin:2px 0}
  .outline-item .summary{color:#ccc}
  .outline-item.resolved::before{content:"✓ ";color:#8fc08f}
  .msg-list{max-height:500px;overflow-y:auto}
  .msg{padding:4px 0;border-bottom:1px solid #222;display:flex;gap:8px}
  .msg .ts{color:#555;min-width:140px;flex-shrink:0}
  .msg .author{color:#7cb4f0;min-width:120px;flex-shrink:0}
  .msg .content{color:#ccc;word-break:break-word}
  .pagination{display:flex;align-items:center;gap:8px;padding:10px 0;color:#666}
  .pagination button{background:#222;border:1px solid #444;color:#aaa;padding:4px 12px;cursor:pointer;font-family:monospace;font-size:12px;border-radius:3px}
  .pagination button:hover:not(:disabled){background:#2a2a2a;color:#eee}
  .pagination button:disabled{opacity:0.4;cursor:default}
  .count{color:#666;font-size:12px;padding:4px 0}
  .conf{font-size:11px;color:#888}
  .urgency-low{color:#8fc08f}.urgency-medium{color:#c0b88f}.urgency-high{color:#c08f8f}
  .empty{color:#555;padding:20px;text-align:center}
  .participant{display:flex;gap:8px;padding:3px 0;font-size:12px}
  .participant .role{color:#888;min-width:140px}
  .participant .weight{color:#555}
</style>
</head>
<body>
<header>
  <h1>ASAF Viewer</h1>
  <nav>
    <button id="tab-fragments" class="active" onclick="showTab('fragments')">Fragments</button>
    <button id="tab-sessions" onclick="showTab('sessions')">Sessions</button>
    <button id="tab-messages" onclick="showTab('messages')">Messages</button>
  </nav>
</header>

<div id="pane-fragments">
  <div class="search-bar">
    <input id="frag-q" placeholder="Search topic / tech / entity / concept…" onkeydown="if(event.key==='Enter')fragSearch()">
    <select id="frag-urgency" onchange="fragSearch()">
      <option value="">All urgency</option>
      <option value="low">low</option>
      <option value="medium">medium</option>
      <option value="high">high</option>
    </select>
    <select id="frag-model" onchange="fragSearch()">
      <option value="">All models</option>
    </select>
    <button onclick="fragSearch()">Search</button>
  </div>
  <main>
    <div class="count" id="frag-count"></div>
    <table>
      <thead><tr>
        <th>Session</th>
        <th id="th-start" onclick="fragToggleStartSort()" style="cursor:pointer;user-select:none">Start → End <span id="start-sort-icon">▼</span></th>
        <th>Topic</th>
        <th id="th-conf" onclick="fragToggleConfSort()" style="cursor:pointer;user-select:none">Conf <span id="conf-sort-icon"></span></th>
        <th>Urgency</th><th>Model</th><th>Tags</th>
      </tr></thead>
      <tbody id="frag-tbody"></tbody>
    </table>
    <div class="pagination">
      <button id="frag-prev" onclick="fragPage(-1)" disabled>◀ Prev</button>
      <span id="frag-page-info"></span>
      <button id="frag-next" onclick="fragPage(1)" disabled>Next ▶</button>
    </div>
  </main>
</div>

<div id="pane-sessions" style="display:none">
  <div class="search-bar">
    <input id="sess-q" placeholder="Search session_id or message content…" onkeydown="if(event.key==='Enter')sessSearch()">
    <button onclick="sessSearch()">Search</button>
  </div>
  <main>
    <div class="count" id="sess-count"></div>
    <table>
      <thead><tr>
        <th>Session</th><th>Start → End</th><th>Messages</th>
      </tr></thead>
      <tbody id="sess-tbody"></tbody>
    </table>
    <div class="pagination">
      <button id="sess-prev" onclick="sessPage(-1)" disabled>◀ Prev</button>
      <span id="sess-page-info"></span>
      <button id="sess-next" onclick="sessPage(1)" disabled>Next ▶</button>
    </div>
  </main>
</div>

<div id="pane-messages" style="display:none">
  <div class="search-bar">
    <input id="msg-q" placeholder="Search content…" onkeydown="if(event.key==='Enter')msgSearch()" style="flex:2">
    <input id="msg-author" placeholder="Author filter…" onkeydown="if(event.key==='Enter')msgSearch()">
    <button onclick="msgSearch()">Search</button>
  </div>
  <main>
    <div class="count" id="msg-count"></div>
    <div id="msg-list" class="msg-list"></div>
    <div class="pagination">
      <button id="msg-prev" onclick="msgPage(-1)" disabled>◀ Prev</button>
      <span id="msg-page-info"></span>
      <button id="msg-next" onclick="msgPage(1)" disabled>Next ▶</button>
    </div>
  </main>
</div>

<script>
const PAGE = 50;

// ── Tab switching ──────────────────────────────────────────────────────────────
function showTab(name) {
  ['fragments','sessions','messages'].forEach(t => {
    document.getElementById('pane-'+t).style.display = t===name ? '' : 'none';
    document.getElementById('tab-'+t).classList.toggle('active', t===name);
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function tags(arr, cls) {
  return (arr||[]).map(t => `<span class="tag ${cls}">${esc(t)}</span>`).join('');
}
function urgencyClass(u) {
  return u === 'high' ? 'urgency-high' : u === 'medium' ? 'urgency-medium' : 'urgency-low';
}
function updatePager(prevId, nextId, infoId, page, total) {
  const pages = Math.max(1, Math.ceil(total / PAGE));
  document.getElementById(prevId).disabled = page <= 0;
  document.getElementById(nextId).disabled = page >= pages - 1;
  document.getElementById(infoId).textContent = `Page ${page+1} / ${pages}`;
}

// ── Fragments ─────────────────────────────────────────────────────────────────
let fragState = {page:0, q:'', urgency:'', model:'', confSort:'', startSort:'desc'};

function fragSearch() {
  fragState.q       = document.getElementById('frag-q').value.trim();
  fragState.urgency = document.getElementById('frag-urgency').value;
  fragState.model   = document.getElementById('frag-model').value;
  fragState.page    = 0;
  fragLoad();
}
function fragPage(dir) { fragState.page += dir; fragLoad(); }
function fragToggleConfSort() {
  fragState.confSort  = fragState.confSort === '' ? 'asc' : fragState.confSort === 'asc' ? 'desc' : '';
  fragState.startSort = '';
  fragState.page = 0;
  fragLoad();
}
function fragToggleStartSort() {
  fragState.startSort = fragState.startSort === 'desc' ? 'asc' : 'desc';
  fragState.confSort  = '';
  fragState.page = 0;
  fragLoad();
}

async function initFragMeta() {
  const data = await (await fetch('/api/fragments/meta')).json();
  const sel = document.getElementById('frag-model');
  if (data.has_null) {
    const opt = document.createElement('option');
    opt.value = '__null__'; opt.textContent = '(none)';
    sel.appendChild(opt);
  }
  data.models.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m; opt.textContent = m;
    sel.appendChild(opt);
  });
}

async function fragLoad() {
  const {q, page, urgency, model, confSort, startSort} = fragState;
  const params = new URLSearchParams({q, page, urgency, model, conf_sort: confSort, start_sort: startSort});
  const res = await fetch(`/api/fragments?${params}`);
  const data = await res.json();
  const icons = {'asc':'▲', 'desc':'▼', '':''};
  document.getElementById('conf-sort-icon').textContent = icons[confSort];
  document.getElementById('start-sort-icon').textContent = icons[startSort];
  document.getElementById('frag-count').textContent = `${data.total} fragments`;
  updatePager('frag-prev','frag-next','frag-page-info', page, data.total);

  const tbody = document.getElementById('frag-tbody');
  tbody.innerHTML = data.items.length === 0
    ? '<tr><td colspan="7" class="empty">No results</td></tr>'
    : data.items.map(f => `
      <tr class="clickable" onclick="fragToggle('${esc(f.session_id)}')">
        <td><a href="#" onclick="event.preventDefault();event.stopPropagation();goToSession('${esc(f.session_id)}')">${esc(f.session_id)}</a></td>
        <td style="white-space:nowrap;color:#888">${esc(f.start.slice(0,16))} →<br>${esc(f.end.slice(0,16))}</td>
        <td>${esc(f.primary_topic)}</td>
        <td class="conf">${f.confidence || ''}</td>
        <td class="${urgencyClass(f.urgency)}">${esc(f.urgency)}</td>
        <td style="color:#777;font-size:11px">${esc(f.model)}</td>
        <td>${tags(f.technologies,'tech')}${tags(f.entities,'entity')}${tags(f.concepts,'concept')}</td>
      </tr>
      <tr id="detail-${esc(f.session_id)}" style="display:none">
        <td colspan="7"><div class="detail open" id="detail-div-${esc(f.session_id)}">Loading…</div></td>
      </tr>
    `).join('');
}

async function goToSession(sid) {
  showTab('sessions');
  document.getElementById('sess-q').value = sid;
  sessState.q = sid;
  sessState.page = 0;
  openSess = null;  // clear stale ref before DOM re-render
  await sessLoad();
  const row = document.getElementById('sess-detail-'+sid);
  if (row && row.style.display === 'none') sessToggle(sid);
}

let openFrag = null;
async function fragToggle(sid) {
  const row = document.getElementById('detail-'+sid);
  const div = document.getElementById('detail-div-'+sid);
  if (openFrag === sid) {
    row.style.display = 'none';
    openFrag = null;
    return;
  }
  if (openFrag) {
    document.getElementById('detail-'+openFrag).style.display = 'none';
  }
  openFrag = sid;
  row.style.display = '';
  if (div.textContent === 'Loading…') {
    const res = await fetch(`/api/fragments/${encodeURIComponent(sid)}`);
    const f = await res.json();
    div.innerHTML = renderFragDetail(f);
  }
}

function renderFragDetail(f) {
  const meta = f.session_metadata || {};
  const idx = f.indexing_metadata || {};
  const participants = (f.participants || []).map(p => `
    <div class="participant">
      <span class="role">${esc(p.role)}</span>
      <span style="color:#aaa">${esc(p.user_id)}</span>
      <span class="weight">(${((p.contribution_weight||0)*100).toFixed(0)}%)</span>
    </div>
  `).join('');

  const outline = (f.discussion_outline || []).map(o => `
    <div class="outline-item ${o.resolved ? 'resolved' : ''}">
      <div class="range">${esc(o.timestamp_range||'')}</div>
      <div class="title">${esc(o.section_title||'')}</div>
      <div class="summary">${esc(o.summary||'')}</div>
    </div>
  `).join('');

  return `
    <div class="detail-section">
      <h4>Participants</h4>${participants}
    </div>
    <div class="detail-section">
      <h4>Discussion Outline</h4>${outline}
    </div>
    <div class="detail-section">
      <h4>Indexing</h4>
      <div>Tech: ${tags(idx.technologies,'tech')}</div>
      <div>Entities: ${tags(idx.entities,'entity')}</div>
      <div>Concepts: ${tags(idx.concepts,'concept')}</div>
      <div>Action Items: ${tags(idx.action_items,'action')}</div>
    </div>
  `;
}

// ── Sessions ──────────────────────────────────────────────────────────────────
let sessState = {page:0, q:''};

function sessSearch() {
  sessState.q = document.getElementById('sess-q').value.trim();
  sessState.page = 0;
  sessLoad();
}
function sessPage(dir) { sessState.page += dir; sessLoad(); }

async function sessLoad() {
  const {q, page} = sessState;
  const res = await fetch(`/api/sessions?q=${encodeURIComponent(q)}&page=${page}`);
  const data = await res.json();
  document.getElementById('sess-count').textContent = `${data.total} sessions`;
  updatePager('sess-prev','sess-next','sess-page-info', page, data.total);

  const tbody = document.getElementById('sess-tbody');
  tbody.innerHTML = data.items.length === 0
    ? '<tr><td colspan="3" class="empty">No results</td></tr>'
    : data.items.map(s => `
      <tr class="clickable" onclick="sessToggle('${esc(s.session_id)}')">
        <td>${esc(s.session_id)}</td>
        <td style="white-space:nowrap;color:#888">${esc(s.start.slice(0,16))} →<br>${esc(s.end.slice(0,16))}</td>
        <td>${s.message_count}</td>
      </tr>
      <tr id="sess-detail-${esc(s.session_id)}" style="display:none">
        <td colspan="3"><div class="detail open" id="sess-div-${esc(s.session_id)}">Loading…</div></td>
      </tr>
    `).join('');
}

let openSess = null;
async function sessToggle(sid) {
  const row = document.getElementById('sess-detail-'+sid);
  const div = document.getElementById('sess-div-'+sid);
  if (openSess === sid) {
    row.style.display = 'none';
    openSess = null;
    return;
  }
  if (openSess) {
    const prevRow = document.getElementById('sess-detail-'+openSess);
    if (prevRow) prevRow.style.display = 'none';
  }
  openSess = sid;
  row.style.display = '';
  if (div.textContent === 'Loading…') {
    const res = await fetch(`/api/sessions/${encodeURIComponent(sid)}`);
    const s = await res.json();
    div.innerHTML = renderSessDetail(s);
  }
}

function renderSessDetail(s) {
  const msgs = (s.messages || []).map(m => `
    <div class="msg">
      <span class="ts">${esc(m.timestamp)}</span>
      <span class="author">${esc(m.display_name)}</span>
      <span class="content">${esc(m.content)}</span>
    </div>
  `).join('');
  return `<div class="detail-section"><h4>Messages (${(s.messages||[]).length})</h4><div class="msg-list">${msgs}</div></div>`;
}

// ── Messages ──────────────────────────────────────────────────────────────────
let msgState = {page:0, q:'', author:''};

function msgSearch() {
  msgState.q = document.getElementById('msg-q').value.trim();
  msgState.author = document.getElementById('msg-author').value.trim();
  msgState.page = 0;
  msgLoad();
}
function msgPage(dir) { msgState.page += dir; msgLoad(); }

async function msgLoad() {
  const {q, author, page} = msgState;
  const res = await fetch(`/api/messages?q=${encodeURIComponent(q)}&author=${encodeURIComponent(author)}&page=${page}`);
  const data = await res.json();
  document.getElementById('msg-count').textContent = `${data.total} messages`;
  updatePager('msg-prev','msg-next','msg-page-info', page, data.total);

  const list = document.getElementById('msg-list');
  list.innerHTML = data.items.length === 0
    ? '<div class="empty">No results</div>'
    : data.items.map(m => `
      <div class="msg">
        <span class="ts">${esc(m.timestamp)}</span>
        <span class="author">${esc(m.display_name)}</span>
        <span class="content">${esc(m.content)}</span>
      </div>
    `).join('');
}

// ── Init ──────────────────────────────────────────────────────────────────────
initFragMeta();
fragLoad();
sessLoad();
</script>
</body>
</html>
"""

# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="ASAF local web viewer")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--sessions", type=Path, default=DEFAULT_SEGS)
    parser.add_argument("--messages", type=Path, default=DEFAULT_MSGS)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    conn = sqlite3.connect(str(args.db), check_same_thread=False)

    print(f"Loading sessions from {args.sessions}…")
    sessions = load_jsonl(args.sessions)
    print(f"  {len(sessions)} sessions loaded")

    print(f"Loading messages from {args.messages}…")
    # Flatten all messages from sessions if messages.jsonl contains session objects,
    # otherwise load as flat message records.
    raw_msgs = load_jsonl(args.messages)
    if raw_msgs and "messages" in raw_msgs[0]:
        messages: list[dict] = []
        for sess in raw_msgs:
            messages.extend(sess.get("messages", []))
    else:
        messages = raw_msgs
    print(f"  {len(messages)} messages loaded")

    # Inject into handler class
    ViewerHandler.conn = conn
    ViewerHandler.sessions = sessions
    ViewerHandler.messages = messages

    server = ThreadingHTTPServer(("", args.port), ViewerHandler)
    print(f"\n  ASAF Viewer running at http://localhost:{args.port}\n  Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        conn.close()
        server.server_close()


if __name__ == "__main__":
    main()
