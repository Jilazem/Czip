# -*- coding: utf-8 -*-
"""oturum-sikistirici — Hermes oturum transkriptlerini HKP1'e paketleyen MCP sunucusu.

Motor: ayni dizindeki hkp.py (saf, MCP'siz) — /czip plugin'i de ayni motoru kullanir.
Amac: uzun bir oturumu yeni oturuma tasinacak kadar küçültmek; okuyan AI eksiksiz
anlayip devam edebilsin (paket ACILMADAN kilavuz+mesajlar ile hedefli okunur).
Güvenlik: state.db daima READ-ONLY; silme/bozma yok.
"""

import asyncio
import json
import os

import hkp
import time
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("oturum-sikistirici")


@ mcp.tool()
def sikistir(girdi: str, cikti: str, mod: str = "eksiksiz",
             arac_bas: int = 2000, arac_son: int = 2000, jev: bool = False) -> str:
    """Pack a transcript / Bir dökümü paketle. Default: lossless / Varsayılan: eksiksiz.

    girdi: JSON/JSONL path or message JSON / dosya yolu veya ileti JSON'u.
    cikti: explicit output path / açık çıktı yolu. mod: eksiksiz|akilli (full|smart).
    jev is the compatibility name for an optional LOCAL Clef gate, used only in
    smart mode. No remote fallback / jev yalnız akıllı moddaki yerel Clef kapısıdır.
    Smart mode can trim content / Akıllı mod içerik kırpabilir; inspect eksik/jev.
    Source is preserved / Kaynak korunur.
    """
    try:
        kaynak_bayt = None
        if isinstance(girdi, str) and os.path.isfile(os.path.expanduser(girdi.strip())):
            kaynak_bayt = os.path.getsize(os.path.expanduser(girdi.strip()))
        mesajlar = hkp.girdi_ayristir(girdi)
        if not mesajlar:
            return hkp.hata("girdiden mesaj cikmadi")
        mod = {"full":"eksiksiz", "smart":"akilli"}.get(mod, mod)
        return json.dumps(hkp.sikistir(mesajlar, cikti, mod, arac_bas, arac_son,
                                       kaynak_bayt=kaynak_bayt, jev=jev), ensure_ascii=False)
    except Exception as e:
        return hkp.hata(e)


@ mcp.tool()
def iceri_ac(dosya: str, parca: int = 0) -> str:
    """Full export (prefer map/range) / HKP dosyasını tam metne çevirir (tum sozluk jetonlari cozulur).

    parca: 0 = tum iletler tek JSONL, 1..N = N. parca (40 ilet/parca).
    Buyuk oturumlar icin ONERILEN yol iceri_ac degil, kilavuz + mesajlar'dir
    (paket acilmadan okunur, context'i sisirmez).
    Ilet bicimi: {rol, icerik, tool_calls:[{name,arguments,id}], reasoning,
    reasoning2, finish_reason, meta}"""
    try:
        return json.dumps(hkp.iceri_ac(dosya, parca), ensure_ascii=False)
    except Exception as e:
        return hkp.hata(e)


@ mcp.tool()
def kilavuz(dosya: str) -> str:
    """Guide without full export / HKP paketini açmadan okuma kılavuzu — yeni oturum icin asil giris noktasidir.

    Tam oturumu context'e yuklemeden kaldigi yerden devam etmeyi saglar:
    - ilet basina TEK SATIR indeks: idx | rol | icerikten 120 karakter (+tc isimleri)
    - son iletler TAM METIN (baglamdan kopmamak icin, en fazla son 6 ilet)
    - bitis durumu (son finish_reason, kullaniciyi bekleme)
    - nasil devam edilecegi talimati
    Ayrinti gereken yer icin `mesajlar(dosya, aralik)` ile ilgili pencereyi cagirsan yeter.
    Doner: JSON {baslik, toplam, indeks:[...], son:[...], durum, talimat}"""
    try:
        return json.dumps(hkp.kilavuz(dosya), ensure_ascii=False)
    except Exception as e:
        return hkp.hata(e)


@ mcp.tool()
def mesajlar(dosya: str, aralik: str) -> str:
    """Exact selected range / HKP'den seçili mesaj aralığını tam metin getirir (paket acilmadan, hedefli okuma).

    aralik: '45' tek mesaj, '40-60' aralik (0-tabanli indeks, kilavuz indeksindeki i degeri).
    Azami 80 mesaj/cagri. reasoning/tool_calls/arguments dahildir.
    Doner: {ok, istenen, gelen, iletler:[tam ilet...]}"""
    try:
        donen = hkp.mesaj_araligi(dosya, aralik)
        return json.dumps({"ok": True, "istenen": aralik, "gelen": len(donen),
                           "iletler": donen}, ensure_ascii=False)
    except Exception as e:
        return hkp.hata(e)


@ mcp.tool()
def bilgi(dosya: str) -> str:
    """Pack metadata / HKP özeti: mesaj/sozluk sayisi, parca sayisi, baytlar, oran, sozluk onizleme."""
    try:
        yol = os.path.expanduser(dosya)
        meta, veri = hkp.paket_oku(yol)
        return json.dumps({
            "ok": True, "yol": yol, "paket_bayt": os.path.getsize(yol),
            "govde_bayt": len(veri.encode("utf-8")),
            "mesaj": meta.get("n"), "sozluk": len(meta.get("soz", [])),
            "baslik": meta.get("baslik"),
            "parca": max(1, -(-meta.get("n", 0) // hkp.PARCA_ILET)),
            "sozluk_ornek": [s[:70] for s in meta.get("soz", [])[:8]],
        }, ensure_ascii=False)
    except Exception as e:
        return hkp.hata(e)


@ mcp.tool()
def oturum_sikistir(session_id: str, cikti: str, mod: str = "eksiksiz",
                    jev: bool = False) -> str:
    """Pack one explicit Hermes session / Açıkça seçilen tek Hermes oturumunu paketle.

    session_id: exact ID preferred / tam kimlik tercih edilir. Active selectors
    require this caller's context / aktif seçim bu çağıranın bağlamını gerektirir.
    The standalone MCP server has no per-request chat identity: supply an exact
    ID, never last / Bağımsız MCP sunucusunda sohbet kimliği yoktur; tam kimlik ver.
    No merging or closing / Birleştirme veya kapatma yok. jev = optional local Clef.
    """
    try:
        if session_id in ("son", "last", "latest", "active", "aktif", "current", "en-uzun", "longest"):
            raise ValueError(hkp.T("MCP requires an explicit session ID", "MCP açık oturum kimliği gerektirir"))
        sid, mesajlar, baslik = hkp.oturum_oku(session_id)
        mod = {"full":"eksiksiz", "smart":"akilli"}.get(mod, mod)
        sonuc = hkp.sikistir(mesajlar, cikti, mod, baslik=baslik, jev=jev)
        sonuc["session_id"] = sid
        return json.dumps(sonuc, ensure_ascii=False)
    except Exception as e:
        return hkp.hata(e)


@ mcp.tool()
def durum_tablosu() -> str:
    """Read-only schema / state.db tablo/kolon/doluluk raporu (salt-okunur, tek sorgu/tablo)."""
    try:
        con = hkp._db()
        out = {}
        try:
            for tab, in con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                    "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
                try:
                    n = con.execute(f'SELECT COUNT(*) FROM "{tab}"').fetchone()[0]
                    kolonlar = [r[1] for r in con.execute(f'PRAGMA table_info("{tab}")')]
                    dolu = {}
                    for c in kolonlar[:14]:
                        try:
                            dolu[c] = con.execute(
                                f'SELECT COUNT(*) FROM "{tab}" WHERE "{c}" IS NOT NULL').fetchone()[0]
                        except Exception:
                            dolu[c] = "?"
                    out[tab] = {"satir": n, "kolonlar": kolonlar, "dolu": dolu}
                except Exception:
                    out[tab] = "okunamadi"
        finally:
            con.close()
        return json.dumps(out, ensure_ascii=False)
    except Exception as e:
        return hkp.hata(e)


# ─────────────────────────────────────────────────────────────────────────────
# LONG-TERM MEMORY — every past session, searchable, for Hermes and any agent.
# The packages are the archive; depo.py keeps an FTS index over all of them.
# Typical cost: a hafiza_ara call returns ~200-600 tokens instead of reloading
# a 90k-token transcript.
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
def hafiza_ara(sorgu: str, limit: int = 8) -> str:
    """Search across archives / Geçmiş tüm oturumlarda ara (uzun sureli hafiza).

    Bir isi daha once yapip yapmadigini, nasil cozdugunu, hangi karari neden
    verdigini bulmak icin kullan. Tam metin gerekirse donen paket kimligi ve
    ileti numarasiyla `hafiza_getir` cagir.

    sorgu: aranacak ifade (tam ifade eslesmesi)
    limit: en fazla kac paket dondurulecek
    """
    import depo
    try:
        r = depo.ara(sorgu, limit=max(1, min(int(limit), 25)))
    except Exception as e:
        return json.dumps({"durum": "hata", "mesaj": str(e),
                           "ipucu": "once `czip index` calistirin"}, ensure_ascii=False)
    out = []
    for pk in r["packages"]:
        out.append({
            "paket": pk["kid"] or os.path.basename(pk["path"]),
            "baslik": str(pk["title"])[:80],
            "zaman": time.strftime("%Y-%m-%d %H:%M", time.localtime(pk["mtime"])),
            "isabetler": [{"i": h["i"], "rol": h["role"], "metin": h["snippet"][:200]}
                          for h in pk["hits"]],
        })
    return json.dumps({"durum": "ok", "toplam_isabet": r["total"],
                       "paketler": out,
                       "sonraki_adim": "tam metin icin hafiza_getir(paket, aralik)"},
                      ensure_ascii=False, indent=1)


@mcp.tool()
def hafiza_getir(paket: str, aralik: str) -> str:
    """Read selected memory range / hafiza_ara'nın bulduğu iletileri getirir.

    paket: hafiza_ara'nin dondurdugu paket kimligi
    aralik: "120" veya "118-125" (en fazla 80 ileti)
    """
    import hkp as _h
    try:
        yol = _h.id_coz(paket) or paket
        iletler = _h.mesaj_araligi(yol, aralik)
    except Exception as e:
        return json.dumps({"durum": "hata", "mesaj": str(e)}, ensure_ascii=False)
    return json.dumps({"durum": "ok", "paket": paket, "aralik": aralik,
                       "iletler": iletler}, ensure_ascii=False, indent=1)


@mcp.tool()
def hafiza_harita(paket: str) -> str:
    """Bounded memory map / Bir paketin RAG haritası: is tanimlari, arac histogrami, son iletler.

    Tam dokumu YUKLEMEZ (~1.5k token). Once bunu al, sonra hafiza_getir ile
    yalnizca ihtiyacin olan araligi cek.
    """
    import hkp as _h
    try:
        yol = _h.id_coz(paket) or paket
        return json.dumps(_h.harita(yol), ensure_ascii=False, indent=1)
    except Exception as e:
        return json.dumps({"durum": "hata", "mesaj": str(e)}, ensure_ascii=False)


@mcp.tool()
def hafiza_guncelle(tam: bool = False) -> str:
    """Refresh memory index / Hafıza indeksini yeni paketlerle günceller (artimli; saniyeler surer)."""
    import depo
    try:
        r = depo.guncelle(tam=bool(tam))
        st = depo.istatistik() or {}
        return json.dumps({"durum": "ok", "yeni": r["yeni"], "guncellenen": r["guncellenen"],
                           "degismeyen": r["atlanan"], "indekslenen_ileti": r["ileti"],
                           "toplam_paket": st.get("packages"),
                           "toplam_ileti": st.get("indexed"),
                           "depo_mb": round(r["boyut"] / 1e6, 1)},
                          ensure_ascii=False)
    except Exception as e:
        return json.dumps({"durum": "hata", "mesaj": str(e)}, ensure_ascii=False)


@mcp.tool()
def hafiza_durum() -> str:
    """Memory status / Hafıza deposunun durumu: kac paket, kac ileti, ne kadar yer, ne kadar guncel."""
    import depo
    st = depo.istatistik()
    if not st:
        return json.dumps({"durum": "yok", "ipucu": "hafiza_guncelle() ile olusturun"},
                          ensure_ascii=False)
    return json.dumps({"durum": "ok", "paket": st["packages"], "ileti": st["indexed"],
                       "mb": round(st["bytes"] / 1e6, 1),
                       "en_eski": time.strftime("%Y-%m-%d", time.localtime(st["oldest"] or 0)),
                       "en_yeni": time.strftime("%Y-%m-%d %H:%M", time.localtime(st["newest"] or 0)),
                       "yol": st["path"]}, ensure_ascii=False)


async def _run(name: str, **kw):
    """Async kuyruk yolu (plugin) — ayni islevler, await edilebilir sarmalayici."""
    import anyio.to_thread
    fn = {"sikistir": sikistir, "iceri_ac": iceri_ac, "kilavuz": kilavuz,
          "mesajlar": mesajlar, "bilgi": bilgi, "oturum_sikistir": oturum_sikistir,
          "durum_tablosu": durum_tablosu}[name]
    return await anyio.to_thread.run_sync(lambda: fn(**kw))


if __name__ == "__main__":
    mcp.run()
