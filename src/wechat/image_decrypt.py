"""
Image processing for WeChat images.
Uses WeFlow's WASM module via persistent Node.js subprocess for transformation.

The Python implementation differs from WeFlow's WASM implementation,
so we delegate to Node.js + WASM. To avoid 2-second startup overhead per call,
we use a persistent Node.js process that handles multiple transformation requests.
"""
import re
import struct
import subprocess
import tempfile
import os
import json
import time
import threading
import pathlib
from typing import Optional
from urllib.request import Request, urlopen
import ssl
import logging

logger = logging.getLogger(__name__)

# Path to WeFlow's WASM decrypt script (lib/wasm/wasm_decrypt_service.js)
PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent
WASM_DECRYPT_SCRIPT = str(PROJECT_ROOT / "lib" / "wasm" / "wasm_decrypt_service.js")


class _PersistentWasmService:
    """Singleton persistent WASM decrypt service.

    Keeps a Node.js process alive across calls to avoid 2s startup overhead.
    The process accepts JSON commands via stdin and returns results via stdout.

    Singleton is enforced by __new__ + _lock to prevent concurrent threads
    from creating multiple instances or re-entering __init__ (which would
    reset io_lock/_start_lock and break in-flight operations).
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.process = None
        self.io_lock = threading.Lock()
        self._start_lock = threading.Lock()

    def _ensure_started(self):
        with self._start_lock:
            if self.process is not None and self.process.poll() is None:
                return  # Still running

            # Start fresh process
            logger.info("Starting persistent WASM decrypt service...")
            self.process = subprocess.Popen(
                ["node", WASM_DECRYPT_SCRIPT],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=os.path.dirname(WASM_DECRYPT_SCRIPT),
                text=True,
                bufsize=1,  # Line-buffered
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )
            # Wait for WASM to initialize (2s)
            time.sleep(2.2)

            if self.process.poll() is not None:
                err = self.process.stderr.read() if self.process.stderr else "unknown"
                self.process = None
                raise RuntimeError(f"WASM service failed to start: {err}")

            logger.info(f"WASM service started (PID={self.process.pid})")

    def decrypt_file(self, key: str, input_path: str, output_path: str) -> Optional[dict]:
        """Send decrypt command to persistent service and wait for response.

        Uses a threading-based 10-second timeout on stdout readline —
        if the Node.js process hangs or crashes without responding, we
        kill it and return an error instead of blocking forever (which
        would deadlock all export requests waiting on io_lock).
        """
        self._ensure_started()

        cmd = json.dumps({"key": key, "input": input_path, "output": output_path})

        with self.io_lock:
            try:
                self.process.stdin.write(cmd + '\n')
                self.process.stdin.flush()

                # Thread-based readline with 10s timeout
                # (select.select doesn't work on Windows pipes)
                result_line = [None]
                def _read():
                    try:
                        result_line[0] = self.process.stdout.readline()
                    except Exception:
                        result_line[0] = ""

                reader = threading.Thread(target=_read, daemon=True)
                reader.start()
                reader.join(timeout=10.0)

                if reader.is_alive():
                    logger.error("WASM decrypt service: no response in 10s — killing process")
                    self._kill_process()
                    return {"ok": False, "error": "WASM service timeout (10s)"}

                line = result_line[0]
                if not line:
                    # Process died, restart on next call
                    self.process = None
                    return {"ok": False, "error": "Service died"}
                return json.loads(line)
            except (BrokenPipeError, OSError, json.JSONDecodeError) as e:
                self.process = None
                return {"ok": False, "error": str(e)}

    def _kill_process(self):
        """Kill the Node.js process and reset state so next call restarts it."""
        if self.process is not None:
            try:
                self.process.kill()
                self.process.wait(timeout=3)
            except Exception:
                pass
            self.process = None

    def shutdown(self):
        """Shutdown the persistent service."""
        with self._start_lock:
            if self.process is not None:
                try:
                    self.process.terminate()
                    self.process.wait(timeout=3)
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass
                self.process = None


# Global singleton
_wasm_service = _PersistentWasmService()


def _decrypt_with_wasm(data: bytes, key: str) -> Optional[bytes]:
    """Decrypt data using persistent WASM service (no per-call Node.js startup)."""
    if not data or not key:
        return None

    try:
        # Write encrypted data to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(data)
            temp_input = f.name
        temp_output = temp_input + ".dec"

        try:
            result = _wasm_service.decrypt_file(key, temp_input, temp_output)
            if result and result.get("ok") and os.path.exists(temp_output):
                with open(temp_output, "rb") as f:
                    return f.read()
        finally:
            for path in (temp_input, temp_output):
                try:
                    if os.path.exists(path):
                        os.unlink(path)
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"WASM decrypt failed: {e}")
    return None


def isaac64_decrypt(data: bytes, key: int) -> bytes:
    """Decrypt WeChat ISAAC-64 encrypted image bytes using WASM."""
    if not key or key == 0:
        return data

    key_str = str(key)
    result = _decrypt_with_wasm(data, key_str)
    if result and result[:2] == b"\xff\xd8":
        return result

    # Fallback: return original data if decryption failed
    if result:
        return result
    return data


def fix_sns_url(url: str) -> str:
    """Normalize WeChat SNS CDN URL: force https and strip trailing size suffix."""
    if not url:
        return url
    url = url.replace("http://", "https://")
    url = re.sub(r"/\d+$", "/0", url)
    return url


def download_and_decrypt(url: str, key, timeout: int = 15) -> Optional[bytes]:
    """Download an image from WeChat CDN and decrypt it."""
    if not url:
        return None
    fixed_url = fix_sns_url(url)
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = Request(fixed_url, headers={
            "User-Agent": "Mozilla/5.0 MicroMessenger/3.9.12.29",
            "Referer": "https://wx.qq.com/",
        })
        resp = urlopen(req, context=ctx, timeout=timeout)
        data = resp.read()
    except Exception:
        return None

    if key is None:
        return data
    try:
        if isinstance(key, str):
            key_str = key
        else:
            key_str = str(key)
    except (ValueError, TypeError):
        return data

    if key_str == "0" or not key_str:
        return data
    return isaac64_decrypt(data, key_str)
