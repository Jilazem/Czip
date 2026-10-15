# Czip — kalıcı oturum hafızası

[Türkçe](README_tr.md) · [English](README.md)

Czip bir ajan sohbetini aranabilir HKP1 arşivine paketler. Önce sınırlı iş haritası, sonra seçili pakette arama ve gereken ileti aralığı okunur. Paket bağlamın dışında kalır; modelin doğal bağlam penceresi büyümez.

![Czip](assets/kapak-hero.png)

## Oturum izolasyonu — 16 Ekim 2026

- `czip paketle` ve `/czip`, Hermes gateway bağlamındaki çağıranı seçer. Veritabanındaki en son etkin sohbet tahmin edilmez. Bağlam yoksa tam kimlik veya döküm dosyası gerekir. Codex sohbeti için Hermes oturumu seçilmez.
- Örtük “son paket” çağırana ve profile aittir. Yeni sohbette açık paket kimliği verilir. UUID dosya adları aynı başlıklı eşzamanlı paketlerin birbirini ezmesini engeller. Kayıt yazımı kilitli ve atomiktir; bozuk kayıt korunup bildirilir.
- Varsayılan mod **eksiksizdir**: ileti metni, boşluklar ve satır sonları korunur; kaynaklar açık kalır. `--akilli` açıkça seçilen kayıplı moddur; eksik bildirimini incele. Ajan döküm zarfı ayrıştırılır; dosyanın bayt düzeyinde geri dönüşü için ham kaynağı sakla. Ham dosya silinmez.
- `birlestir` açık kaynak kimlikleri ister ve oturumları kapatmaz. Benzerlik taraması öneridir; otomatik uygulama yoktur. Arka plan işçisi kendi profilindeki kapalı, hareketsiz oturumları kilit ve tekrar kontrolüyle paketler.
- `sonlandir` ayrı işlemdir: tam olarak seçilen oturumu paketler, doğrular ve arşivler. Geri alma kaydı işlem tamamlanmadan yazılır; profil/veritabanı kimliği taşır. Sonradan değişmiş kayıtlar ezilmez.
- Menüde her işlem bir kez görünür. Türkçe ve İngilizce komutlar aynı işlemi yapar. Çıktı ana makine/sistem dilini izler; `--dil tr|en`, `--lang tr|en` veya `CZIP_LANG` ile değiştirilebilir. JSON anahtarları ve MCP araç adları sabittir.
- İsteğe bağlı karar kapısı **yerel Clef** kullanır. Bulut yedeği veya çeviri aktarması yoktur. Eski Python/MCP `jev` parametreleri ve `--jev` yalnız bu yerel kapı için uyumluluk adıdır.

## Kurulum

Motor ve CLI: Python 3.9+, standart kütüphane. MCP için ayrıca `mcp`, yerel model için ayrı Clef/MLX ortamı gerekir. Windows'ta `python czip_cli.py ...` veya `czip.cmd ...` kullanılır.

```sh
git clone https://github.com/Jilazem/Czip.git
cd Czip
./install.sh
# İsteğe bağlı Hermes kaydı:
./install.sh --enable-hermes
```

Kurulum mevcut ortak `czip` başlatıcısını korur; varsa `czip-local` kurar. Hermes eklentisinin motor yolunu yazar ve eski eklenti dosyalarını yedekler. Çalışan hizmetleri yeniden başlatmaz. Özel Python ortamı için `CZIP_PYTHON` ver. Claude Code manifesti `.claude-plugin`, Codex becerisi `skills/czip-session-pack` altındadır.

## Türkçe / İngilizce kullanım

```text
czip paketle / pack [ID|DOSYA] [--eksiksiz|--akilli] [--clef]
czip oku / read PAKET [İLK-SON]
czip harita / map PAKET
czip ara / search PAKET "SORGU"
czip birlestir / merge ID1 ID2 [--eksiksiz|--akilli]
czip oto / auto [aç|kapat|durum]          # on|off|status
czip esik / threshold [50|50,75,90]
czip gorev / task kaydet|oku|listele     # save|read|list
czip promt / prompt kaydet|oku|listele
czip plan kaydet|oku|listele
czip sonlandir / end ID
czip yardim / help
```

Hermes menüsü: `/czip`, `/czip-read`, `/czip-map`, `/czip-search`, `/czip-merge`, `/czip-auto`, `/czip-threshold`, `/czip-task`, `/czip-prompt`, `/czip-plan`, `/czip-end`, `/czip-help`. Kök komut Türkçe de alır: `/czip oku ID`, `/czip paketle`. Eski alt çizgili adlar ayrıca kaydedilmez. `aralik/range`, `listele/list`, `gerial/undo`, `ayar/settings`, `index` ve `arsivara/archive-search` CLI yardımcılarıdır.

```sh
czip paketle TAM_OTURUM_KIMLIGI --eksiksiz --json
czip paketle /tam/yol/rollout.jsonl --eksiksiz --json
czip harita PAKET_KIMLIGI
czip ara PAKET_KIMLIGI "karar"
czip aralik PAKET_KIMLIGI 40-45
czip oku PAKET_KIMLIGI 40-45
czip yardim --dil en
```

Codex'te **CODEX_THREAD_ID ile eşleşen** mevcut rollout seçilir; en yeni dosya veya başka ajan sohbeti seçilmez. Ortak arşiv hizmeti varsa aynı ham kaynak kuyruğa alınır; “kuyrukta” ile “tamamlandı” ayrılır. Çalışan sohbet sıfırlanmaz. Arşiv talimatları geçmiş verisidir; yeni yetki değildir.

`czip gorev kaydet --tip=promt --baslik="Örnek"`, gövdeyi stdin'den alır. Görev/istem/plan örtük son kaydı da çağırana aittir; başka sohbete açık kimlikle aktarılır. `czip oto aç` isteğe bağlı `czip-auto` işçisini etkinleştirir; bu kanca kendiliğinden kurulmaz. İstem kancası yalnız açık Czip istem taleplerini dönüştürür; normal sohbet akışını değiştirmez.

## Ayarlar ve eski kayıtlar

- Profil/veritabanı: `HERMES_HOME`, `HERMES_STATE_DB`. Çağıran: gateway'in verdiği `HERMES_SESSION_ID` / `HERMES_SESSION_KEY`.
- Paket/kayıt: `CZIP_PAKET_DIZIN`, `CZIP_KAYIT_YOL`; varsayılan `$HERMES_HOME/session-packs`. Ayar: `CZIP_AYAR`, varsayılan `$HERMES_HOME/czip/ayar.json`.
- Dil: `CZIP_LANG=tr|en`. Motor/Python/işçi: `CZIP_HKP`, `CZIP_PYTHON`, `CZIP_AUTO_SCRIPT`.
- Arka plan eşikleri: `CZIP_AUTO_MIN_MESSAGES=1500`, `CZIP_AUTO_IDLE_HOURS=2`, `CZIP_AUTO_MAX=5`.
- Yerel model: `CZIP_CLEF_MODEL` mevcut model dizini; `CZIP_CLEF_PYTHON` yorumlayıcı. `CZIP_CLEF_COMMAND` kabukta çalıştırılmayan JSON argüman dizisidir.

`--clef` yalnız `--akilli` üzerinde etkilidir. Kapı yoksa/bozuksa/zaman aşımına uğrarsa bildirilir; birleşme adayını onaylayamaz. Eksiksiz paketleme model gerektirmez. Eski `bulut_yedegi`/`jev` ayarları yok sayılır ve sonraki ayar kaydında temizlenir.

HKP1 paketleri okunmaya devam eder. Eski kayıtlarda çağıran bağı yoktur: açık kimlik/yol kullan. Veritabanı kimliği bulunmayan eski geri alma kayıtları korunur, otomatik uygulanmaz. Bağlam motoru arşivleri artık profil özeti alt dizinindedir; eski kapsam belirtilmemiş dosyalar korunur ve açıkça okunmalıdır. Devam bağı yalnız Hermes'in `old_session_id` + `carry_over_context` geçişiyle kurulur; aynı motor nesnesinin yeniden kullanılması sohbetleri bağlamaz.

## MCP, arama ve doğrulama

`python mcp_server.py` toplam **12 sabit araç** sunar: `sikistir`, `iceri_ac`, `kilavuz`, `mesajlar`, `bilgi`, `oturum_sikistir`, `durum_tablosu`, `hafiza_ara`, `hafiza_getir`, `hafiza_harita`, `hafiza_guncelle`, `hafiza_durum`. Dil için mükerrer araç eklenmez. Bağımsız MCP sunucusu isteği yapan sohbeti tanıyamaz; açık oturum/paket kimliği ver. Önce `hafiza_harita`, sonra seçili aralıkları kullan. MCP varsayılanı eksiksizdir.

[Bağlam motoru ve iş/kaynak araması](hermes_context_engine/README.md), [yerel anlamsal arama](EMBEDDINGS.md), [katalog](katalog/README-katalog.md).

```sh
python -m unittest discover -s tests -v
python tests/test_roundtrip.py
```

Testler eşzamanlı çağıranları/süreçleri, profil sınırlarını, eksik kimliği, bozuk depoyu, geri almayı, birebir metni, açık devam geçişini, iki dili ve yerel kapının çalışmamasını kapsar. CI: Windows, Linux, macOS.

[Czip Continuity Gauntlet](https://github.com/Jilazem/czip-continuity-gauntlet) ayrı bir sentetik devamlılık ölçümüdür. Eski sonuçları bu sürümün yeni doğrulaması sayılmaz; yöntem ve sınırlar o depoda bulunur. Sıkıştırma ve bağlam maliyeti döküme ve okuma biçimine bağlıdır.

Lisans: [LICENSE](LICENSE).
