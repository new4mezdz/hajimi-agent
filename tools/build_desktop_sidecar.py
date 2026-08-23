from __future__ import annotations

import base64
import importlib.util
import json
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = ROOT / "build" / "desktop-sidecar"
DIST_DIR = BUILD_ROOT / "dist"
WORK_DIR = BUILD_ROOT / "work"
SPEC_DIR = BUILD_ROOT / "spec"
ENTRYPOINT = ROOT / "tools" / "desktop_sidecar_entry.py"
TAURI_BINARIES = ROOT / "web" / "src-tauri" / "binaries"


def rust_host() -> str:
    result = subprocess.run(
        ["rustc", "-vV"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    for line in result.stdout.splitlines():
        if line.startswith("host: "):
            return line.removeprefix("host: ").strip()
    raise RuntimeError("rustc -vV did not report a host target")


def read_lines(stream, output: queue.Queue[str]) -> None:
    for line in iter(stream.readline, ""):
        output.put(line)


def next_event(output: queue.Queue[str], deadline: float) -> dict[str, object]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Timed out waiting for the desktop sidecar")
    line = output.get(timeout=remaining)
    payload = json.loads(line)
    if not isinstance(payload, dict):
        raise RuntimeError("The desktop sidecar returned a non-object message")
    return payload


def verify_sidecar(executable: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="hajimi-sidecar-") as temporary_directory:
        process = subprocess.Popen(
            [str(executable)],
            cwd=temporary_directory,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        output: queue.Queue[str] = queue.Queue()
        reader = threading.Thread(target=read_lines, args=(process.stdout, output), daemon=True)
        reader.start()
        deadline = time.monotonic() + 60
        try:
            ready = next_event(output, deadline)
            if ready.get("type") != "ready" or ready.get("protocolVersion") != 1:
                raise RuntimeError(f"Unexpected sidecar ready event: {ready}")
            request = {
                "type": "request",
                "id": "build-smoke-test",
                "method": "GET",
                "path": "/health/ready",
                "headers": {},
                "body": None,
            }
            process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            process.stdin.flush()
            status = None
            body = bytearray()
            while True:
                event = next_event(output, deadline)
                if event.get("id") != request["id"]:
                    continue
                if event.get("type") == "response_start":
                    status = event.get("status")
                elif event.get("type") == "response_chunk":
                    body.extend(base64.b64decode(str(event["data"])))
                elif event.get("type") == "error":
                    raise RuntimeError(str(event.get("message", "Sidecar smoke test failed")))
                elif event.get("type") == "response_end":
                    break
            if status != 200 or json.loads(body) != {"status": "ready"}:
                raise RuntimeError(
                    f"Sidecar health check failed with status {status} and body {body!r}"
                )
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def main() -> None:
    if importlib.util.find_spec("PyInstaller") is None:
        raise SystemExit(
            'PyInstaller is required. Install the desktop build tools with: '
            'python -m pip install -e ".[desktop]"'
        )
    host = rust_host()
    executable_suffix = ".exe" if sys.platform == "win32" else ""
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--onefile",
            "--name",
            "agent-product-sidecar",
            "--paths",
            str(ROOT / "src"),
            "--distpath",
            str(DIST_DIR),
            "--workpath",
            str(WORK_DIR),
            "--specpath",
            str(SPEC_DIR),
            "--collect-all",
            "pydantic_ai",
            "--recursive-copy-metadata",
            "pydantic-ai-slim",
            "--collect-submodules",
            "sqlalchemy.dialects.sqlite",
            "--hidden-import",
            "aiosqlite",
            str(ENTRYPOINT),
        ],
        cwd=ROOT,
        check=True,
    )
    built = DIST_DIR / f"agent-product-sidecar{executable_suffix}"
    verify_sidecar(built)
    TAURI_BINARIES.mkdir(parents=True, exist_ok=True)
    target = TAURI_BINARIES / f"agent-product-sidecar-{host}{executable_suffix}"
    shutil.copy2(built, target)
    print(f"Desktop sidecar ready: {target}")


if __name__ == "__main__":
    main()
