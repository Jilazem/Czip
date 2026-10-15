"""Rewrite only explicitly requested Czip prompt work; ordinary chat is untouched."""
import re
from typing import Any

_RAW = re.compile(r"^\s*/?czip[ _-](?:promt|prompt)\s+(?!oku\b|read\b|listele\b|list\b)(.+)$", re.I | re.S)


def rewrite_gateway_message(event: Any = None, **kwargs):
    text = str(getattr(event, "text", "") or "").strip()
    match = _RAW.match(text)
    if not match:
        return None
    from czip_runtime import T
    instruction = T(
        "Turn this explicit request into a usable prompt: goal, inputs, scope, output and acceptance criteria. Preserve user intent. Ask only for essential missing information. Show the prompt before saving it with czip task save --type=promt. Do not reset or close any session. Source request:\n",
        "Bu açık isteği kullanılabilir bir isteme dönüştür: amaç, girdiler, kapsam, çıktı ve kabul ölçütleri. Kullanıcının niyetini koru. Yalnız gerekli eksik bilgileri sor. czip gorev kaydet --tip=promt ile kaydetmeden önce istemi göster. Oturumları sıfırlama veya kapatma. Kaynak istek:\n")
    return {"action":"rewrite", "text":instruction + match.group(1).strip()}


def register(ctx):
    ctx.register_hook("pre_gateway_dispatch", rewrite_gateway_message)
