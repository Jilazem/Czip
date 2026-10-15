"""Portable language, identity and atomic storage helpers shared by Czip surfaces."""
from __future__ import annotations

import contextlib
import contextvars
import functools
import hashlib
import json
import locale
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid

_language = contextvars.ContextVar("czip_language", default=None)
_locks = {}
_locks_guard = threading.Lock()


def session_env(name, default=""):
    try:
        from gateway.session_context import get_session_env
    except ImportError:
        return os.environ.get(name, default)
    return get_session_env(name, default)


def language():
    explicit = _language.get() or os.environ.get("CZIP_LANG")
    if explicit:
        return "tr" if str(explicit).lower().startswith(("tr", "türk", "turkish")) else "en"
    try:
        from agent.i18n import get_language
        return "tr" if get_language().startswith("tr") else "en"
    except (ImportError, AttributeError):
        pass
    chosen = session_env("HERMES_LANGUAGE") or os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES")
    if not chosen and sys.platform == "darwin":
        chosen = _mac_language()
    chosen = chosen or os.environ.get("LANG")
    if not chosen:
        chosen = locale.getlocale()[0] or "en"
    return "tr" if str(chosen).strip().lower().startswith(("tr", "turkish", "türk")) else "en"


@functools.lru_cache(maxsize=1)
def _mac_language():
    try:
        return subprocess.check_output(["defaults", "read", "-g", "AppleLanguages"], text=True,
             stderr=subprocess.DEVNULL, timeout=1).strip("()\n \"")
    except (OSError, subprocess.SubprocessError):
        return None


def T(en, tr):
    return tr if language() == "tr" else en


@contextlib.contextmanager
def output_language(value):
    token = _language.set(value)
    try:
        yield
    finally:
        _language.reset(token)


def session_identity():
    """Current caller only; never use global last-message activity as identity."""
    codex = session_env("CODEX_THREAD_ID")
    sid = session_env("HERMES_SESSION_ID")
    key = session_env("HERMES_SESSION_KEY")
    agent, owner = ("hermes", sid or key) if sid or key else ("codex", codex)
    if not owner:
        return None
    home = os.path.realpath(os.path.expanduser(session_env("HERMES_HOME", "~/.hermes")))
    return (agent, home, str(owner))


def owner_key():
    identity = session_identity()
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest() if identity else None


def unique_package_path(folder, title="session"):
    slug = re.sub(r"[^\w.-]+", "-", str(title), flags=re.UNICODE).strip(".-")[:60] or "session"
    return str(Path(folder).expanduser() / (slug + "-" + uuid.uuid4().hex + ".hkp"))


@contextlib.contextmanager
def file_lock(path, timeout=15):
    """Serialize read-modify-write across threads and processes, including Windows."""
    lock_path = Path(str(Path(path).expanduser()) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _locks_guard:
        thread_lock = _locks.setdefault(str(lock_path.resolve()), threading.RLock())
    with thread_lock, lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"\0"); stream.flush()
        until = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= until:
                    raise TimeoutError(T("Czip storage is busy", "Czip deposu meşgul"))
                time.sleep(.02)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read_json(path, default):
    try:
        return json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def atomic_json(path, value):
    path = Path(path).expanduser(); path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=1)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
