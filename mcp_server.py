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
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("oturum-sikistirici")


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
    jev:    True = akilli modda buyuk arac ciktilarini Jev'e (System-One) toplu sor;
            olu olanlar paketten cikarilir (izli marker), gerekenler OLDUGU GIBI
            kalir, ortalar statik dilime duser. Jev erisilemezse statik dilime doner.
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
    jev: True = buyuk arac ciktilarini Jev'e sor; oluler paketten cikarilir.
    Baska bir sey isterseniz once durum_tablosu ile semayi gorun."""
    try:
        sid, mesajlar, baslik = hkp.oturum_oku(session_id)
        sonuc = hkp.sikistir(mesajlar, cikti, mod, baslik=baslik, jev=jev)
        sonuc["session_id"] = sid
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


async def _run(name: str, **kw):
    """Async kuyruk yolu (plugin) — ayni islevler, await edilebilir sarmalayici."""
    import anyio.to_thread
    fn = {"sikistir": sikistir, "iceri_ac": iceri_ac, "kilavuz": kilavuz,
          "mesajlar": mesajlar, "bilgi": bilgi, "oturum_sikistir": oturum_sikistir,
          "durum_tablosu": durum_tablosu}[name]
    return await anyio.to_thread.run_sync(lambda: fn(**kw))


if __name__ == "__main__":
    mcp.run()
