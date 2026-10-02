"""czip-promt: ponytail'in yerine, aynı çalışma şekli (her turda bağlam + gateway yeniden yazımı)."""

from __future__ import annotations

import os
import re
from typing import Any

_HKP = os.environ.get("CZIP_HKP", os.path.expanduser("~/czip/hkp.py"))
_PY = os.environ.get("CZIP_PYTHON", "python3")
_CLI = f"HERMES_HOME=$HOME/.hermes {_PY} {_HKP} gorev"

_BAGLAM = f"""CZIP-PROMT AKTİF — görev tanımı ve devri `czip promt` biçiminde yapılır.

1) Kullanıcıdan ham görev gelince (`czip promt <ham görev>`, `/czip_promt <ham görev>` veya açık bir iş talebi):
   - Ham tarifi çalıştırılabilir prompta çevir: rol/bağlam, somut görev, girdi yolları, çıktı biçimi,
     kapsam dışı, kabul ölçütü. Kullanıcının kelimelerini koru; belirsizi uydurma, `[BELİRSİZ: ...]` yaz ve sor.
   - Promtu kod bloğunda göster, tek cümleyle "bunu mu istiyorsun?" diye onay iste. Onaysız ilerleme.
   - Onay gelince kaydet (terminal):
     {_CLI} kaydet --tip=promt --baslik="<kısa başlık>" --ham="<ham istek>" <<'PROMT'
     <onaylanmış prompt>
     PROMT
   - `kid`'i tek satırda bildir, promtu tekrar özetlemeden uygulamaya başla.
2) Uzmana/profile görev atarken (kanban, `hermes -p <profil> chat -q`, delege) görev tanımı DAİMA czip-promt biçimindedir:
   önce promtu yukarıdaki `kaydet` ile depoya yaz (bu devirde kullanıcı onayı gerekmez; kullanıcı işi zaten istedi),
   sonra görev gövdesine şunu koy:  `czip promt oku <kid>`  + tek satır başlık + dosya kökü/teslim yolu.
   Uzun görev metnini gövdeye gömme; depoda durur.
3) Sana görev `czip promt oku <kid>` olarak geldiyse önce oku ({_CLI} oku <kid>), sonra o promtu görevin kabul et ve uygula.
   Okuyamazsan işi uydurma; hatayı atayana bildir.
4) Kayıtlı promt/planlar: `/czip_promt listele|oku <kid|son>`, `/czip_plan ...`, `/czip_gorev listele`.
Bu akış Telegram dahil tüm kanallarda geçerlidir. Küçük sohbet/soru işlerinde akışı zorlama."""

_RAW = re.compile(r"^\s*/?czip[ _-]promt\s+(?!oku\b|listele\b)(.+)$", re.I | re.S)


def _pre_llm_call(**_: Any) -> dict[str, str]:
    return {"context": _BAGLAM}


def rewrite_gateway_message(event: Any = None, **_: Any) -> dict[str, str] | None:
    """'czip promt <ham görev>' (slash'lı ya da düz mesaj, Telegram dahil) → ajan promtu."""
    text = str(getattr(event, "text", "") or "").strip()
    m = _RAW.match(text)
    if not m:
        return None
    return {"action": "rewrite",
            "text": "czip-promt akışını uygula (bağlamdaki 1. madde). Ham görev:\n" + m.group(1).strip()}


def register(ctx: Any) -> None:
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("pre_gateway_dispatch", rewrite_gateway_message)
