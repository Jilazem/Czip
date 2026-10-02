# czip-auto — oturum baslarken czip_auto.py'yi ATEŞ-VE-UNUT arka plan süreci olarak tetikler.
# Kural: hook geri donusu HIZLI olmali (timeout'lu hook yuzeyi) — isin kendisi ayrik coproc'te kosar.
# Akis korumasi: son tetikleme damgasi — ARALIK sn'den kisa aralikla yeniden baslatmaz.
import os
import subprocess
import time

_SCRIPT = os.environ.get("CZIP_AUTO_SCRIPT", os.path.expanduser("~/czip/czip_auto.py"))
_DAMGA = os.path.expanduser("~/.hermes/czip/auto-baslat.stamp")
_LOG = os.path.expanduser("~/.hermes/czip/_auto.log")
_ARALIK = int(os.environ.get("CZIP_AUTO_ARALIK", "1800"))  # 30 dk


def _tetikle(session_id=None, **kw):
    try:
        os.makedirs(os.path.dirname(_DAMGA), exist_ok=True)
        if os.path.exists(_DAMGA) and time.time() - os.path.getmtime(_DAMGA) < _ARALIK:
            return None  # cok yeni tetiklenme var, ikincisi gereksiz
        with open(_DAMGA, "w") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(session_id or "") + "\n")
        with open(_LOG, "a", encoding="utf-8") as log:
            subprocess.Popen(
                ["/usr/bin/python3", _SCRIPT],
                stdout=log, stderr=log,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # kopuk grup — gateway restart'indan etkilenmez
            )
    except Exception:
        pass  # hook asla istemciyu bozmamali; asil hata kaydi _auto.log'da zaten
    return None


def register(ctx):
    ctx.register_hook("on_session_start", _tetikle)
