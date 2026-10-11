"""Lightweight HTTP dashboard for the Diamond orchestrator."""
import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from diamond.store import EventStore
from diamond.workflow_store import WorkflowStore
from diamond.config import load_config
from diamond.paths import resolve_paths
from diamond.diagnostics import doctor


class DashboardHandler(BaseHTTPRequestHandler):
    """Serve the dashboard and provide API data."""

    def do_GET(self) -> None:
        if self.path == "/api/data":
            self._send_json(self._get_data())
        elif self.path == "/":
            self._send_html()
        else:
            self.send_error(404)

    def _get_data(self) -> dict[str, Any]:
        project_root = str(Path(__file__).resolve().parents[2])
        paths = resolve_paths(project_root, None, None)
        config = load_config(paths.config)
        store = EventStore(paths.database)

        providers = doctor(config)
        workflows = WorkflowStore(store).list()
        tasks = store.list()

        return {
            "project": paths.project.name,
            "providers": [
                {
                    "name": p.provider.value,
                    "status": p.status,
                    "version": p.version or "UNKNOWN",
                    "auth": p.auth_status,
                }
                for p in providers
            ],
            "workflows": [
                {
                    "id": w.spec.workflow_id[:12],
                    "full_id": w.spec.workflow_id,
                    "status": w.status.value,
                    "objective": w.spec.objective,
                    "revision": w.revision,
                    "builder": w.spec.builder_provider.value,
                    "debugger": w.spec.debugger_provider.value,
                }
                for w in workflows
            ],
            "tasks": [
                {
                    "id": t.spec.task_id[:12],
                    "full_id": t.spec.task_id,
                    "status": t.status.value,
                    "objective": t.spec.objective,
                    "role": t.spec.role.value,
                }
                for t in tasks
            ],
            "schema_version": 1,
            "timestamp": "now",
        }

    def _send_json(self, data: dict) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self) -> None:
        html = HTML_TEMPLATE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, format: str, *args: object) -> None:
        pass  # suppress request logs


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Diamond Orchestrator Dashboard</title>
<style>
:root {
  --bg: #0d1117; --card: #161b22; --border: #30363d;
  --text: #c9d1d9; --muted: #8b949e; --accent: #58a6ff;
  --green: #238636; --red: #da3633; --yellow: #d29922;
  --blue: #1f6feb; --purple: #8957e5;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }

.header { background: var(--card); border-bottom: 1px solid var(--border); padding: 16px 24px; display: flex; align-items: center; gap: 16px; }
.header h1 { font-size: 20px; color: var(--accent); }
.header .badge { background: var(--blue); color: white; padding: 2px 10px; border-radius: 12px; font-size: 12px; }
.header .status { margin-left: auto; font-size: 13px; color: var(--muted); }

.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 16px; padding: 20px 24px; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
.card-header { padding: 12px 16px; border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 8px; }
.card-header h2 { font-size: 14px; font-weight: 600; }
.card-body { padding: 12px; }

.provider-row { display: flex; align-items: center; gap: 12px; padding: 8px 4px; border-bottom: 1px solid var(--border); }
.provider-row:last-child { border-bottom: none; }
.dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
.dot.green { background: var(--green); box-shadow: 0 0 6px var(--green); }
.dot.red { background: var(--red); box-shadow: 0 0 6px var(--red); }
.dot.yellow { background: var(--yellow); box-shadow: 0 0 6px var(--yellow); }
.provider-name { font-weight: 500; flex: 1; }
.provider-ver { color: var(--muted); font-size: 12px; font-family: monospace; }
.provider-auth { font-size: 11px; color: var(--muted); }

.workflow-row { display: flex; align-items: center; gap: 12px; padding: 8px 4px; border-bottom: 1px solid var(--border); }
.workflow-row:last-child { border-bottom: none; }
.wf-id { font-family: monospace; font-size: 11px; color: var(--muted); width: 80px; }
.wf-status { font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 600; }
.wf-status.verified { background: var(--green); color: white; }
.wf-status.blocked { background: var(--yellow); color: black; }
.wf-status.failed { background: var(--red); color: white; }
.wf-objective { flex: 1; font-size: 13px; }
.wf-roles { font-size: 11px; color: var(--muted); }

.task-row { display: flex; align-items: center; gap: 12px; padding: 8px 4px; border-bottom: 1px solid var(--border); }
.task-row:last-child { border-bottom: none; }
.task-id { font-family: monospace; font-size: 11px; color: var(--muted); width: 80px; }
.task-status { font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 600; }
.task-status.created { background: var(--blue); color: white; }
.task-status.succeeded { background: var(--green); color: white; }
.task-status.failed { background: var(--red); color: white; }
.task-objective { flex: 1; font-size: 13px; }
.task-role { font-size: 11px; color: var(--muted); }

.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; padding: 20px 24px; }
.stat-card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; text-align: center; }
.stat-num { font-size: 32px; font-weight: 700; color: var(--accent); }
.stat-label { font-size: 12px; color: var(--muted); margin-top: 4px; }

.loading { text-align: center; padding: 40px; color: var(--muted); }
.spinner { width: 24px; height: 24px; border: 3px solid var(--border); border-top-color: var(--accent); border-radius: 50%; animation: spin 0.8s linear infinite; margin: 0 auto 8px; }
@keyframes spin { to { transform: rotate(360deg); } }

.refresh-btn { background: var(--blue); color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; }
.refresh-btn:hover { opacity: 0.85; }

.live-indicator { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--green); }
.live-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); animation: pulse 2s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

.wide { grid-column: span 2; }
@media (max-width: 800px) { .wide { grid-column: span 1; } .stats { grid-template-columns: repeat(2, 1fr); } }
</style>
</head>
<body>

<div class="header">
  <h1>◆ Diamond Orchestrator</h1>
  <span class="badge" id="schemaBadge">v1</span>
  <div class="live-indicator"><div class="live-dot"></div>Live</div>
  <button class="refresh-btn" onclick="loadData()">↻ Refresh</button>
</div>

<div class="stats" id="statsBar">
  <div class="loading"><div class="spinner"></div>Loading...</div>
</div>

<div class="grid">
  <div class="card">
    <div class="card-header"><h2>🔌 Providers</h2></div>
    <div class="card-body" id="providersBody">
      <div class="loading"><div class="spinner"></div></div>
    </div>
  </div>

  <div class="card wide">
    <div class="card-header"><h2>📋 Workflows</h2></div>
    <div class="card-body" id="workflowsBody">
      <div class="loading"><div class="spinner"></div></div>
    </div>
  </div>

  <div class="card wide">
    <div class="card-header"><h2>📌 Tasks</h2></div>
    <div class="card-body" id="tasksBody">
      <div class="loading"><div class="spinner"></div></div>
    </div>
  </div>
</div>

<script>
async function loadData() {
  try {
    const res = await fetch('/api/data');
    const data = await res.json();
    renderProviders(data.providers);
    renderWorkflows(data.workflows);
    renderTasks(data.tasks);
    renderStats(data);
  } catch(e) {
    document.getElementById('providersBody').innerHTML = '<div style="color:var(--red);padding:16px;text-align:center;">Failed to load data: ' + e.message + '</div>';
  }
}

function dot(status) {
  if (status === 'available') return '<span class="dot green"></span>';
  if (status === 'unavailable') return '<span class="dot red"></span>';
  return '<span class="dot yellow"></span>';
}

function statusClass(s) {
  const map = { verified:'verified', blocked:'blocked', failed:'failed', created:'created', succeeded:'succeeded',
    needs_correction:'blocked', exhausted:'failed', building:'created', debugging:'created' };
  return map[s.toLowerCase()] || 'created';
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[character]));
}

function renderProviders(providers) {
  const el = document.getElementById('providersBody');
  if (!providers.length) { el.innerHTML = '<div style="color:var(--muted);text-align:center;padding:16px;">No providers configured</div>'; return; }
  el.innerHTML = providers.map(p => `
    <div class="provider-row">
      ${dot(p.status)}
       <span class="provider-name">${escapeHtml(p.name)}</span>
       <span class="provider-ver">${escapeHtml(p.version)}</span>
       <span class="provider-auth">${escapeHtml(p.auth)}</span>
    </div>`).join('');
}

function renderWorkflows(wfs) {
  const el = document.getElementById('workflowsBody');
  if (!wfs.length) { el.innerHTML = '<div style="color:var(--muted);text-align:center;padding:16px;">No workflows yet. Run: diamond.sh workflow create</div>'; return; }
  el.innerHTML = wfs.map(w => `
    <div class="workflow-row">
       <span class="wf-id">${escapeHtml(w.id)}</span>
       <span class="wf-status ${statusClass(w.status)}">${escapeHtml(w.status)}</span>
       <span class="wf-objective">${escapeHtml(w.objective)}</span>
       <span class="wf-roles">B:${escapeHtml(w.builder)} D:${escapeHtml(w.debugger)}</span>
    </div>`).join('');
}

function renderTasks(tasks) {
  const el = document.getElementById('tasksBody');
  if (!tasks.length) { el.innerHTML = '<div style="color:var(--muted);text-align:center;padding:16px;">No tasks yet. Run: diamond.sh task create</div>'; return; }
  el.innerHTML = tasks.map(t => `
    <div class="task-row">
       <span class="task-id">${escapeHtml(t.id)}</span>
       <span class="task-status ${statusClass(t.status)}">${escapeHtml(t.status)}</span>
       <span class="task-objective">${escapeHtml(t.objective)}</span>
       <span class="task-role">${escapeHtml(t.role)}</span>
    </div>`).join('');
}

function renderStats(d) {
  const ok = d.providers.filter(p => p.status === 'available').length;
  const wf = d.workflows.filter(w => w.status === 'VERIFIED').length;
  const el = document.getElementById('statsBar');
  el.innerHTML = `
    <div class="stat-card"><div class="stat-num">${ok}/${d.providers.length}</div><div class="stat-label">Providers Probed</div></div>
    <div class="stat-card"><div class="stat-num">${wf}</div><div class="stat-label">Verified Workflows</div></div>
    <div class="stat-card"><div class="stat-num">${d.tasks.length}</div><div class="stat-label">Queued Tasks</div></div>
    <div class="stat-card"><div class="stat-num">${d.schema_version || 1}</div><div class="stat-label">Schema Version</div></div>
  `;
}

loadData();
setInterval(loadData, 30000);
</script>
</body>
</html>"""


def main(port: int = 8765) -> int:
    addr = ("127.0.0.1", port)
    server = HTTPServer(addr, DashboardHandler)
    print(f"Diamond Dashboard running at http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
