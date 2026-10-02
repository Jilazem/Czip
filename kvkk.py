#!/usr/bin/env python3
"""czip KVKK katmani — paket icindeki kisisel verileri sifreler.

Kapsam (Gokhan karari 25.09.2026): TC kimlik no, ad-soyad, adres, tapu malik
bilgileri. Yerel model 8888'e gidilir (KVKK acisindan sorun degil).

Calisma sekli:
  - TC ve adres kaliplari regex ile KESIN bulunur (LLM'ye gerek yok).
  - Ad-soyad/malik LLM'e (node1:8888, tek kaynak = /v1/models) sorulur.
  - Her bulunan alan paket icinde {KVKK:<tur>:<kod>} tokenina doner.
  - Orijinal degerler yalniz <paket>.sifre yan dosyasinda, Fernet ile sifreli.
    Anahtar: ~/.hermes/czip/kvkk.key (0600). Anahtar + yan dosya ayr ayr tutulur.
  - LLM erisilemezse yalniz regex bolumu uygulanir ve net uyarilir (sessiz eksik yok).

Kullanim (hkp.py paketle komutundan --kvkk bayragiyla cagrilir):
    yeni, rapor = uygula(mesajlar, yol)
"""
import base64
import json
import os
import re
import secrets
import urllib.request

ANAHTAR_YOL = os.path.expanduser("~/.hermes/czip/kvkk.key")
AYAR_YOL = os.path.expanduser("~/.hermes/czip/kvkk_ayar.json")
LLM_URL = "http://127.0.0.1:8888/v1/chat/completions"
MODELLER_URL = "http://127.0.0.1:8888/v1/models"
TUR_Short = {"tc": "TC", "ad-soyad": "AD", "adres": "ADR", "malik": "MLK"}

# --- regex kesisler (kesin, LLM'siz) -------------------------------------
TC_RE = re.compile(r"(?<!\d)[1-9]\d{8}(?!\d)")
ADRES_RE = re.compile(
    r"[\w\sçğıöşüÇĞİÖŞÜ./-]{6,80}"
    r"(?:Mah(?:alle)?s?(?:i|\.)|Cad(?:de)?s?(?:i|\.)|Sok(?:ak)?s?(?:ı|\.)|Bulvar(?:ı|\.)"
    r"|Çıkmaz(?:ı|\.)|Küme Evleri)"
    r"[\w\sçğıöşüÇĞİÖŞÜ.,/No:()-]{0,80}")
MALIK_ETIKET_RE = re.compile(r"(?:Malik|Tasarruf sahibi|Ad[a-]?soyad[ıi]|Vatandas)[:\s]+[^\n]{3,60}")


def anahtar_yukle():
    """Fernet anahtari; yoksa uretir (0600)."""
    from cryptography.fernet import Fernet
    os.makedirs(os.path.dirname(ANAHTAR_YOL), exist_ok=True)
    if not os.path.exists(ANAHTAR_YOL):
        k = Fernet.generate_key()
        fd = os.open(ANAHTAR_YOL, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(k)
    with open(ANAHTAR_YOL, "rb") as f:
        return Fernet(f.read())


def _ayar_yukle():
    try:
        with open(AYAR_YOL, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _ayar_yazla(politika):
    fd = os.open(AYAR_YOL, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"izin": politika}, f, ensure_ascii=False)


def hizli_tara(metinler):
    """Onay oncesi anlik kesin tarama (yalniz regex — LLM beklemez)."""
    n_tc = n_adr = n_mlk = 0
    for m in metinler:
        n_tc += len(TC_RE.findall(m))
        n_adr += len(ADRES_RE.findall(m))
        n_mlk += len(MALIK_ETIKET_RE.findall(m))
    return {"tc": n_tc, "adres": n_adr, "malik": n_mlk}


def izin_ver(hedef, metinler, stdin=None):
    """Disa gidecek veride KVKK alani bulunduysa KULLANICIYA sorar (4 secenek).

    Doner True (gonder, sifrele) / False (gonderme). Politika 'hep'->sormadan
    gonder, 'hic'->sormadan reddet. TTY yoksa ve politika yoksa GUVENLI varsayilan
    RED (sessiz bulut asla).
    """
    a = _ayar_yukle().get("izin")
    if a == "hep":
        return True
    if a == "hic":
        return False
    v = hizli_tara(metinler)
    toplam = sum(v.values())
    if toplam == 0:
        return True  # sifrelenecek KVKK alani yok — soru gereksiz
    stdin = stdin or __import__("sys").stdin
    if not stdin.isatty():
        print("KVKK KAPISI: %d kisisel alan (TC:%d adres:%d malik:%d) — onay sorulamadi (etkilesimsiz), GONDERILMEDI."
              % (toplam, v["tc"], v["adres"], v["malik"]))
        return False
    print("\nKVKK ONAYI — su veri DIS BAGLANTIYA (%s) gidecek, %d kisisel alan bulundu (TC:%d adres:%d malik:%d)."
          % (hedef, toplam, v["tc"], v["adres"], v["malik"]))
    print("1) bu sefer izin ver (sifrele ve gonder)")
    print("2) onayla (detaylari goster, sonra karar ver)")
    print("3) hep izin ver (bundan sonra sormadan sifrele+gonder)")
    print("4) bu sefer izin verme (gonderme)")
    print("5) hic izin verme (kalici reddet)")
    sec = input("Secim [1-5, varsayilan 4]: ").strip() or "4"
    if sec == "1":
        return True
    if sec == "2":
        for i, m in enumerate(metinler):
            for a in TC_RE.findall(m) + ADRES_RE.findall(m):
                print("  %d. alan: %s" % (i, a[:70]))
        return input("Gonderilsin mi? (e/h, varsayilan h): ").strip().lower() == "e"
    if sec == "3":
        _ayar_yazla("hep")
        return True
    if sec == "5":
        _ayar_yazla("hic")
        return False
    return False


def _model_adi():
    """TEK KAYNAK: 8888 /v1/models — sabit model adi YOK.

    model_esitle.kaynak_ad() ile ayni kazanani sec (root/onek kurali):
    8888 birden cok isim serve edebilir ve [0] yanlis modellere (14B arac
    cagrisini reddeder) denk gelebilir.
    """
    import importlib.util
    yol = os.path.expanduser("~/007-HERMES/03-SCRIPTS/model_esitle.py")
    try:
        spec = importlib.util.spec_from_file_location("model_esitle", yol)
        me = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(me)
        return me.kaynak_ad()
    except SystemExit:
        return None
    except Exception:
        return None


def llm_kisisel(metinler):
    """Metin listesinden kisisel alanları çıkarır: [(metin_alt, tur), ...].

    LLM = node1:8888 (yerel). JSON dizisi ister; bozuk cevaplada bos doner.
    """
    model = _model_adi()
    if not model:
        raise RuntimeError("8888 erisilemedi")
    bulgu = []
    for i, metin in enumerate(metinler):
        parca = metin[:12000]
        if not parca.strip():
            continue
        soru = ("Metindeki KISISEL verileri cikar. Donus: yalniz JSON dizisi, "
                'her eleman {"v": "<birebir ayni metin>", "t": "ad-soyad|adres|malik"}.\n'
                "Kurallar: kurum adlarini (mahkeme, bakanlik, banka adi) cikarma; "
                "parsel/ada/mahalle adi tek basina degil, tam adres bütünü olarak alin.\n"
                f"---\n{parca}\n---")
        govde = json.dumps({
            "model": model, "temperature": 0.0, "max_tokens": 900,
            "chat_template_kwargs": {"enable_thinking": False},  # thinking 762 token/11sn bos yapiyordu; 1.3sn
            "messages": [{"role": "user", "content": soru}]},
            ensure_ascii=False).encode("utf-8")
        istek = urllib.request.Request(LLM_URL, data=govde,
                                       headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(istek, timeout=90) as r:
                msg = json.load(r)["choices"][0]["message"]
                # thinking modellerde content None olabilir; reasoning_content'e dus
                yanit = str(msg.get("content") or msg.get("reasoning_content") or "")
        except Exception:
            raise RuntimeError("LLM erisilemedi")
        i0, i1 = yanit.find("["), yanit.rfind("]")
        if i0 < 0 or i1 < 0:
            continue
        try:
            for b in json.loads(yanit[i0:i1 + 1]):
                v, t = str(b.get("v", "")).strip(), str(b.get("t", "")).strip()
                if v and t in TUR_Short and v in parca:
                    bulgu.append((v, t))
        except Exception:
            continue
    return bulgu


def _token(tur, deger):
    # ayni deger -> ayni token (paket ici tutarlilik)
    return "{" + "KVKK:%s:%s}" % (TUR_Short.get(tur, "?"), secrets.token_hex(2))


def uygula(mesajlar, paket_yol):
    """Mesaj listesindeki kisisel verileri tokenlar; sifreli haritayi yan dosyaya
    yazar. Doner: (yeni_mesajlar, rapor_dict).

    ponytail: mesaj bazli sirali LLM cagrisi (N×90sn tavan). Yuzlerce iletilik
    paketlerde hiz issizligi olursa mesajlari tek tolu sorguda birlestir.
    """
    fernet = anahtar_yukle()
    # 1) tum string icerikleri topla (mesaj govdesi + arac argumanlari)
    duz_metinler = []
    for m in mesajlar:
        _duz_topla(m, duz_metinler)

    # 2) kesis: regex
    bulunanlar = []  # [(deger, tur)]
    for metin in duz_metinler:
        for a in TC_RE.findall(metin):
            bulunanlar.append((a, "tc"))
        for a in ADRES_RE.findall(metin):
            bulunanlar.append((a.strip(), "adres"))
        for a in MALIK_ETIKET_RE.findall(metin):
            bulunanlar.append((a.strip(), "malik"))

    # 3) LLM kisisel alan (ad-soyad agirlikli) — yerel 8888
    llm_durum = "tam"
    try:
        bulunanlar += llm_kisisel(duz_metinler)
    except Exception as e:
        llm_durum = "YOK (%s) — yalniz regex uygulandi" % str(e)[:60]

    # 4) birebir degerleri tokenla (uzun -> kisa sirali, ic ice yerlesmesin)
    token_harita = {}
    for deger, tur in sorted(set(bulunanlar), key=lambda x: -len(x[0])):
        if deger in token_harita:
            continue
        token_harita[deger] = _token(tur, deger)

    if token_harita:
        mesajlar = json.loads(json.dumps(mesajlar, ensure_ascii=False))  # derin kopya
        _tokenla(mesajlar, token_harita)
        yan = paket_yol + ".sifre"
        os.makedirs(os.path.dirname(yan), exist_ok=True)
        with open(yan, "wb") as f:
            f.write(fernet.encrypt(json.dumps(token_harita, ensure_ascii=False).encode("utf-8")))
        os.chmod(yan, 0o600)

    # 5) kontrol: TC sizinti denetimi — tokenlanmis mesajlarda ham TC kalmamali
    kontrol_kasa = []
    _duz_topla(mesajlar, kontrol_kasa)
    kalan_tc = sum(len(TC_RE.findall(metin)) for metin in kontrol_kasa)

    sayilar = {}
    for d, t in token_harita.items():
        tur = t.split(":")[1]  # {KVKK:<tur>:<hex>}
        sayilar[tur] = sayilar.get(tur, 0) + 1
    return mesajlar, {"sifrelenen": len(token_harita), "dagilim": sayilar,
                      "llm": llm_durum, "kalan_tc": kalan_tc}


def _duz_topla(nesne, kasa):
    """Yapistir: icerideki her string'i kasaya ekler (arac argumanlari dahil)."""
    if isinstance(nesne, str):
        kasa.append(nesne)
    elif isinstance(nesne, dict):
        for v in nesne.values():
            _duz_topla(v, kasa)
    elif isinstance(nesne, (list, tuple)):
        for v in nesne:
            _duz_topla(v, kasa)
    return kasa


def _tokenla(nesne, harita):
    """Sozluk/liste agacindaki her string'te deger->token yer degistirmesi."""
    if isinstance(nesne, dict):
        for k, v in nesne.items():
            if isinstance(v, str):
                for d, t in harita.items():
                    if d in v:
                        v = v.replace(d, t)
                nesne[k] = v
            else:
                _tokenla(v, harita)
    elif isinstance(nesne, list):
        for i, v in enumerate(nesne):
            if isinstance(v, str):
                for d, t in harita.items():
                    if d in v:
                        v = v.replace(d, t)
                nesne[i] = v
            else:
                _tokenla(v, harita)


def metin_gecir(nesne, etiket="czip"):
    """Herhangi yapidaki metinlerdeki KVK veriyi tokenlastirir (uygula'ya delegasyon).

    Doner: (yeni_nesne, {}). Harita DISARI DONMEZ — uygula onu dis_kayitlari
    altina sifreli yan dosyaya yazar (hkp sargisi 2. alani kullanmaz).
    """
    yol = os.path.join(os.path.dirname(ANAHTAR_YOL), "dis_kayitlari",
                       re.sub(r"[^A-Za-z0-9_-]", "-", (etiket or "czip"))[:40])
    yeni, _rapor = uygula([nesne], yol)
    return yeni[0], {}


def dis_kapisi(govde, etiket="", ilet_sayisi=0, tahmini_bayt=0):
    """DIŞ BAGLANTI tek kapisi (hkp._jev_batch cagrir).

    Doner: None -> gidiş engellendi (cagiran HTTP'ye gecmemeli).
           dict -> gidecek govde (izin varsa KVK veriler token'a cevrilmis,
           harita dis_kayitlari'nda sifreli, yerde kalir).
    Onay 5 secenek: bu sefer / detay-goster / hep / bu-sefer-ret / hic (izin_ver).
    """
    kasa = []
    _duz_topla(govde, kasa)
    if not izin_ver(etiket or "czip", kasa):
        return None
    yeni, _ = metin_gecir(govde, etiket=etiket or "czip")
    return yeni


def coz(paket_yol):
    """Yan dosyayi cozup {token: orijinal} haritasini doner."""
    fernet = anahtar_yukle()
    with open(paket_yol + ".sifre", "rb") as f:
        return json.loads(fernet.decrypt(f.read()).decode("utf-8"))


if __name__ == "__main__":
    # tek kontrol: regex + sifre/gizli-donus turu + token
    assert len(TC_RE.findall("TC: 123456789, tel 05551234567")) == 1
    assert ADRES_RE.search("Cumhuriyet Mah. Inonu Cad. No:4")
    msgs = [{"role": "user", "content": "TC 123456789 — CumhurYetis M. Inonu Cad."}]
    yeni, rap = uygula(msgs, "/tmp/kvkk-test.hkp")
    assert "123456789" not in yeni[0]["content"] and rap["kalan_tc"] == 0
    harita = coz("/tmp/kvkk-test.hkp")
    assert "123456789" in harita and harita["123456789"].startswith("{KVKK:TC:")
    print("OK:", rap)
