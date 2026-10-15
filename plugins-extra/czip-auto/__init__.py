"""Launch the closed-session packer at most once per profile and interval."""
import os
from pathlib import Path
import subprocess
import sys
import time


def _tetikle(session_id=None, **kw):
    try:
        from czip_runtime import atomic_json, file_lock, read_json, session_env
        home = Path(session_env("HERMES_HOME", "~/.hermes")).expanduser()
        script = Path(os.environ.get("CZIP_AUTO_SCRIPT", str(Path(__file__).resolve().parents[2] / "czip_auto.py")))
        if not script.is_file():
            return None
        stamp = home / "czip" / "auto-start.json"
        with file_lock(stamp, timeout=.1):
            if time.time() - read_json(stamp, {}).get("at", 0) < int(os.environ.get("CZIP_AUTO_ARALIK", "1800")):
                return None
            env = dict(os.environ)
            for key in ("HERMES_HOME", "HERMES_STATE_DB"):
                value = session_env(key)
                if value: env[key] = value
                else: env.pop(key, None)
            for key in ("HERMES_SESSION_ID", "HERMES_SESSION_KEY", "CODEX_THREAD_ID"):
                env.pop(key, None)
            with (home / "czip" / "auto.log").open("a", encoding="utf-8") as log:
                process = subprocess.Popen([os.environ.get("CZIP_PYTHON", sys.executable), str(script)],
                    stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True, env=env)
            atomic_json(stamp, {"at":time.time(), "pid":process.pid})
    except (OSError, ValueError, TimeoutError, ImportError):
        pass  # An optional housekeeping hook must never interrupt the caller.
    return None


def register(ctx):
    ctx.register_hook("on_session_start", _tetikle)
