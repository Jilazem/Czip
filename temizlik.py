# -*- coding: utf-8 -*-
"""temizlik.py — haftalik temizlik: gereksiz yiginlari cope tasir, copu bosaltir.

Guvenlik kurallari (bunlar degismez):
  - Hicbir sey DOGRUDAN silinmez. Once `.cop/<zaman>/` altina TASINIR, geri
    alma listesi (geri.json) yazilir; `czip temizle geri` ile geri gelir.
    Cop `cop_gun` (30) gun sonra gercekten bosaltilir.
  - Yalniz czip'in KENDI urettigi seyler: .hkp paketleri, czip dosyalarinin
    .bak/.yedek kopyalari, czip gunluk/log dosyalari, hook durum dosyalari.
    Hermes state.db'ye ve kullanici dosyalarina dokunulmaz.
  - Bir paket ancak BASKA bir paket onun icerigini tasiyorsa (>= %95 kullanici +
    asistan iletisi birebir) "yenisi var" diye tasinir; eski kisa ID yeni pakete
    YONLENDIRILIR — hicbir `czip aralik <eski-id>` kirilmaz.
  - Kayipsiz (--eksiksiz) paket, kirpilmis bir paket yuzunden asla tasinmaz.

Kurallar:
  ayni_icerik : dosya baytlari birebir ayni -> en yenisi kalir
  yenisi_var  : ayni oturumun/basligin sonraki paketi bunu kapsiyor
  hayalet     : <=2 iletili, 4KB alti, 7 gunden eski (bos/yarim oturum)
  eski_yedek  : czip dosyasinin .bak/.yedek kopyasi, 14 gunden eski,
                her dosyanin en yeni 2 yedegi korunur
  buyuk_log   : 2MB ustu log -> son 512KB kalir (eskisi cope)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time

import hkp

GUN = 86400
HAYALET_ILET = 2
HAYALET_BAYT = 4096
YEDEK_GUN = 14
YEDEK_TUT = 2
LOG_MAX = 2 * 1024 * 1024
LOG_KALAN = 512 * 1024
KAPSAMA = 0.95
YEDEK_RE = re.compile(r"^(?P<ana>.+?\.(py|json|md|sh|yaml|yml|txt))\.(bak|yedek)[-_.]?.*$")
CZIP_DOSYALARI = {"hkp.py", "birlestir.py", "depo.py", "ayar.py", "karar.py", "yon.py",
                  "hafiza.py", "temizlik.py", "laya_kapi.py", "ccd_dokum.py", "server.py",
                  "mcp_server.py", "gorev.py", "czip_auto.py", "claude_hook.py",
                  "context-nobetci.py", "SKILL.md", "plugin.yaml", "__init__.py",
                  "ayar.json", "kayit.json", "config.yaml"}


def _dizin():
    return os.path.expanduser(hkp.PAKET_DIZIN)


def cop_dizin():
    return os.path.join(_dizin(), ".cop")


def durum_yolu():
    return os.path.join(_dizin(), ".temizlik.json")


def _ayar():
    try:
        import ayar
        return ayar.oku()
    except Exception:  # noqa: BLE001
        return {}


def yedek_dizinleri():
    """.bak/.yedek taranacak dizinler (CZIP_YEDEK_DIZINLERI=yol1:yol2 ile degisir)."""
    env = os.environ.get("CZIP_YEDEK_DIZINLERI")
    if env:
        return [os.path.expanduser(d) for d in env.split(os.pathsep) if d]
    return sorted({os.path.dirname(os.path.abspath(hkp.__file__)), _dizin(),
                   os.path.expanduser("~/007-HERMES/10-MCP-SERVERS/oturum-sikistirici")})


def son_calisma():
    try:
        with open(durum_yolu(), encoding="utf-8") as f:
            return float(json.load(f).get("son") or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def baslat_isareti():
    """Arka plan temizligi baslamadan once: 'basladi' yaz — ayni anda acilan
    ikinci oturum temizligi ikinci kez baslatmasin (kilitsiz, yeterli)."""
    os.makedirs(_dizin(), exist_ok=True)
    with open(durum_yolu(), "w", encoding="utf-8") as f:
        json.dump({"son": time.time(), "durum": "basladi"}, f)


def gerekli_mi(simdi=None):
    """Son temizlikten bu yana `temizlik_gun` (7) gun gecti mi?"""
    gun = float(_ayar().get("temizlik_gun", 7) or 7)
    return (simdi or time.time()) - son_calisma() >= gun * GUN


# ------------------------------------------------------------------ analiz
def _paketler():
    d = _dizin()
    if not os.path.isdir(d):
        return []
    out = []
    for f in os.listdir(d):
        if not f.endswith(".hkp"):
            continue
        y = os.path.join(d, f)
        st = os.stat(y)
        try:
            meta = hkp.meta_oku(y)
        except Exception:  # noqa: BLE001
            meta = None
        out.append({"yol": y, "mtime": st.st_mtime, "bayt": st.st_size, "meta": meta})
    out.sort(key=lambda p: p["mtime"])
    return out


def _grup_anahtari(p):
    m = p["meta"] or {}
    sid = (m.get("kaynak") or {}).get("sid")
    if sid:
        return "sid:" + str(sid)
    if m.get("baslik"):
        return "b:" + " ".join(str(m["baslik"]).lower().split())[:120]
    return "d:" + re.sub(r"-\d{8}-\d{6}\.hkp$", "", os.path.basename(p["yol"]))


def _konusma_izi(yol):
    """Paketin kullanici+asistan iletilerinin ozet kumesi (sozluk cozulmus)."""
    meta, kayitlar = hkp.yukle(yol)
    soz = meta.get("soz", [])
    iz = set()
    for k in kayitlar:
        if k.get("r") in ("user", "assistant") and k.get("c"):
            c = " ".join(hkp.coz_sozluk(str(k["c"]), soz).split())[:4000]
            iz.add(hashlib.blake2b((k["r"] + "|" + c).encode("utf-8", "replace"),
                                   digest_size=12).digest())
    return iz


def _dosya_ozeti(yol):
    h = hashlib.blake2b(digest_size=16)
    with open(yol, "rb") as f:
        for parca in iter(lambda: f.read(1 << 20), b""):
            h.update(parca)
    return h.digest()


def plan(simdi=None):
    """Salt-okunur: ne yapilacagini listeler, hicbir seye dokunmaz."""
    simdi = simdi or time.time()
    islemler = []
    paketler = _paketler()
    tasinan = set()

    # 1) birebir ayni dosyalar
    ozet = {}
    for p in paketler:
        ozet.setdefault(_dosya_ozeti(p["yol"]), []).append(p)
    for grup in ozet.values():
        if len(grup) < 2:
            continue
        en_yeni = grup[-1]
        for p in grup[:-1]:
            islemler.append({"tur": "tasi", "yol": p["yol"], "bayt": p["bayt"],
                             "neden": "ayni_icerik", "yonlendir": en_yeni["yol"]})
            tasinan.add(p["yol"])

    # 2) ayni oturumun/basligin sonraki paketi kapsiyor
    gruplar = {}
    for p in paketler:
        if p["yol"] not in tasinan and p["meta"]:
            gruplar.setdefault(_grup_anahtari(p), []).append(p)
    iz_onbellek = {}

    def iz(yol):
        if yol not in iz_onbellek:
            try:
                iz_onbellek[yol] = _konusma_izi(yol)
            except Exception:  # noqa: BLE001
                iz_onbellek[yol] = None
        return iz_onbellek[yol]

    for grup in gruplar.values():
        if len(grup) < 2:
            continue
        for j, eski in enumerate(grup[:-1]):
            if simdi - eski["mtime"] < GUN:
                continue  # bir gunluk tolerans: yeni paketlenmis olabilir
            eski_mod = (eski["meta"] or {}).get("mod")
            for yeni in reversed(grup[j + 1:]):
                if (yeni["meta"] or {}).get("n", 0) < (eski["meta"] or {}).get("n", 0):
                    continue
                if eski_mod == "eksiksiz" and (yeni["meta"] or {}).get("mod") != "eksiksiz":
                    continue  # kayipsiz arsiv, kirpilmis kopya yuzunden gitmez
                a, b = iz(eski["yol"]), iz(yeni["yol"])
                if not a or b is None:
                    continue
                if len(a & b) / len(a) >= KAPSAMA:
                    islemler.append({"tur": "tasi", "yol": eski["yol"], "bayt": eski["bayt"],
                                     "neden": "yenisi_var", "yonlendir": yeni["yol"]})
                    tasinan.add(eski["yol"])
                    break

    # 3) hayalet paketler
    for p in paketler:
        m = p["meta"] or {}
        if (p["yol"] not in tasinan and p["meta"] is not None
                and m.get("n", 0) <= HAYALET_ILET and p["bayt"] < HAYALET_BAYT
                and simdi - p["mtime"] > 7 * GUN):
            islemler.append({"tur": "tasi", "yol": p["yol"], "bayt": p["bayt"],
                             "neden": "hayalet"})

    # 4) eski yedekler (.bak / .yedek) — yalniz czip dosyalarininki
    if _ayar().get("bak_temizle", True):
        for d in yedek_dizinleri():
            if not os.path.isdir(d):
                continue
            yedekler = {}
            for f in os.listdir(d):
                m = YEDEK_RE.match(f)
                if m and m.group("ana") in CZIP_DOSYALARI:
                    y = os.path.join(d, f)
                    if os.path.isfile(y):
                        yedekler.setdefault(m.group("ana"), []).append(y)
            for liste in yedekler.values():
                liste.sort(key=os.path.getmtime, reverse=True)
                for y in liste[YEDEK_TUT:]:
                    if simdi - os.path.getmtime(y) > YEDEK_GUN * GUN:
                        islemler.append({"tur": "tasi", "yol": y, "bayt": os.path.getsize(y),
                                         "neden": "eski_yedek"})

    # 5) buyuk loglar
    d = _dizin()
    if os.path.isdir(d):
        for f in os.listdir(d):
            y = os.path.join(d, f)
            if f.endswith(".log") and os.path.isfile(y) and os.path.getsize(y) > LOG_MAX:
                islemler.append({"tur": "kirp", "yol": y,
                                 "bayt": os.path.getsize(y) - LOG_KALAN, "neden": "buyuk_log"})
    return islemler


# ------------------------------------------------------------------ uygulama
def _kayit_yonlendir(eski, yeni):
    """kayit.json'da eski paketi gosteren id'ler -> yeni pakete. Doner: id listesi."""
    kayit = hkp._kayit_oku()
    eski = os.path.abspath(eski)
    idler = []
    for kid, v in kayit.items():
        if isinstance(v, dict) and os.path.abspath(os.path.expanduser(str(v.get("yol")))) == eski:
            idler.append(kid)
            if yeni:
                v["yol"] = os.path.abspath(yeni)
                v["yonlendi"] = time.strftime("%Y-%m-%d")
    if not yeni:
        for kid in idler:
            kayit.pop(kid, None)
    if idler:
        hkp._kayit_yaz(kayit)
    return idler


def uygula(islemler=None, simdi=None):
    """Plani uygular: tasi (cope) / kirp; copu bosaltir; kayit ve gunlugu budar."""
    simdi = simdi or time.time()
    islemler = plan(simdi) if islemler is None else islemler
    damga = time.strftime("%Y%m%d-%H%M%S", time.localtime(simdi))
    hedef = os.path.join(cop_dizin(), damga)
    geri = []
    rapor = {"tasinan": 0, "kirpilan": 0, "kazanc_bayt": 0, "neden": {}}
    for x in islemler:
        if not os.path.exists(x["yol"]):
            continue
        os.makedirs(hedef, exist_ok=True)
        cop = os.path.join(hedef, "%03d-%s" % (len(geri), os.path.basename(x["yol"])))
        if x["tur"] == "kirp":
            shutil.copy2(x["yol"], cop)
            with open(x["yol"], "rb") as f:
                f.seek(-LOG_KALAN, os.SEEK_END)
                kuyruk = f.read()
            with open(x["yol"], "wb") as f:
                f.write(kuyruk)
            rapor["kirpilan"] += 1
            geri.append({"kaynak": x["yol"], "cop": cop, "tur": "kirp"})
        else:
            shutil.move(x["yol"], cop)
            idler = _kayit_yonlendir(x["yol"], x.get("yonlendir")) if x["yol"].endswith(".hkp") else []
            rapor["tasinan"] += 1
            geri.append({"kaynak": x["yol"], "cop": cop, "tur": "tasi", "kid": idler,
                         "yonlendir": x.get("yonlendir")})
        rapor["kazanc_bayt"] += x.get("bayt", 0)
        rapor["neden"][x["neden"]] = rapor["neden"].get(x["neden"], 0) + 1
    if geri:
        with open(os.path.join(hedef, "geri.json"), "w", encoding="utf-8") as f:
            json.dump(geri, f, ensure_ascii=False, indent=1)
    rapor["cop"] = hedef if geri else None
    rapor["bosaltilan"] = cop_bosalt(simdi)
    rapor["kayit_budanan"] = _kayit_buda()
    rapor["gunluk_budanan"] = _gunluk_buda()
    rapor["hook_durum_silinen"] = _hook_durum_buda(simdi)
    try:
        import depo
        if os.path.exists(depo.yol()):
            depo.guncelle(butce_sn=15)  # tasinan paketleri indeksten dusurur
    except Exception:  # noqa: BLE001
        pass
    os.makedirs(_dizin(), exist_ok=True)
    with open(durum_yolu(), "w", encoding="utf-8") as f:
        json.dump({"son": simdi, "rapor": rapor}, f, ensure_ascii=False, indent=1)
    return rapor


def cop_bosalt(simdi=None):
    """`cop_gun` gunden eski cop klasorlerini GERCEKTEN siler. Doner: klasor sayisi."""
    simdi = simdi or time.time()
    gun = float(_ayar().get("cop_gun", 30) or 30)
    d = cop_dizin()
    if not os.path.isdir(d):
        return 0
    n = 0
    for f in os.listdir(d):
        y = os.path.join(d, f)
        if os.path.isdir(y) and simdi - os.path.getmtime(y) > gun * GUN:
            shutil.rmtree(y, ignore_errors=True)
            n += 1
    return n


def _kayit_buda():
    """Dosyasi olmayan (ve cop listesinde de olmayan) id kayitlarini siler."""
    kayit = hkp._kayit_oku()
    olu = [k for k, v in kayit.items()
           if isinstance(v, dict) and v.get("yol")
           and not os.path.exists(os.path.expanduser(str(v["yol"])))]
    for k in olu:
        kayit.pop(k)
    if olu:
        hkp._kayit_yaz(kayit)
    return len(olu)


def _gunluk_buda():
    try:
        import hafiza
        y = hafiza.gunluk_yolu()
        if not os.path.exists(y):
            return 0
        with open(y, encoding="utf-8", errors="replace") as f:
            satirlar = f.readlines()
        fazla = len(satirlar) - hafiza.GUNLUK_MAX
        if fazla <= 0:
            return 0
        with open(y + ".tmp", "w", encoding="utf-8") as f:
            f.writelines(satirlar[fazla:])
        os.replace(y + ".tmp", y)
        return fazla
    except Exception:  # noqa: BLE001
        return 0


def _hook_durum_buda(simdi):
    try:
        import claude_hook
        d = claude_hook.durum_dizini()
    except Exception:  # noqa: BLE001
        return 0
    if not os.path.isdir(d):
        return 0
    n = 0
    for f in os.listdir(d):
        y = os.path.join(d, f)
        if f.endswith(".json") and simdi - os.path.getmtime(y) > 30 * GUN:
            os.remove(y)
            n += 1
    return n


def geri_al(damga=None):
    """Cop klasorunu (varsayilan: en yenisi) geri yukler. Doner: geri gelen sayisi."""
    d = cop_dizin()
    if not os.path.isdir(d):
        raise ValueError("cop bos")
    klasorler = sorted(f for f in os.listdir(d) if os.path.isdir(os.path.join(d, f)))
    if not klasorler:
        raise ValueError("cop bos")
    damga = damga or klasorler[-1]
    yol = os.path.join(d, damga, "geri.json")
    with open(yol, encoding="utf-8") as f:
        liste = json.load(f)
    kayit = hkp._kayit_oku()
    n = 0
    for x in liste:
        if not os.path.exists(x["cop"]):
            continue
        if x["tur"] == "kirp":
            shutil.copy2(x["cop"], x["kaynak"])
        else:
            if os.path.exists(x["kaynak"]):
                continue  # ustune yazma
            os.makedirs(os.path.dirname(x["kaynak"]), exist_ok=True)
            shutil.move(x["cop"], x["kaynak"])
            for kid in x.get("kid") or []:
                kayit.setdefault(kid, {})["yol"] = x["kaynak"]
                kayit[kid].pop("yonlendi", None)
        n += 1
    hkp._kayit_yaz(kayit)
    shutil.rmtree(os.path.join(d, damga), ignore_errors=True)
    return n


def ozet(islemler):
    """Plan/rapor icin insan-okur tek paragraf."""
    say, bayt = {}, 0
    for x in islemler:
        say[x["neden"]] = say.get(x["neden"], 0) + 1
        bayt += x.get("bayt", 0)
    if not islemler:
        return "temiz — tasinacak yigin yok"
    return "%d oge, ~%.1f MB: %s" % (len(islemler), bayt / 1e6,
                                     ", ".join("%s=%d" % kv for kv in sorted(say.items())))
