import io
import os
import threading
import time
import uuid
import zipfile
from pathlib import Path

from flask import Flask, jsonify, request, send_file

from config import DEFAULT_API_BASE, DEFAULT_API_KEY, DEFAULT_MAX_CONCURRENCY, DEFAULT_MAX_RPS, DEFAULT_MODEL
from md_translater import translate_paper

app = Flask(__name__)

JOBS_DIR = Path(__file__).resolve().parent / "web_jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

JOBS = {}
JOBS_LOCK = threading.Lock()


def _job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


@app.get("/")
def index():
    html = """<!doctype html>
<html lang=\"zh\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Markdown Translator</title>
  <style>
    body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; max-width: 980px; margin: 24px auto; padding: 0 16px; }
    .card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 16px; margin-bottom: 16px; }
    .row { display: grid; grid-template-columns: 180px 1fr; gap: 10px; align-items: center; margin: 10px 0; }
    input[type=text], input[type=number] { width: 100%; padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 8px; }
    input[type=file] { width: 100%; }
    button { padding: 10px 14px; border-radius: 8px; border: 1px solid #d1d5db; background: #111827; color: #fff; cursor: pointer; }
    button:disabled { opacity: .55; cursor: not-allowed; }
    .muted { color: #6b7280; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, \"Liberation Mono\", \"Courier New\", monospace; }
    .actions { display: flex; gap: 10px; flex-wrap: wrap; }
    a.btn { display: inline-block; padding: 10px 14px; border-radius: 8px; border: 1px solid #d1d5db; text-decoration: none; }
  </style>
</head>
<body>
  <h1>Markdown Translator</h1>

  <div class=\"card\">
    <div class=\"row\">
      <div>Markdown 文件</div>
      <div><input id=\"file\" type=\"file\" accept=\".md,text/markdown\" /></div>
    </div>

    <div class=\"row\">
      <div>Model</div>
      <div><input id=\"model\" type=\"text\" /></div>
    </div>

    <div class=\"row\">
      <div>API Base</div>
      <div><input id=\"apiBase\" type=\"text\" /></div>
    </div>

    <div class=\"row\">
      <div>API Key (可选)</div>
      <div><input id=\"apiKey\" type=\"text\" placeholder=\"留空则使用服务器默认值\" /></div>
    </div>

    <div class=\"row\">
      <div>速率限制</div>
      <div style=\"display:flex; gap:10px;\">
        <input id=\"rpsCalls\" type=\"number\" min=\"1\" step=\"1\" style=\"max-width:160px\" />
        <span class=\"muted\">calls /</span>
        <input id=\"rpsPeriod\" type=\"number\" min=\"1\" step=\"1\" style=\"max-width:160px\" />
        <span class=\"muted\">seconds</span>
      </div>
    </div>

    <div class=\"row\">
      <div>最大并发</div>
      <div><input id=\"concurrency\" type=\"number\" min=\"1\" step=\"1\" /></div>
    </div>

    <div class=\"actions\">
      <button id=\"startBtn\">开始翻译</button>
      <span id=\"status\" class=\"muted\"></span>
    </div>
  </div>

  <div class=\"card\">
    <div class=\"row\">
      <div>Job ID</div>
      <div class=\"mono\" id=\"jobId\">-</div>
    </div>

    <div class=\"row\">
      <div>进度</div>
      <div>
        <progress id=\"progress\" value=\"0\" max=\"100\" style=\"width:100%\"></progress>
        <div class=\"muted\"><span id=\"progressText\">0%</span></div>
      </div>
    </div>

    <div class=\"actions\" id=\"downloadArea\" style=\"display:none\">
      <a class=\"btn\" id=\"dlZh\" href=\"#\">下载：中文</a>
      <a class=\"btn\" id=\"dlBilingual\" href=\"#\">下载：双语</a>
      <a class=\"btn\" id=\"dlZip\" href=\"#\">下载：Zip</a>
    </div>
  </div>

<script>
let pollTimer = null;

async function loadDefaults() {
  const res = await fetch('/api/default_config');
  const data = await res.json();
  document.getElementById('model').value = data.model;
  document.getElementById('apiBase').value = data.api_base;
  document.getElementById('rpsCalls').value = data.max_rps_calls;
  document.getElementById('rpsPeriod').value = data.max_rps_period;
  document.getElementById('concurrency').value = data.max_concurrency;
}

function setStatus(msg) {
  document.getElementById('status').textContent = msg;
}

function setProgress(pct, completed, total, status, error) {
  const p = document.getElementById('progress');
  p.value = pct;
  const extra = total > 0 ? ` (${completed}/${total})` : '';
  const err = error ? ` | error: ${error}` : '';
  document.getElementById('progressText').textContent = `${pct.toFixed(1)}% | ${status}${extra}${err}`;
}

function showDownloads(jobId) {
  const area = document.getElementById('downloadArea');
  area.style.display = 'flex';
  document.getElementById('dlZh').href = `/api/jobs/${jobId}/download?kind=zh`;
  document.getElementById('dlBilingual').href = `/api/jobs/${jobId}/download?kind=bilingual`;
  document.getElementById('dlZip').href = `/api/jobs/${jobId}/download?kind=zip`;
}

async function poll(jobId) {
  const res = await fetch(`/api/jobs/${jobId}/progress`);
  const data = await res.json();
  setProgress(data.percent, data.completed_lines, data.total_lines, data.status, data.error);
  if (data.status === 'completed') {
    clearInterval(pollTimer);
    pollTimer = null;
    setStatus('完成');
    showDownloads(jobId);
  }
  if (data.status === 'failed') {
    clearInterval(pollTimer);
    pollTimer = null;
    setStatus('失败');
  }
}

async function start() {
  const fileInput = document.getElementById('file');
  if (!fileInput.files || fileInput.files.length === 0) {
    setStatus('请选择 Markdown 文件');
    return;
  }

  document.getElementById('downloadArea').style.display = 'none';
  const startBtn = document.getElementById('startBtn');
  startBtn.disabled = true;

  try {
    setStatus('上传中...');

    const fd = new FormData();
    fd.append('file', fileInput.files[0]);

    const up = await fetch('/api/jobs', { method: 'POST', body: fd });
    const upData = await up.json();
    if (!up.ok) {
      throw new Error(upData.error || 'upload failed');
    }

    const jobId = upData.job_id;
    document.getElementById('jobId').textContent = jobId;

    setStatus('启动翻译...');
    const payload = {
      model: document.getElementById('model').value,
      api_base: document.getElementById('apiBase').value,
      api_key: document.getElementById('apiKey').value,
      max_rps_calls: Number(document.getElementById('rpsCalls').value),
      max_rps_period: Number(document.getElementById('rpsPeriod').value),
      max_concurrency: Number(document.getElementById('concurrency').value),
    };

    const st = await fetch(`/api/jobs/${jobId}/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const stData = await st.json();
    if (!st.ok) {
      throw new Error(stData.error || 'start failed');
    }

    setStatus('翻译中...');
    setProgress(0, 0, 0, 'running', null);
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => poll(jobId), 1000);
    await poll(jobId);
  } catch (e) {
    setStatus(`错误: ${e.message}`);
  } finally {
    startBtn.disabled = false;
  }
}

document.getElementById('startBtn').addEventListener('click', start);
loadDefaults();
</script>
</body>
</html>"""
    return html


@app.get("/api/default_config")
def default_config():
    return jsonify(
        {
            "model": DEFAULT_MODEL,
            "api_base": DEFAULT_API_BASE,
            "max_rps_calls": DEFAULT_MAX_RPS[0],
            "max_rps_period": DEFAULT_MAX_RPS[1],
            "max_concurrency": DEFAULT_MAX_CONCURRENCY,
        }
    )


@app.post("/api/jobs")
def create_job():
    if "file" not in request.files:
        return jsonify({"error": "missing file"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "empty filename"}), 400

    job_id = uuid.uuid4().hex
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    input_path = job_dir / Path(f.filename).name
    f.save(str(input_path))

    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "status": "uploaded",
            "error": None,
            "created_at": time.time(),
            "input_file": str(input_path),
            "original_filename": Path(f.filename).name,
            "output_dir": str(job_dir),
            "completed_lines": 0,
            "total_lines": 0,
        }

    return jsonify({"job_id": job_id})


@app.post("/api/jobs/<job_id>/start")
def start_job(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "job not found"}), 404
        if job["status"] == "running":
            return jsonify({"error": "job already running"}), 400

    payload = request.get_json(silent=True) or {}

    model = payload.get("model") or DEFAULT_MODEL
    api_base = payload.get("api_base") or DEFAULT_API_BASE
    api_key = payload.get("api_key") or DEFAULT_API_KEY

    max_rps_calls = _safe_int(payload.get("max_rps_calls"), DEFAULT_MAX_RPS[0])
    max_rps_period = _safe_int(payload.get("max_rps_period"), DEFAULT_MAX_RPS[1])
    max_concurrency = _safe_int(payload.get("max_concurrency"), DEFAULT_MAX_CONCURRENCY)

    job_dir = _job_dir(job_id)
    input_file = Path(job["input_file"])

    def progress_callback(done: int, total: int):
        with JOBS_LOCK:
            j = JOBS.get(job_id)
            if not j:
                return
            j["completed_lines"] = done
            j["total_lines"] = total

    def worker():
        with JOBS_LOCK:
            j = JOBS.get(job_id)
            if not j:
                return
            j["status"] = "running"
            j["error"] = None

        try:
            ok = translate_paper(
                input_file=str(input_file),
                output_dir=str(job_dir),
                model=model,
                max_rps=[max_rps_calls, max_rps_period],
                max_concurrency=max_concurrency,
                api_key=api_key,
                api_base=api_base,
                progress_callback=progress_callback,
                exit_on_error=False,
            )

            with JOBS_LOCK:
                j = JOBS.get(job_id)
                if not j:
                    return
                if ok:
                    j["status"] = "completed"
                else:
                    j["status"] = "failed"
                    j["error"] = "translation failed"
        except Exception as e:
            with JOBS_LOCK:
                j = JOBS.get(job_id)
                if not j:
                    return
                j["status"] = "failed"
                j["error"] = str(e)

    t = threading.Thread(target=worker, daemon=True)
    t.start()

    return jsonify({"job_id": job_id, "status": "running"})


@app.get("/api/jobs/<job_id>/progress")
def job_progress(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "job not found"}), 404

        total = int(job.get("total_lines") or 0)
        done = int(job.get("completed_lines") or 0)
        pct = 0.0
        if total > 0:
            pct = max(0.0, min(100.0, done * 100.0 / total))

        return jsonify(
            {
                "job_id": job_id,
                "status": job.get("status"),
                "error": job.get("error"),
                "completed_lines": done,
                "total_lines": total,
                "percent": pct,
            }
        )


@app.get("/api/jobs/<job_id>/download")
def download(job_id: str):
    kind = (request.args.get("kind") or "zip").lower()

    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"error": "job not found"}), 404
        if job.get("status") != "completed":
            return jsonify({"error": "job not completed"}), 400

        input_name = job.get("original_filename") or "input.md"

    stem = Path(input_name).stem
    job_dir = _job_dir(job_id)
    zh_path = job_dir / f"{stem}_zh.md"
    bilingual_path = job_dir / f"{stem}_bilingual.md"

    if kind == "zh":
        if not zh_path.exists():
            return jsonify({"error": "zh file not found"}), 404
        return send_file(str(zh_path), as_attachment=True, download_name=zh_path.name)

    if kind == "bilingual":
        if not bilingual_path.exists():
            return jsonify({"error": "bilingual file not found"}), 404
        return send_file(str(bilingual_path), as_attachment=True, download_name=bilingual_path.name)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        if zh_path.exists():
            z.write(zh_path, arcname=zh_path.name)
        if bilingual_path.exists():
            z.write(bilingual_path, arcname=bilingual_path.name)
    buf.seek(0)

    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{stem}_translation.zip",
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
