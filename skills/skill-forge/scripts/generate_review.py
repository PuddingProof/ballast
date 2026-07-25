"""Generate and optionally serve a review page for eval results.

SOURCE: skill-creator/eval-viewer/generate_review.py
CROSS-PLATFORM NOTES:
  - _kill_port(): branches on sys.platform. On win32, uses netstat + taskkill
    (available on all modern Windows installs). On macOS/Linux, uses
    `lsof -ti :PORT` + `kill -9` (the standard POSIX equivalent). Both branches
    fall back gracefully if their tools aren't available or nothing is
    listening — this is a best-effort convenience, not a critical path.
  - signal.SIGTERM on Windows: os.kill() with SIGTERM is not supported by the
    Windows signal model for arbitrary PIDs. Replaced with subprocess.run
    ["taskkill", "/F", "/PID", str(pid)] for the kill-port path.
  - webbrowser.open() works cross-platform (opens the OS default browser).
  - All file I/O uses explicit encoding="utf-8" (Windows defaults to cp1252).
  - --static mode is the recommended easy path when there's no display / server
    needed — the flag is preserved and well-documented.
  - Server mode (HTTPServer on 127.0.0.1) works cross-platform; no changes needed.
UNVERIFIED:
  - _kill_port(): the netstat + taskkill (Windows) and lsof + kill -9 (POSIX)
    approaches have been logic-reviewed but the POSIX branch has not been
    live-tested (ballast's Windows-only Phase 3 gate can't exercise it — see
    the build spec's "Known deferred" section for the macOS/Linux live-test
    plan). If a port is still in use after the kill attempt, the server falls
    back to OS port assignment (server_address[1]) — safe but prints a
    different URL than expected.
  - HTTPServer feedback writes (do_POST): Python's stdlib HTTP server is
    single-threaded by default. Concurrent feedback POSTs are serialized
    naturally; no concurrency risk, but very high-frequency saves (multiple
    browser tabs) could queue up. Acceptable for a local review tool.
"""

import argparse
import base64
import json
import mimetypes
import os
import re
import subprocess
import sys
import time
import webbrowser
from functools import partial
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Files we skip when listing eval outputs (they're metadata, not results)
METADATA_FILES = {"transcript.md", "user_notes.md", "metrics.json"}

# Extensions rendered as inline text in the viewer
TEXT_EXTENSIONS = {
    ".txt", ".md", ".json", ".csv", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".yaml", ".yml", ".xml", ".html", ".css", ".sh", ".rb", ".go", ".rs",
    ".java", ".c", ".cpp", ".h", ".hpp", ".sql", ".r", ".toml",
}

# Extensions rendered as inline images
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}

# Override mimetypes for types Python's stdlib sometimes gets wrong
MIME_OVERRIDES = {
    ".svg": "image/svg+xml",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def get_mime_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in MIME_OVERRIDES:
        return MIME_OVERRIDES[ext]
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


# ---------------------------------------------------------------------------
# Run discovery
# ---------------------------------------------------------------------------

def find_runs(workspace: Path) -> list[dict]:
    """Recursively find directories that contain an outputs/ subdirectory."""
    runs: list[dict] = []
    _find_runs_recursive(workspace, workspace, runs)
    # Sort by eval_id first, then by run path string
    runs.sort(key=lambda r: (r.get("eval_id", float("inf")), r["id"]))
    return runs


def _find_runs_recursive(root: Path, current: Path, runs: list[dict]) -> None:
    if not current.is_dir():
        return

    outputs_dir = current / "outputs"
    if outputs_dir.is_dir():
        run = build_run(root, current)
        if run:
            runs.append(run)
        return  # don't recurse into a run directory

    # Skip directories that are never run containers
    skip = {"node_modules", ".git", "__pycache__", "skill", "inputs"}
    for child in sorted(current.iterdir()):
        if child.is_dir() and child.name not in skip:
            _find_runs_recursive(root, child, runs)


def build_run(root: Path, run_dir: Path) -> dict | None:
    """Build a run dict with prompt, outputs, and grading data."""
    prompt = ""
    eval_id = None

    # Try eval_metadata.json in run dir or parent
    for candidate in [run_dir / "eval_metadata.json", run_dir.parent / "eval_metadata.json"]:
        if candidate.exists():
            try:
                metadata = json.loads(candidate.read_text(encoding="utf-8"))
                prompt = metadata.get("prompt", "")
                eval_id = metadata.get("eval_id")
            except (json.JSONDecodeError, OSError):
                pass
            if prompt:
                break

    # Fall back to transcript.md
    if not prompt:
        for candidate in [run_dir / "transcript.md", run_dir / "outputs" / "transcript.md"]:
            if candidate.exists():
                try:
                    text = candidate.read_text(encoding="utf-8")
                    match = re.search(r"## Eval Prompt\n\n([\s\S]*?)(?=\n##|$)", text)
                    if match:
                        prompt = match.group(1).strip()
                except OSError:
                    pass
                if prompt:
                    break

    if not prompt:
        prompt = "(No prompt found)"

    # Build a stable string ID from the run path relative to workspace root.
    # Use forward slashes consistently (the viewer uses this as a dict key).
    run_id = str(run_dir.relative_to(root)).replace("\\", "/")

    # Collect and embed output files
    outputs_dir = run_dir / "outputs"
    output_files: list[dict] = []
    if outputs_dir.is_dir():
        for f in sorted(outputs_dir.iterdir()):
            if f.is_file() and f.name not in METADATA_FILES:
                output_files.append(embed_file(f))

    # Load grading.json if present
    grading = None
    for candidate in [run_dir / "grading.json", run_dir.parent / "grading.json"]:
        if candidate.exists():
            try:
                grading = json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
            if grading:
                break

    return {
        "id": run_id,
        "prompt": prompt,
        "eval_id": eval_id,
        "outputs": output_files,
        "grading": grading,
    }


# ---------------------------------------------------------------------------
# File embedding (for self-contained HTML)
# ---------------------------------------------------------------------------

def embed_file(path: Path) -> dict:
    """Read a file and return a JSON-serialisable embedded representation."""
    ext = path.suffix.lower()
    mime = get_mime_type(path)

    if ext in TEXT_EXTENSIONS:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = "(Error reading file)"
        return {"name": path.name, "type": "text", "content": content}

    elif ext in IMAGE_EXTENSIONS:
        try:
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            return {"name": path.name, "type": "error", "content": "(Error reading file)"}
        return {"name": path.name, "type": "image", "mime": mime, "data_uri": f"data:{mime};base64,{b64}"}

    elif ext == ".pdf":
        try:
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            return {"name": path.name, "type": "error", "content": "(Error reading file)"}
        return {"name": path.name, "type": "pdf", "data_uri": f"data:{mime};base64,{b64}"}

    elif ext == ".xlsx":
        try:
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            return {"name": path.name, "type": "error", "content": "(Error reading file)"}
        return {"name": path.name, "type": "xlsx", "data_b64": b64}

    else:
        # Binary / unknown — emit a download link
        try:
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            return {"name": path.name, "type": "error", "content": "(Error reading file)"}
        return {"name": path.name, "type": "binary", "mime": mime, "data_uri": f"data:{mime};base64,{b64}"}


# ---------------------------------------------------------------------------
# Previous-iteration context loader
# ---------------------------------------------------------------------------

def load_previous_iteration(workspace: Path) -> dict[str, dict]:
    """Load feedback and outputs from a previous-iteration workspace.

    Returns a map of run_id -> {"feedback": str, "outputs": list[dict]}.
    """
    result: dict[str, dict] = {}

    # Load feedback.json if present
    feedback_map: dict[str, str] = {}
    feedback_path = workspace / "feedback.json"
    if feedback_path.exists():
        try:
            data = json.loads(feedback_path.read_text(encoding="utf-8"))
            feedback_map = {
                r["run_id"]: r["feedback"]
                for r in data.get("reviews", [])
                if r.get("feedback", "").strip()
            }
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    # Discover runs in the previous workspace
    prev_runs = find_runs(workspace)
    for run in prev_runs:
        result[run["id"]] = {
            "feedback": feedback_map.get(run["id"], ""),
            "outputs": run.get("outputs", []),
        }

    # Include feedback for run_ids that exist in feedback.json but have no run dir
    for run_id, fb in feedback_map.items():
        if run_id not in result:
            result[run_id] = {"feedback": fb, "outputs": []}

    return result


# ---------------------------------------------------------------------------
# HTML generation (injects data into viewer.html template)
# ---------------------------------------------------------------------------

def generate_html(
    runs: list[dict],
    skill_name: str,
    previous: dict[str, dict] | None = None,
    benchmark: dict | None = None,
) -> str:
    """Generate a complete standalone HTML page with all eval data embedded.

    Reads the viewer.html template (sibling to this file) and replaces the
    /*__EMBEDDED_DATA__*/ sentinel with a JS assignment of the data JSON.
    The resulting page requires no server — all data is baked in.
    """
    template_path = Path(__file__).parent / "viewer.html"
    template = template_path.read_text(encoding="utf-8")

    # Build previous_feedback and previous_outputs maps for the template JS
    previous_feedback: dict[str, str] = {}
    previous_outputs: dict[str, list[dict]] = {}
    if previous:
        for run_id, data in previous.items():
            if data.get("feedback"):
                previous_feedback[run_id] = data["feedback"]
            if data.get("outputs"):
                previous_outputs[run_id] = data["outputs"]

    embedded = {
        "skill_name": skill_name,
        "runs": runs,
        "previous_feedback": previous_feedback,
        "previous_outputs": previous_outputs,
    }
    if benchmark:
        embedded["benchmark"] = benchmark

    data_json = json.dumps(embedded)
    return template.replace("/*__EMBEDDED_DATA__*/", f"const EMBEDDED_DATA = {data_json};")


# ---------------------------------------------------------------------------
# Port management (Windows-compatible)
# ---------------------------------------------------------------------------

def _kill_port(port: int) -> None:
    """Kill any process listening on the given port (cross-platform).

    On Windows: uses `netstat -ano` to find the PID, then `taskkill /F /PID`.
    On macOS/Linux: uses `lsof -ti :PORT` to find the PID(s) directly, then
    `kill -9`. Both branches are wrapped in a swallow-errors try/except —
    this is a best-effort convenience (freeing up a stale dev-server port
    before re-binding), not a critical path, so any failure (tool missing,
    permission denied, nothing listening) should silently fall through and
    let the caller's own bind-with-OS-assigned-fallback logic take over.
    """
    try:
        if sys.platform == "win32":
            # netstat -ano shows proto/local-addr/foreign-addr/state/PID
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            pids: set[int] = set()
            for line in result.stdout.splitlines():
                # Match lines like: TCP  0.0.0.0:3117  ...  LISTENING  1234
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        try:
                            pids.add(int(parts[-1]))
                        except ValueError:
                            pass

            for pid in pids:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(pid)],
                        capture_output=True,
                        timeout=5,
                    )
                except (subprocess.TimeoutExpired, OSError):
                    pass

            if pids:
                time.sleep(0.5)  # brief wait for the port to fully release

        else:
            # POSIX (macOS/Linux): `lsof -ti :PORT` prints just the PID(s) of
            # whatever holds the port listening, one per line — no parsing of
            # tabular output needed like the Windows netstat branch above.
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            pids = set()
            for line in result.stdout.splitlines():
                line = line.strip()
                if line:
                    try:
                        pids.add(int(line))
                    except ValueError:
                        pass

            for pid in pids:
                try:
                    subprocess.run(
                        ["kill", "-9", str(pid)],
                        capture_output=True,
                        timeout=5,
                    )
                except (subprocess.TimeoutExpired, OSError):
                    pass

            if pids:
                time.sleep(0.5)  # brief wait for the port to fully release

    except (subprocess.TimeoutExpired, OSError, FileNotFoundError):
        # netstat/taskkill (Windows) or lsof/kill (POSIX) not available —
        # proceed without killing; the caller falls back to OS port assignment.
        pass


# ---------------------------------------------------------------------------
# HTTP server (stdlib only)
# ---------------------------------------------------------------------------

class ReviewHandler(BaseHTTPRequestHandler):
    """Serves the review HTML and handles feedback saves.

    Regenerates the HTML on each GET / so refreshing the browser picks up new
    eval outputs without restarting the server.
    """

    def __init__(
        self,
        workspace: Path,
        skill_name: str,
        feedback_path: Path,
        previous: dict[str, dict],
        benchmark_path: Path | None,
        *args,
        **kwargs,
    ):
        self.workspace = workspace
        self.skill_name = skill_name
        self.feedback_path = feedback_path
        self.previous = previous
        self.benchmark_path = benchmark_path
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            # Re-scan workspace on every request (picks up new outputs live)
            runs = find_runs(self.workspace)
            benchmark = None
            if self.benchmark_path and self.benchmark_path.exists():
                try:
                    benchmark = json.loads(self.benchmark_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
            page = generate_html(runs, self.skill_name, self.previous, benchmark)
            content = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        elif self.path == "/api/feedback":
            data = b"{}"
            if self.feedback_path.exists():
                data = self.feedback_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path == "/api/feedback":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                if not isinstance(data, dict) or "reviews" not in data:
                    raise ValueError("Expected JSON object with 'reviews' key")
                self.feedback_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                resp = b'{"ok":true}'
                self.send_response(200)
            except (json.JSONDecodeError, OSError, ValueError) as e:
                resp = json.dumps({"error": str(e)}).encode("utf-8")
                self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        else:
            self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:
        # Suppress per-request logging to keep the terminal clean
        pass


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate and optionally serve an eval review page"
    )
    parser.add_argument("workspace", type=Path, help="Path to workspace directory")
    parser.add_argument("--port", "-p", type=int, default=3117, help="Server port (default: 3117)")
    parser.add_argument("--skill-name", "-n", type=str, default=None, help="Skill name for page header")
    parser.add_argument(
        "--previous-workspace", type=Path, default=None,
        help="Path to previous iteration's workspace (shows old outputs + feedback as context)",
    )
    parser.add_argument(
        "--benchmark", type=Path, default=None,
        help="Path to benchmark.json to show in the Benchmark tab",
    )
    parser.add_argument(
        "--static", "-s", type=Path, default=None,
        help=(
            "Write standalone HTML to this path instead of starting a server. "
            "Recommended on Windows — works without a running process or display."
        ),
    )
    args = parser.parse_args()

    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        print(f"Error: {workspace} is not a directory", file=sys.stderr)
        sys.exit(1)

    runs = find_runs(workspace)
    if not runs:
        print(f"No runs found in {workspace}", file=sys.stderr)
        sys.exit(1)

    skill_name = args.skill_name or workspace.name.replace("-workspace", "")
    feedback_path = workspace / "feedback.json"

    previous: dict[str, dict] = {}
    if args.previous_workspace:
        previous = load_previous_iteration(args.previous_workspace.resolve())

    benchmark_path = args.benchmark.resolve() if args.benchmark else None
    benchmark = None
    if benchmark_path and benchmark_path.exists():
        try:
            benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # --static mode: emit self-contained HTML and exit (no server needed)
    if args.static:
        page = generate_html(runs, skill_name, previous, benchmark)
        args.static.parent.mkdir(parents=True, exist_ok=True)
        args.static.write_text(page, encoding="utf-8")
        print(f"\n  Static viewer written to: {args.static}\n")
        # Open in default browser automatically (works on Windows)
        webbrowser.open(args.static.resolve().as_uri())
        sys.exit(0)

    # Server mode — kill any existing listener then bind
    port = args.port
    _kill_port(port)
    handler = partial(ReviewHandler, workspace, skill_name, feedback_path, previous, benchmark_path)
    try:
        server = HTTPServer(("127.0.0.1", port), handler)
    except OSError:
        # Port still in use after kill attempt — let OS assign a free port
        server = HTTPServer(("127.0.0.1", 0), handler)
        port = server.server_address[1]

    url = f"http://localhost:{port}"
    print(f"\n  Eval Viewer")
    print(f"  {'─' * 33}")
    print(f"  URL:       {url}")
    print(f"  Workspace: {workspace}")
    print(f"  Feedback:  {feedback_path}")
    if previous:
        print(f"  Previous:  {args.previous_workspace} ({len(previous)} runs)")
    if benchmark_path:
        print(f"  Benchmark: {benchmark_path}")
    print(f"\n  Press Ctrl+C to stop.\n")

    webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
