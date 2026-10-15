# -*- coding: utf-8 -*-
"""ajanlar — farkli AI ajanlarinin oturum transkriptlerini czip mesaj listesine cevirir.

Desteklenen bicimler (otomatik algilanir):
  claude  Claude Code  ~/.claude/projects/<proje>/<session_id>.jsonl
  codex   OpenAI Codex ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl
  (digerleri: JSON / JSONL mesaj listesi, Hermes export — hkp.girdi_ayristir)

Cikti: (bicim, baslik, mesajlar, cwd). Mesajlar OpenAI benzeri
{role, content, tool_calls, tool_call_id, reasoning, timestamp} — hkp.py bunu dogrudan paketler.
Saf stdlib.
"""
import json
import os


def _satirlar(yol, sinir=None):
    with open(yol, encoding="utf-8", errors="replace") as f:
        for n, s in enumerate(f):
            if sinir is not None and n >= sinir:
                return
            try:
                d = json.loads(s)
            except Exception:
                continue
            if isinstance(d, dict):
                yield d


def bicim_algila(yol):
    """'claude' | 'codex' | None (bilinmeyen -> hkp'nin genel ayristiricisi)."""
    if not (isinstance(yol, str) and yol.endswith(".jsonl") and os.path.isfile(yol)):
        return None
    for d in _satirlar(yol, 200):
        t = d.get("type")
        if t in ("session_meta", "response_item", "turn_context"):
            return "codex"
        if t in ("user", "assistant") and isinstance(d.get("message"), dict):
            return "claude"
    return None


def _metin(v):
    """blok listesi / dize -> duz metin."""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        p = []
        for x in v:
            if isinstance(x, dict):
                t = x.get("type", "")
                if "text" in x and isinstance(x["text"], str):
                    p.append(x["text"])
                elif "image" in t:
                    p.append("[gorsel]")
                else:
                    p.append(json.dumps(x, ensure_ascii=False))
            else:
                p.append(str(x))
        return "\n".join(p)
    return "" if v is None else json.dumps(v, ensure_ascii=False)


def _baslik(b, ilk, yol):
    return (b or ilk or os.path.basename(yol)).replace("\n", " ").strip()[:60]


# ------------------------------------------------------------- Claude Code
def claude_oku(yol):
    mesajlar, baslik, ilk, cwd = [], None, None, None
    for d in _satirlar(yol):
        t = d.get("type")
        if t == "custom-title" and d.get("customTitle"):
            baslik = d["customTitle"]
        ek = d.get("attachment") or {}
        if t == "attachment" and ek.get("type") == "queued_command" and not d.get("isSidechain"):
            # ajan calisirken gonderilen kullanici mesaji ("mid-turn")
            istem = ek.get("prompt")
            if isinstance(istem, str) and istem.strip():
                mesajlar.append({"role": "user", "content": istem, "timestamp": d.get("timestamp")})
                ilk = ilk or istem.strip()
            continue
        if t not in ("user", "assistant") or d.get("isSidechain"):
            continue
        cwd = cwd or d.get("cwd")
        m = d.get("message") or {}
        rol = m.get("role") or t
        icerik = m.get("content")
        ts = d.get("timestamp")
        if isinstance(icerik, str):
            if d.get("isMeta"):
                continue
            mesajlar.append({"role": rol, "content": icerik, "timestamp": ts})
            if rol == "user" and not ilk:
                ilk = icerik.strip()
            continue
        if not isinstance(icerik, list):
            continue
        metin, dusunce, cagri = [], [], []
        for b in icerik:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                metin.append(b.get("text", ""))
            elif bt == "thinking":
                dusunce.append(b.get("thinking", ""))
            elif bt == "image":
                metin.append("[gorsel]")
            elif bt == "tool_use":
                cagri.append({"id": b.get("id"), "type": "function",
                              "function": {"name": b.get("name"),
                                           "arguments": json.dumps(b.get("input"), ensure_ascii=False)}})
            elif bt == "tool_result":
                mesajlar.append({"role": "tool", "tool_call_id": b.get("tool_use_id"),
                                 "content": _metin(b.get("content")), "timestamp": ts})
        if metin or dusunce or cagri:
            msj = {"role": rol, "content": "\n".join(metin), "timestamp": ts}
            if dusunce:
                msj["reasoning"] = "\n".join(dusunce)
            if cagri:
                msj["tool_calls"] = cagri
            mesajlar.append(msj)
            if rol == "user" and metin and not ilk:
                ilk = "\n".join(metin).strip()
    return _baslik(baslik, ilk, yol), mesajlar, cwd


# ------------------------------------------------------------- Codex
_CODEX_BAGLAM = ("<environment_context>", "<user_instructions>", "# AGENTS.md",
                 "<app-context>", "<permissions",
                 "<recommended_plugins>", "<skills_instructions>", "<INSTRUCTIONS>")


def codex_oku(yol):
    mesajlar, ilk, cwd = [], None, None
    for d in _satirlar(yol):
        t, p = d.get("type"), d.get("payload") or {}
        ts = d.get("timestamp")
        if t == "session_meta":
            cwd = cwd or p.get("cwd")
            continue
        if t != "response_item":
            continue
        pt = p.get("type")
        if pt == "message":
            rol = p.get("role")
            if rol not in ("user", "assistant"):
                continue                      # developer/system: oturuma ozel baglam
            metin = _metin(p.get("content"))
            if rol == "user" and metin.lstrip().startswith(_CODEX_BAGLAM):
                continue
            mesajlar.append({"role": rol, "content": metin, "timestamp": ts})
            if rol == "user" and not ilk and not metin.lstrip().startswith('<external_codex_apps_'):
                ilk = metin.strip()
        elif pt in ("function_call", "custom_tool_call", "local_shell_call"):
            arg = p.get("arguments") if pt == "function_call" else p.get("input") or p.get("action")
            if not isinstance(arg, str):
                arg = json.dumps(arg, ensure_ascii=False)
            cagri = {"id": p.get("call_id"), "type": "function",
                     "function": {"name": p.get("name") or pt, "arguments": arg}}
            son = mesajlar[-1] if mesajlar else None
            if son and son["role"] == "assistant":
                son.setdefault("tool_calls", []).append(cagri)
            else:
                mesajlar.append({"role": "assistant", "content": "", "tool_calls": [cagri],
                                 "timestamp": ts})
        elif pt in ("function_call_output", "custom_tool_call_output"):
            cikti = p.get("output")
            if isinstance(cikti, dict):
                cikti = cikti.get("content", cikti)
            mesajlar.append({"role": "tool", "tool_call_id": p.get("call_id"),
                             "content": _metin(cikti), "timestamp": ts})
        elif pt == "reasoning":
            ozet = _metin(p.get("summary") or [])   # encrypted_content okunamaz, atlanir
            if ozet.strip():
                mesajlar.append({"role": "assistant", "content": "", "reasoning": ozet,
                                 "timestamp": ts})
    return _baslik(None, ilk, yol), mesajlar, cwd


def oku(yol):
    """-> (bicim, baslik, mesajlar, cwd); bicim bilinmiyorsa (None, None, None, None)."""
    b = bicim_algila(yol)
    if b == "claude":
        return (b,) + claude_oku(yol)
    if b == "codex":
        return (b,) + codex_oku(yol)
    return None, None, None, None


def son_oturum(bicim, cwd=None, haric=None):
    """Bir ajanin en son oturum dosyasi (istege bagli: ayni calisma dizini)."""
    import glob
    if bicim == "claude":
        desen = os.path.expanduser("~/.claude/projects/*/*.jsonl")
    elif bicim == "codex":
        desen = os.path.join(os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex")),
                             "sessions", "**", "rollout-*.jsonl")
    else:
        return None
    dosyalar = sorted(glob.glob(desen, recursive=True), key=os.path.getmtime, reverse=True)
    for f in dosyalar[:200]:
        if haric and haric in f:
            continue
        if cwd is None:
            return f
        if bicim == "codex":
            for d in _satirlar(f, 3):
                if d.get("type") == "session_meta":
                    if os.path.normcase(d.get("payload", {}).get("cwd") or "") == os.path.normcase(cwd):
                        return f
                    break
        else:
            for d in _satirlar(f, 30):
                if d.get("cwd"):
                    if os.path.normcase(d["cwd"]) == os.path.normcase(cwd):
                        return f
                    break
    return None
