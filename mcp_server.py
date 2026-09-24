# -*- coding: utf-8 -*-
"""oturum-sikistirici — Hermes oturum transkriptlerini HKP1'e paketleyen MCP sunucusu.

Motor: ayni dizindeki hkp.py (saf, MCP'siz) — /czip plugin'i de ayni motoru kullanir.
Amac: uzun bir oturumu yeni oturuma tasinacak kadar küçültmek; okuyan AI eksiksiz
anlayip devam edebilsin (paket ACILMADAN kilavuz+mesajlar ile hedefli okunur).
Güvenlik: state.db daima READ-ONLY; silme/bozma yok.
"""

import asyncio
import inspect
import json
import os
import sys

import hkp
import time

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:  # `pip install mcp` yoksa: asagidaki saf-stdlib sunucu
    FastMCP = None


class _MiniMCP:
    """`mcp` paketi kurulu degilken ayni araclari sunan saf-stdlib MCP sunucusu.

    Neden: czip 0 bagimliliktir; Claude Desktop/Code'da MCP'nin tek eksik
    `pip install mcp` yuzunden "Connection closed" ile dusmesi kabul edilemez.
    Kapsam: stdio JSON-RPC 2.0 — initialize, tools/list, tools/call, ping."""

    TIPLER = {str: "string", int: "integer", bool: "boolean", float: "number"}

    def __init__(self, ad):
        self.ad = ad
        self.araclar = {}

    def tool(self):
        def kaydet(fn):
            self.araclar[fn.__name__] = fn
            return fn
        return kaydet

    def _sema(self, fn):
        ozellik, zorunlu = {}, []
        for ad, p in inspect.signature(fn).parameters.items():
            ozellik[ad] = {"type": self.TIPLER.get(p.annotation, "string")}
            if p.default is inspect.Parameter.empty:
                zorunlu.append(ad)
            else:
                ozellik[ad]["default"] = p.default
        return {"type": "object", "properties": ozellik, "required": zorunlu}

    def isle(self, istek):
        """Tek JSON-RPC istegi -> cevap sozlugu (bildirimse None)."""
        yontem, rid = istek.get("method"), istek.get("id")
        if rid is None:
            return None  # bildirim (notifications/initialized vb.)
        par = istek.get("params") or {}
        if yontem == "initialize":
            sonuc = {"protocolVersion": par.get("protocolVersion") or "2024-11-05",
                     "capabilities": {"tools": {}},
                     "serverInfo": {"name": self.ad, "version": "1.0.0"}}
        elif yontem == "ping":
            sonuc = {}
        elif yontem == "tools/list":
            sonuc = {"tools": [{"name": ad, "description": inspect.getdoc(fn) or "",
                                "inputSchema": self._sema(fn)}
                               for ad, fn in self.araclar.items()]}
        elif yontem == "tools/call":
            fn = self.araclar.get(par.get("name"))
            if fn is None:
                return {"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32602, "message": "bilinmeyen arac"}}
            try:
                metin, hata_mi = str(fn(**(par.get("arguments") or {}))), False
            except Exception as e:  # noqa: BLE001
                metin, hata_mi = "%s: %s" % (type(e).__name__, e), True
            sonuc = {"content": [{"type": "text", "text": metin}], "isError": hata_mi}
        else:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": "yontem yok: %s" % yontem}}
        return {"jsonrpc": "2.0", "id": rid, "result": sonuc}

    def run(self):
        for satir in sys.stdin:
            satir = satir.strip()
            if not satir:
                continue
            try:
                cevap = self.isle(json.loads(satir))
            except ValueError:
                cevap = {"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32700, "message": "parse error"}}
            if cevap is not None:
                sys.stdout.write(json.dumps(cevap, ensure_ascii=False) + "\n")
                sys.stdout.flush()


mcp = FastMCP("oturum-sikistirici") if FastMCP else _MiniMCP("oturum-sikistirici")


@ mcp.tool()
def sikistir(girdi: str, cikti: str, mod: str = "akilli",
             arac_bas: int = 2000, arac_son: int = 2000, jev: bool = False) -> str:
    """Hermes oturum dosyasini/listesini HKP1'e sikistirir.

    girdi:  dosya yolu (.json/.jsonl) veya iletilerin JSON/metni
    cikti:  .hkp cikis yolu (uzanti otomatik eklenir)
    mod:    'akilli' (varsayilan) = icerik tam, tasima iletisindeki gereksiz tekrar
            silinir (bosluk, satir tekrar, sozluk, null'lar, reasoning ikizi);
            'eksiksiz' = her bayt geri doner, hicbir kirpma yok.
    arac_bas/son: akilli modda arac icerigi bas/son dilim baytlari (0 = kirpma).
    jev:    True = akilli modda buyuk arac ciktilarini karar kapisina (varsayilan
            YEREL Laya, bkz. karar.py) toplu sor; olu olanlar paketten cikarilir
            (izli marker), gerekenler OLDUGU GIBI kalir, ortalar statik dilime
            duser. Motor erisilemezse statik dilime doner.
    Doner: yol, bayt, oran, mesaj sayisi, sozluk, eksik bildirimi, jev raporu, parca sayisi."""
    try:
        kaynak_bayt = None
        if isinstance(girdi, str) and os.path.isfile(os.path.expanduser(girdi.strip())):
            kaynak_bayt = os.path.getsize(os.path.expanduser(girdi.strip()))
        mesajlar = hkp.girdi_ayristir(girdi)
        if not mesajlar:
            return hkp.hata("girdiden mesaj cikmadi")
        return json.dumps(hkp.sikistir(mesajlar, cikti, mod, arac_bas, arac_son,
                                       kaynak_bayt=kaynak_bayt, jev=jev), ensure_ascii=False)
    except Exception as e:
        return hkp.hata(e)


@ mcp.tool()
def iceri_ac(dosya: str, parca: int = 0) -> str:
    """HKP dosyasini tam metne cevirir (tum sozluk jetonlari cozulur).

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
    """HKP paketini ACAMDAN okuma kilavuzu — yeni oturum icin asil giris noktasidir.

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
    """HKP'den secili mesaj araligini TAM metin getirir (paket acilmadan, hedefli okuma).

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
    """HKP ozeti: mesaj/sozluk sayisi, parca sayisi, baytlar, oran, sozluk onizleme."""
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
def oturum_sikistir(session_id: str, cikti: str, mod: str = "akilli",
                    jev: bool = False) -> str:
    """Hermes state.db'den oturum okuyup (READ-ONLY) sikistirir — yeni oturuma
    tasinmasi kolay .hkp uretir.

    session_id: 'son' / 'en-uzun' / net ID / benzersiz onek.
    jev: True = buyuk arac ciktilarini karar kapisina sor (varsayilan YEREL
         Laya; bulut Jev yalniz ayarla secilirse); oluler paketten cikarilir.
    Baska bir sey isterseniz once durum_tablosu ile semayi gorun."""
    try:
        sid, mesajlar, baslik = hkp.oturum_oku(session_id)
        sonuc = hkp.sikistir(mesajlar, cikti, mod, baslik=baslik, jev=jev)
        sonuc["session_id"] = sid
        return json.dumps(sonuc, ensure_ascii=False)
    except Exception as e:
        return hkp.hata(e)


@mcp.tool()
def claude_oturumlari(limit: int = 10) -> str:
    """Claude Code / Claude Desktop oturum dokumleri (~/.claude/projects/*/*.jsonl).

    Doner: [{id, proje, tarih, kb}] — en yeni once. id'yi claude_oturum_paketle'ye ver."""
    import ccd_dokum
    try:
        return json.dumps([{"id": os.path.splitext(os.path.basename(y))[0],
                            "proje": os.path.basename(os.path.dirname(y)),
                            "tarih": time.strftime("%Y-%m-%d %H:%M", time.localtime(mt)),
                            "kb": round(b / 1024, 1)}
                           for y, mt, b in ccd_dokum.oturumlar(limit=limit)],
                          ensure_ascii=False)
    except Exception as e:
        return hkp.hata(e)


@mcp.tool()
def claude_oturum_paketle(oturum: str = "son", mod: str = "akilli",
                          laya: bool = False) -> str:
    """Bir Claude Code / Claude Desktop oturumunu HKP1 paketine sikistirir.

    oturum: 'son' (en yeni) / oturum uuid'si (onek yeter) / .jsonl dosya yolu.
    laya:   True = buyuk arac ciktilarini yerel Laya karar kapisina sor.
    Doner: paket yolu + 6 haneli kisa ID. Okuma: hafiza_harita / mesajlar."""
    import ccd_dokum
    try:
        yol = ccd_dokum.coz(oturum if oturum.startswith("cc:") else "cc:" + oturum)
        if not yol:
            return hkp.hata("Claude Code oturumu bulunamadi: %s" % oturum)
        mesajlar, baslik = ccd_dokum.oku(yol)
        if not mesajlar:
            return hkp.hata("oturum bos: %s" % yol)
        import re as _re
        temiz = _re.sub(r"[^A-Za-z0-9-]+", "-", (baslik or "claude")[:48]).strip("-") or "claude"
        cikti = os.path.join(os.path.expanduser(hkp.PAKET_DIZIN),
                             "CC-%s-%s.hkp" % (temiz, time.strftime("%Y%m%d-%H%M%S")))
        sonuc = hkp.sikistir(mesajlar, cikti, mod, baslik=baslik,
                             kaynak_bayt=os.path.getsize(yol), jev=laya)
        sonuc["id"] = hkp.id_ata(sonuc["yol"], baslik)
        sonuc["kaynak"] = yol
        return json.dumps(sonuc, ensure_ascii=False)
    except Exception as e:
        return hkp.hata(e)


@ mcp.tool()
def durum_tablosu() -> str:
    """state.db tablo/kolon/doluluk raporu (salt-okunur, tek sorgu/tablo)."""
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
    """Gecmis TUM oturumlarda ara (uzun sureli hafiza).

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
    """hafiza_ara'nin bulduğu iletilerin TAM metnini getirir.

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
    """Bir paketin RAG haritasi: is tanimlari, arac histogrami, son iletler.

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
    """Hafiza indeksini yeni paketlerle gunceller (artimli; saniyeler surer)."""
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
    """Hafiza deposunun durumu: kac paket, kac ileti, ne kadar yer, ne kadar guncel."""
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


# ─────────────────────────────────────────────────────────────────────────────
# OTOPILOT — yon, brifing, hatirlama, temizlik (hook'larla ayni cekirdek)
# ─────────────────────────────────────────────────────────────────────────────
@mcp.tool()
def yon_karti(paket: str = "son") -> str:
    """Paketin YON KARTI: hedef, bozulmamasi gereken kararlar, acik isler, son hata,
    dokunulan dosyalar ve karar mekanizmasinin onerdigi TEK sonraki adim.
    Devralinan bir isi surdurmeden once bunu oku (~200-500 token)."""
    import yon
    try:
        yol = hkp.id_coz(paket)
        meta = hkp.meta_oku(yol)
        k = meta.get("yon")
        if not k:
            _, kayitlar = hkp.yukle(yol)
            soz = meta.get("soz", [])
            k = yon.kart([dict(x, c=hkp.coz_sozluk(x["c"], soz)) if isinstance(x.get("c"), str)
                          else x for x in kayitlar], meta.get("baslik"))
        return yon.metin(k, paket if len(paket) == 6 else None, (meta.get("baslik") or "")[:60])
    except Exception as e:
        return hkp.hata(e)


@mcp.tool()
def hafiza_brifing(cwd: str = "") -> str:
    """"Bu projede en son ne yaptim?" — gunlukten son isler + en son paketin yon karti.
    cwd: proje dizini (bos = tum projeler)."""
    import hafiza
    return hafiza.brifing(cwd=cwd or None, n=5) or "(henuz kayit yok)"


@mcp.tool()
def hatirla(istek: str, limit: int = 3) -> str:
    """Bu istekle ilgili gecmis is (RAG). Alaka kapisi: sorgu kelimelerinin en az
    yarisi ayni iletide gecmeli — alakasiz hatirlatma yerine bos doner."""
    import hafiza
    metin, _ = hafiza.hatirlatma(istek, limit=max(1, min(int(limit), 8)))
    return metin or "(ilgili gecmis yok)"


@mcp.tool()
def temizlik(uygula: bool = False) -> str:
    """Haftalik temizlik. uygula=False: yalniz plan (hicbir seye dokunmaz).
    uygula=True: gereksiz yiginlari COPE tasir (geri alinabilir: czip temizle geri),
    30 gunluk copu bosaltir, eski id'leri yeni paketlere yonlendirir."""
    import temizlik as _t
    try:
        islemler = _t.plan()
        if not uygula:
            return json.dumps({"plan": _t.ozet(islemler),
                               "ogeler": [{"neden": x["neden"], "dosya": os.path.basename(x["yol"])}
                                          for x in islemler[:40]]}, ensure_ascii=False)
        return json.dumps(_t.uygula(islemler), ensure_ascii=False)
    except Exception as e:
        return hkp.hata(e)


@mcp.tool()
def czip_yardim(konu: str = "") -> str:
    """czip-help: tum komutlar gruplu ve tanimlariyla. konu: paket | hafiza | auto |
    temizlik | claude | karar | hermes | mesaj ya da bir komut parcasi (ornek 'auto64')."""
    import yardim
    return yardim.metin(konu or None, dil="tr")


async def _run(name: str, **kw):
    """Async kuyruk yolu (plugin) — ayni islevler, await edilebilir sarmalayici."""
    import anyio.to_thread
    fn = {"sikistir": sikistir, "iceri_ac": iceri_ac, "kilavuz": kilavuz,
          "mesajlar": mesajlar, "bilgi": bilgi, "oturum_sikistir": oturum_sikistir,
          "durum_tablosu": durum_tablosu}[name]
    return await anyio.to_thread.run_sync(lambda: fn(**kw))


if __name__ == "__main__":
    mcp.run()
