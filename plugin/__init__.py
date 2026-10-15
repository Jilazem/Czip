"""Hermes Czip commands: caller-scoped archives and one bilingual command catalog."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any

_reference = Path(__file__).with_name("engine-path.txt")
_MOTOR = os.environ.get("CZIP_HKP") or (_reference.read_text().strip() if _reference.is_file()
         else str(Path(__file__).resolve().parents[1] / "hkp.py"))


def _motor_dizin():
    return str(Path(_MOTOR).resolve().parent)


def _yukle(name, path):
    directory = _motor_dizin()
    if directory not in sys.path:
        sys.path.insert(0, directory)
    existing = sys.modules.get(name)
    if existing is not None:
        if Path(existing.__file__).resolve() != Path(path).resolve():
            raise RuntimeError("Czip module path conflict: " + name)
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


def _motor():
    return _yukle("hkp", _MOTOR)


def _runtime():
    _motor()
    import czip_runtime
    return czip_runtime


def _cli(*args):
    runtime = _runtime()
    env = dict(os.environ)
    for key in ("HERMES_HOME", "HERMES_STATE_DB", "HERMES_SESSION_ID", "HERMES_SESSION_KEY", "CODEX_THREAD_ID"):
        value = runtime.session_env(key)
        if value:
            env[key] = value
        else:
            env.pop(key, None)
    env["CZIP_LANG"] = runtime.language()
    result = subprocess.run([sys.executable, _MOTOR, *args], capture_output=True, text=True,
                            timeout=120, env=env, cwd=_motor_dizin())
    return (result.stdout or result.stderr).strip()


def _packed(args):
    m = _motor(); runtime = _runtime(); T = runtime.T
    tokens = shlex.split(args)
    if any(token in tokens for token in ("--oto", "--auto", "--oto-pasif", "--auto-archive")):
        raise ValueError(T("Select sessions explicitly with /czip merge ID1 ID2", "Oturumları /czip birlestir ID1 ID2 ile açıkça seç"))
    flags = [token for token in tokens if token.startswith("--")]
    allowed = {"--full", "--eksiksiz", "--smart", "--akilli", "--clef", "--jev"}
    if set(flags) - allowed:
        raise ValueError(T("Unknown packing option", "Bilinmeyen paketleme seçeneği"))
    targets = [token for token in tokens if not token.startswith("--")]
    if len(targets) > 1:
        raise ValueError(T("Packing takes exactly one target", "Paketleme tek hedef alır"))
    target = targets[0] if targets else "active"
    if target in ("active", "aktif", "current", "son", "last", "latest") and not runtime.session_identity():
        raise ValueError(T("Caller context missing; give an exact session ID", "Oturum bağlamı yok; tam oturum kimliğini ver"))
    sid = None
    if Path(target).expanduser().is_file():
        messages = m.girdi_ayristir(target); title = Path(target).stem
    else:
        sid, messages, title = m.oturum_oku(target)
    if not messages:
        raise ValueError(T("The selected session is empty", "Seçilen oturum boş"))
    path = runtime.unique_package_path(m.PAKET_DIZIN, title or sid or "session")
    mode = "akilli" if any(t in tokens for t in ("--smart", "--akilli")) else "eksiksiz"
    result = m.sikistir(messages, path, mod=mode, baslik=title,
                        jev=any(t in tokens for t in ("--clef", "--jev")))
    if m.harita(path, son_n=0, istek_max=0)["toplam"] != result["mesaj"]:
        raise ValueError(T("Package verification failed", "Paket doğrulaması başarısız"))
    result["kid"] = m.id_ata(path, title, source_id=sid)
    return result, sid, title


def _format(result, sid, title):
    T = _runtime().T
    return "\n".join([T("Session packed: ", "Oturum paketlendi: ") + str(title or sid or ""),
        T("  %s messages | %s bytes | ID=%s", "  %s ileti | %s bayt | Kimlik=%s") %
            (result["mesaj"], result["paket_bayt"], result["kid"]),
        "  " + result["yol"],
        T("Read: /czip read ", "Oku: /czip oku ") + result["kid"],
        T("Source session state is unchanged.", "Kaynak oturumların durumu değişmedi.")])


def _paketle(args=""):
    result, sid, title = _packed(args)
    return _format(result, sid, title)


def _okur(args="", map_only=False):
    m = _motor(); runtime = _runtime(); tokens = shlex.split(args)
    if not tokens and not runtime.session_identity():
        raise ValueError(runtime.T("Give a package ID", "Paket kimliğini ver"))
    path = m.id_coz(tokens[0] if tokens else None)
    if not path or not Path(path).is_file():
        raise ValueError(runtime.T("No package for this caller; give an explicit ID", "Bu oturuma ait paket yok; açık kimlik ver"))
    data = m.mesaj_araligi(path, tokens[1]) if len(tokens) > 1 and not map_only else m.harita(path)
    return json.dumps(data, ensure_ascii=False)


def _son(args=""):
    # Capture this operation's result locally. Another caller cannot replace its target.
    tokens = shlex.split(args)
    if tokens and Path(tokens[0]).expanduser().is_file():
        raise ValueError(_runtime().T("A file is not an endable Hermes session", "Dosya sonlandırılabilir bir Hermes oturumu değildir"))
    result, sid, title = _packed(args)
    if not sid:
        raise ValueError(_runtime().T("Missing exact source session", "Tam kaynak oturum kimliği yok"))
    merger = _yukle("birlestir", str(Path(_motor_dizin()) / "birlestir.py"))
    ended = merger.pasife_al([sid], result["yol"], result["kid"], sebep="czip_end")
    if ended.get("pasif") != [sid]:
        raise ValueError(_runtime().T("Session was packed but could not be archived", "Oturum paketlendi fakat arşivlenemedi"))
    return (_format(result, sid, title).replace(
        _runtime().T("Source session state is unchanged.", "Kaynak oturumların durumu değişmedi."),
        _runtime().T("Only this source session was archived: ", "Yalnız bu kaynak oturum arşivlendi: ") + sid) + "\n" + _runtime().T("Undo: czip undo ", "Geri al: czip gerial ") + ended["gerial"])


def _birlestir(args=""):
    tokens = shlex.split(args)
    ids = [token for token in tokens if not token.startswith("--")]
    if len(ids) < 2 or any(t in ("oto", "auto", "son", "last", "active", "aktif") for t in ids):
        raise ValueError(_runtime().T("Usage: /czip merge ID1 ID2 [--full]", "Kullanım: /czip birlestir ID1 ID2 [--eksiksiz]"))
    return _cli("merge", *tokens, "--pasif-yok")


def _auto(args=""):
    values = shlex.split(args)
    if not values or values[0] in ("status", "durum"):
        return _cli("settings")
    if values[0] not in ("on", "off", "ac", "aç", "kapat"):
        raise ValueError(_runtime().T("Use on, off or status", "aç, kapat veya durum kullan"))
    return _cli("settings", "oto", {"ac":"on", "aç":"on", "kapat":"off"}.get(values[0], values[0]))


def _esik(args=""):
    return _cli("settings", *( ["tiers" if "," in args else "threshold", args.strip()] if args.strip() else []))


def _ara(args=""):
    values = shlex.split(args)
    if len(values) < 2:
        raise ValueError(_runtime().T('Usage: /czip search PACKAGE "QUERY"', 'Kullanım: /czip ara PAKET "SORGU"'))
    return _cli("search", *values)


def _gorev(args=""):
    return _cli("task", *shlex.split(args or "list"))


def _promt_plan(args, kind):
    values = shlex.split(args or "list")
    if values[0] not in ("read", "oku", "list", "listele"):
        return _runtime().T("Use the Czip prompt/plan skill for a new request.", "Yeni istek için Czip istem/plan becerisini kullan.")
    return _cli("task", *values, "--tip=" + kind)


def _yardim_metni(args=""):
    _motor()
    from czip_commands import help_text
    return help_text(slash=True)


def _invoke(name, args):
    handlers = {"pack":_paketle, "read":_okur, "map":lambda a: _okur(a, True),
                "search":_ara, "merge":_birlestir, "auto":_auto, "threshold":_esik,
                "task":_gorev, "prompt":lambda a:_promt_plan(a,"promt"),
                "plan":lambda a:_promt_plan(a,"plan"), "end":_son, "help":_yardim_metni}
    try:
        return handlers[name](args)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        return _runtime().T("Czip error: ", "Czip hatası: ") + str(error)


def _command(args=""):
    _motor()
    from czip_commands import resolve
    parts = args.strip().split(maxsplit=1)
    verb = resolve(parts[0]) if parts else "pack"
    return _invoke(verb or "pack", parts[1] if verb and len(parts)>1 else ("" if verb else args))


def register(ctx: Any):
    _motor()
    from czip_commands import COMMANDS
    T = _runtime().T
    for verb, _, hint, en, tr in COMMANDS:
        name = "czip" if verb == "pack" else "czip-" + verb
        handler = _command if verb == "pack" else lambda args="", v=verb: _invoke(v, args)
        ctx.register_command(name, handler, description=T(en,tr), args_hint=hint)
