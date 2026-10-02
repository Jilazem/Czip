#!/usr/bin/env python3
# czip_auto — uzun + hareketsiz oturumlari otomatik paketler (cron dostu, sessiz).
# Kurallar: en az ESIG ileti, en az DINLEN_SAT saat hareketsiz, aktif paketinde
# henuz yok, session_id ile oturum henuz acik degil (ended_at dolu).
import json
import os
import sqlite3
import subprocess
import time

STATE = os.path.expanduser("~/007-HERMES/20-HERMES/state.db")
CZIP = os.path.expanduser("~/.local/bin/czip")
KAYIT = os.path.expanduser("~/007-HERMES/05-CIKTILAR/oturum-paketleri/kayit.json")
STATE_DEDUP = os.path.expanduser("~/007-HERMES/05-CIKTILAR/oturum-paketleri/auto-dedup.json")
MERGE_DEDUP = os.path.expanduser("~/007-HERMES/05-CIKTILAR/oturum-paketleri/auto-merge.json")
KAYNAK = os.path.expanduser("~/007-HERMES/20-HERMES/czip-source")
LOG = os.path.expanduser("~/007-HERMES/05-CIKTILAR/oturum-paketleri/_auto.log")
ESIG = 1500            # ileti esigi
DINLEN_SAT = 2         # son aktiviteden en az 2 saat sonra paketle
MAKS = 5               # tur basi en fazla paketleme (kotugiden korur)

def logla(metin):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + metin + "\n")

def paketli_session_idleri():
    """kayit.json uzerinden hangi session_id'ler zaten paketlendi."""
    try:
        with open(KAYIT, encoding="utf-8") as f:
            kayit = json.load(f)
    except Exception:
        return set()
    return {v.get("sid") for v in kayit.values() if v.get("sid")}

def hedefleri_bul():
    con = sqlite3.connect("file:" + STATE + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, title, message_count, last_activity_at, ended_at "
        "FROM sessions WHERE archived=0 AND hidden=0 "
        "AND message_count >= ? AND ended_at IS NOT NULL "
        "AND (last_activity_at IS NULL OR last_activity_at <= ?) "
        "ORDER BY message_count DESC LIMIT 40",
        (ESIG, time.time() - DINLEN_SAT * 3600)).fetchall()
    con.close()
    return rows

def main():
    sec = int(os.environ.get("CZIP_AUTO_MAX", "10"))
    try:
        with open(STATE_DEDUP, encoding="utf-8") as f:
            yapilmis = json.load(f)
    except Exception:
        yapilmis = {}
    hedefler = [r for r in hedefleri_bul() if r["id"] not in yapilmis][:sec]
    ozet = []
    for r in hedefler:
        sid = r["id"]
        try:
            p = subprocess.run([CZIP, "paketle", sid], capture_output=True,
                               text=True, timeout=900)
            cikti = (p.stdout or "") + (p.stderr or "")
            kid = ""
            yol = ""
            for satir in cikti.splitlines():
                s = satir.strip()
                if s.startswith("PACKED:"):
                    yol = s.split("PACKED:", 1)[1].strip()
                if s.startswith("ID="):
                    kid = s.split("ID=", 1)[1].strip()
            if kid and yol:
                yapilmis[sid] = {"kid": kid, "yol": yol, "ileti": r["message_count"],
                                 "t": time.strftime("%Y-%m-%d %H:%M")}
                with open(STATE_DEDUP, "w", encoding="utf-8") as f:
                    json.dump(yapilmis, f, ensure_ascii=False, indent=1)
                ozet.append(f"{r['title'] or sid[:16]} -> {kid} ({r['message_count']} ile)")
                logla(f"OK {sid[:24]} -> {kid}")
            else:
                logla(f"FAIL {sid[:24]} rc={p.returncode} {cikti[-200:]}")
        except Exception as e:
            logla(f"HATA {sid[:24]} {e}")
    if ozet:
        # cron --no-agent deseni: yalniz gercek is varsa konus, bos stdout = sessiz.
        print(f"auto: {len(ozet)} paket")
        for s in ozet:
            print(" -", s)

    # --- 2) BIRLESTIRME BEKCI (jev'siz, yerel esik) ---
    # Ayni isin tekrar eden oturumlari tek pakete indirir. Karar yerel
    # Jaccard 0.45 (birlestir.YEREL_BIRLESTIR) — supheliyi birlestirmez.
    # Kararsizlik bolgesi (ADAY_TABAN..esik) KULLANICIYA SORULUR, kendiligimiz
    # birlestirmeyiz. Sadece kapali + 2 saat hareketsiz oturumlar aday;
    # aktif/aktif-yazilan oturuma asla dokunulmaz.
    try:
        import sys as _sys
        _sys.path.insert(0, KAYNAK)
        import birlestir

        try:
            with open(MERGE_DEDUP, encoding="utf-8") as f:
                mdedup = json.load(f)
        except Exception:
            mdedup = {"islenmis": {}, "sorulan": {}}

        # uygun olanlar: arsivlenmemis/gizli degil + en az 48 saat hareketsiz.
        # Acik (ended_at NULL) oturum da aday OLABILIR — 2 gun boyunca tek satir
        # yazilmadiysa ölü sayilir; canli oturuma dokunma garantisi 48s esigidir.
        con = sqlite3.connect("file:" + STATE + "?mode=ro", uri=True)
        uygun = {r[0] for r in con.execute(
            "SELECT id FROM sessions WHERE COALESCE(archived,0)=0 AND hidden=0 "
            "AND (last_activity_at IS NULL OR last_activity_at <= ?)",
            (time.time() - 48 * 3600,))}
        con.close()

        _, ciftler = birlestir.adaylari_bul(gun=7)
        gruplar, iz = birlestir.gruplari_kur(ciftler, jev=False)

        sorular = []
        for k in iz["kararlar"]:
            if k["kabul"]:
                continue
            cift = k["a"] + "|" + k["b"]
            son = mdedup["sorulan"].get(cift, 0)
            if time.time() - son < 3 * 86400:   # ayni cifti 3 gunde bir sor
                continue
            mdedup["sorulan"][cift] = time.time()
            sorular.append("  {a} <-> {b}  skor={skor}  |  {a_ozet}  //  {b_ozet}".format(**k))
        if sorular:
            print("SOR (kararsiz ayni-is adaylari — birlestirmek icin onay bekliyor):")
            for s in sorular[:5]:
                print(s)
            with open(MERGE_DEDUP, "w", encoding="utf-8") as f:
                json.dump(mdedup, f, ensure_ascii=False, indent=1)

        # onayli gruplari birlestir (en fazla 2 grup/tur)
        birlesik = 0
        for grup in gruplar:
            if birlesik >= 2:
                break
            sids = [s for s in grup if s in uygun and s not in mdedup["islenmis"]]
            if len(sids) < 2:
                continue
            try:
                p = subprocess.run([CZIP, "birlestir"] + sids,
                                   capture_output=True, text=True, timeout=900)
                cikti = (p.stdout or "") + (p.stderr or "")
                yol = kid = ""
                for satir in cikti.splitlines():
                    st = satir.strip()
                    if st.startswith("BIRLESTIRILDI:") or st.startswith("MERGED:"):
                        yol = st.split(":", 1)[1].strip().splitlines()[0].strip()
                    if st.startswith("ID="):
                        kid = st.split("ID=", 1)[1].strip()
                if not kid or not yol:
                    logla("MERGE-FAIL " + " ".join(s[:20] for s in sids) +
                          " " + cikti[-200:])
                    continue
                pr = birlestir.pasife_al(sids, yol, kid, sebep="czip-bekci-oto")
                for s in sids:
                    mdedup["islenmis"][s] = kid
                with open(MERGE_DEDUP, "w", encoding="utf-8") as f:
                    json.dump(mdedup, f, ensure_ascii=False, indent=1)
                birlesik += 1
                print("BIRLESTIRILDI: {} -> {} ({} oturum, pasife alindi; "
                      "geri: czip gerial {})".format(
                          ", ".join(s[:20] for s in sids), kid, len(sids),
                          os.path.basename(pr["gerial"])))
            except Exception as e:
                logla("MERGE-HATA " + " ".join(s[:20] for s in sids) +
                      " " + str(e)[:200])
    except Exception as e:
        logla("MERGE-TARAMA-KAPALI " + str(e)[:200])
    # RAG indeksi tazele (artsal): yeni paketler czip arsivara'da bulunsun ki oturum unutulmasın
    try:
        ix = subprocess.run(["czip", "index"], capture_output=True, text=True, timeout=600)
        if ix.returncode != 0:
            logla("INDEX-TAZELEME-FAIL " + (ix.stdout + ix.stderr)[-200:])
    except Exception as e:
        logla("INDEX-TAZELEME-HATA " + str(e)[:200])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
