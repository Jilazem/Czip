# czip — oturumunu taşı, bağlamını değil
> 🇬🇧 English version (primary): [README.md](README.md)

Uzun bir Hermes sohbetini yeni oturuma taşıdığında tüm geçmiş bağlama
geri döner; 4 MB'lık oturum = 4 MB'lık token faturası.
**czip oturumu AI-okur bir pakete (HKP1) sıkıştırır ve yeni oturum o
paketi asla açmaz** — tek satırlık indeks haritasına bakar, yalnız
ihtiyacı olan mesaj aralığını okur.

```
$ czip paketle son
PAKETLENDI: a37tvc | 1117 ilet | 4,2 MB → 237 KB (18x)
```

![kapak](assets/kapak.png)

## v3 — okuma maliyeti (2026-09-20)

Asıl darboğaz sıkıştırma oranı değilmiş: **paket dosyası context'e hiç
girmiyor**, maliyet yalnızca okuma çıktısı. 3421 iletlik gerçek oturumda
ölçüldü (`o200k_base`):

| Okuma yolu | Token |
|---|---|
| `czip oku` (tam indeks) | 93.417 |
| `czip harita` (RAG haritası) | **1.561** |
| + hedefli `czip ara "<sorgu>"` | +634 |

**60× ucuz.** `harita` tam dökümü değil, iş haritasını verir: rol sayıları,
araç histogramı, kullanıcı istekleri, son iletler ve arama talimatı. Okuyan
AI sonra yalnızca ihtiyacı olan aralığı `ara` → `aralik` ile çeker.

Ölçülen ama **işe yaramayan** iki fikir (denendi, veriyle elendi):

- **Kodek ayarı.** LZMA2 `pb=0 lc=4 dict=256MB` → yalnızca **%1,2** kazanç.
  bz2 %27 daha kötü. Mevcut `preset=9|EXTREME` zaten sınırda.
- **Dil değiştirme.** Aynı talimat: Türkçe 39, İngilizce 34, Çince 35 token.
  Üstelik talimat metni toplam maliyetin **%0,1'i** — çevirmek anlamsız.
  Kazanç dilde değil, **JSON töreninde**: son iletleri `A> metin` biçimine
  çevirmek %47 kazandırdı.

## v3 — tekrar ayıklama

Ölçüm: araç çıktıları paketin **%71'i**, ve bunların **%78'i birebir tekrar**
(8.571 çıktı → 1.854 benzersiz). İkinci kopyalar `[AYNI-#N]` işaretine çevrildi.

```
12.217.023 → 479.724 B   (önce 562.416)   oran 21,7x → 25,5x
```

Beş gerçek oturumda: **39,8 MB → 1,44 MB** (15–35x), 2.590 tekrar ayıklandı.

## v3 — birleştirme (`czip birlestir`)

Aynı işi yapan oturumları tespit edip **tek pakete** alır, istenirse
kaynakları pasife alır (kapat + arşivle; ileti silinmez, `czip gerial` ile
geri alınır).

Kararı **karar kapısı** verir ve farkı gerçekten yapar: yerel benzerlik iki
ayrı dava dosyasını 0,75 ile birleştirmeye kalktı, kapı 0,13 verip reddetti;
gerçek kopyayı 0,97 ile onayladı. Zamanlanmış görev kalıpları ayıklanır (41 yanlış
eşleşme → 20).

```
czip birlestir              # salt-okunur aday taraması
czip birlestir oto --laya   # birleştir + kaynakları pasife al
czip gerial <dosya>         # geri al
```

`czip paketle` ayrıca paketledikten sonra "bu işi yapan başka oturum da var"
uyarısı verir (kararı yine karar kapısı).

## v4 — karar kapısı yerele geçti: Jev → Laya

Kapı eskiden **Jev**'di (typesafe.ai System-One, bulut API). Oturum başlığını,
son kullanıcı mesajlarını ve araç çıktısı örneklerini dışarı yolluyordu; bu
yüzden **21.09.2026'da gizlilik gerekçesiyle kapatıldı**. 23.09.2026'dan beri
varsayılan motor **Laya** ([laya-mlx](https://github.com/mizorewww/laya-mlx)):
kendi makinende çalışır, API anahtarı istemez, veri dışarı çıkmaz.

| | Laya (varsayılan) | Jev (eski) |
|---|---|---|
| Nerede çalışır | yerel (`laya_kapi.py` işçisi, model bir kez yüklenir) | bulut API |
| Silme eşiği | **0,15** (korumacı — Laya puanı bağlama duyarlı) | 0,30 |
| Tutma eşiği | 0,55 | 0,55 |
| Gerekenler | laya-mlx venv'i (`CZIP_LAYA_PY`) | `TYPESAFE_API_KEY` |

- Türkçe içerik önce yerel modelle (node1 / Qwen, `LAYA_NODE1_URL`) İngilizceye
  çevrilir — Laya Türkçede zayıf. İçeriği yutan çeviriler (`"..."`) reddedilir
  (24.09.2026 ölçümü: bu hata Laya'nın 0,73 ile tutacağı çıktıları sessizce
  siliyordu). Kapatmak için `CZIP_LAYA_CEVIRI=0`.
- Laya yoksa kapı **atlanır**, statik dilim kullanılır. Jev'e bulut yedeği
  **yalnızca** açarsan olur: `czip ayar bulut on` (varsayılan kapalı, KVKK).
- Motor: `czip ayar motor laya|jev` ya da `CZIP_KARAR_MOTORU`.
- `--laya` kapıyı açar; eski `--jev` bayrağı da çalışır.

## v4 — Claude Code ve Claude Desktop

Claude Code oturumları (Claude Desktop'tan açılanlar dahil) Hermes'in
`state.db`'sinde değil, `~/.claude/projects/<proje>/<oturum>.jsonl`'de durur.
czip artık bunları doğrudan okur:

```
czip cc                     # son Claude Code oturumları
czip paketle cc:son         # en yenisini paketle
czip paketle cc:<uuid>      # belirli bir oturum (önek yeter)
czip birlestir <hermes-id> cc:<uuid>   # Hermes + Claude tek pakette
```

`./install.sh --claude` MCP sunucusunu Claude Desktop'a
(`claude_desktop_config.json`; önce yedek alınır, diğer sunuculara dokunulmaz)
ve Claude Code'a (`claude mcp add`) tanıtır, İngilizce beceriyi kurar.
Yeni MCP araçları: `claude_oturumlari` (liste), `claude_oturum_paketle` (paketle).

## v5 — otopilot: yön ver, koru, hatırla, temizle

czip artık çağrılmayı beklemiyor. Claude Code / Claude Desktop'ta dört hook
onu kendiliğinden çalıştırır (`./install.sh --claude`); söyleyecek bir şeyi
yoksa sessiz kalır:

| Ne zaman | czip ne yapar | Bağlama maliyeti |
|---|---|---|
| **Oturum açılışı** | *Brifing*: bu projede son yapılanlar + son paketin **yön kartı** | ~300–700 token, bir kez |
| **Her istek** | *Koruma*: **gerçek** bağlam boyutunu okur (transcript'teki son `usage`). Kademe geçilince (%50/75/90 ya da `kalan_token`) oturumu kayıpsız paketler ve modele "bağlamı şişirme" der (dosyanın tamamı yerine grep, loglarda head/tail, yeniden okumak yerine `czip ara`). %90'da: `/compact` öner. | eşik altında 0; kademe başına bir kez ~120 token |
| **Her istek** | *Hatırlatma* (RAG): isteği tüm geçmiş paketlerde arar, en fazla 3 tek satırlık isabet ekler — yalnız **alaka kapısından** geçerse (sorgu kelimelerinin en az yarısı aynı iletide; Türkçe harfler katlanır). Aynı oturumda tekrarlamaz, oturumun kendi paketini hatırlatmaz. | alakasızsa 0; ≤ ~250 token |
| **Sıkıştırmadan önce** | Önce tüm oturumu paketler; sıkıştırma sonrası yeni bağlama `[CZIP BAGLANTISI]` + paket ID'si verilir — özetin düşürdüğü hiçbir şey kaybolmaz | ~150 token |

**Yön kartı** (`czip yon <id>`) modele yön veren karar mekanizmasıdır. Her
paketten LLM'siz çıkarılır: hedef, son istek, **bozulmaması gereken kararlar**
("çünkü / yerine / asla …" satırları — yalnız tur sonu yanıtlarından, ara
anlatımdan değil), **açık işler**, son hata, dokunulan dosyalar, **tek bir
sonraki adım** (yanıtsız istek → yarım kalan tur → hata → açık iş) ve
"önce diskte doğrula" kuralı. `--laya` ile yerel Laya kapısı bayat satırları
ayrıca eler.

**Günlük** (`czip gunluk`): her paket tek satır (tarih, proje, ID, sonraki
adım) — "ne yapmıştım?" sorusunun ucuz cevabı. Her paket ayrıca tam metin
deposuna kendiliğinden eklenir.

**Haftalık temizlik** (`czip temizle`): varsayılan yalnız plan; `--uygula`
ya da otopilot (7 günde bir, arka planda) gereksiz yığınları **çöp kutusuna**
taşır, asla doğrudan silmez:

- `yenisi_var` — aynı oturumun eski paketi; kullanıcı/asistan iletilerinin
  ≥ %95'i yeni pakette var. Kısa ID'si yeni pakete **yönlendirilir**, eski
  ID'ler çalışmaya devam eder. Kayıpsız (`--eksiksiz`) paket kırpılmış bir
  paket yüzünden asla gitmez.
- `ayni_icerik` — bayt bayt aynı paketler · `hayalet` — ≤ 2 ileti, < 4 KB,
  7 günden eski · `eski_yedek` — czip dosyalarının 14 günden eski
  `.bak/.yedek` kopyaları (her dosyanın en yeni 2 yedeği kalır) ·
  `buyuk_log` — 2 MB üstü log son 512 KB'a iner.
- Çöp 30 gün sonra boşaltılır; `czip temizle geri` son temizliği geri alır.

Hepsi açılıp kapatılabilir: `czip ayar koruma|hatirlatma|brifing|haftalik_temizlik on|off`,
1M bağlamlı modeller için `czip ayar ctx 1000000`.

MCP sunucusu artık `pip install mcp` istemiyor: paket yoksa aynı 18 aracı
sunan yerleşik saf-stdlib sunucuya geçer (`yon_karti`, `hatirla`,
`hafiza_brifing`, `temizlik` yeni).

## Rakamlar (gerçek oturumlarda, kanıtlı)

| Oturum | Ham | Paket | Oran |
|---|---|---|---|
| Oyun geliştirme (1117 ilet, 31 uzun araç çıktısı) | 4,27 MB | 237 KB | **18,0x** |
| Kod / araştırma (210 ilet) | 997 KB | 119 KB | **8,4x** |
| Yeni oturumun bağlama girişi | 4,27 MB yerine | **~40 KB** indeks + son iletler | |

## Neden "paket açılmaz"?

Geleneksel kompaksiyon ya geçmişin tamamını yeni oturuma yazar (pahalı)
ya da özetler (bilgi kaybı). czip ikisini de yapmaz: **harita + isteğe
bağlı aralık** modeliyle yeni oturum 1117 iletin her birini tek satırda
görür; ayrıntı gereken mesajı numarasından çeker. Sözlük jetonları
(`␟3␞`) normal metne benzemez — çakışma yok, geri dönüş birebir
(round-trip testi: 210/210 ilet bit-bit).

## Kurulum

```bash
git clone https://github.com/Jilazem/Czip && cd Czip
./install.sh
```

Kurulum: `czip` CLI (`~/.local/bin`) + Hermes plugin'i (`/czip`,
`/cunzip` slash komutları) + skill köprüsü + MCP sunucusu.
Hermes'i yeniden başlattıktan sonra yeni oturumlarda `/czip` gerçek
slash komut olarak görünür.

Claude Desktop / Claude Code için:

```bash
./install.sh --claude    # MCP (pip gerekmez) + beceri + otopilot hook'ları
```

## Komutlar

```
czip paketle <session_id|son|en-uzun|DOSYA|cc:son> [--eksiksiz] [--laya]   → paket + 6 haneli ID
czip oku <id|son>            → PAKETİ AÇMADAN: indeks + son iletler + durum
czip aralik <id|son> <bas-bit>  → seçili aralık tam metin (jetonlar çözülü)
czip ara <id|son> "sorgu"    → paket içi arama, ilgili mesaj i'lerini bul
czip cevre <id|son> <i> [--n=3] → i numaralı mesaj + her yandan n komşu
czip harita <id|son>         → RAG haritası (~1.5k token) + token ekonomisi satırı
czip cc                      → Claude Code / Desktop oturumları
czip listele                 → kayıtlı paketler: id | tarih | başlık
```

Yeni oturumda taşıma akışı:

```
czip oku a37tvc          # harita: hangi mesajda ne var
czip aralik a37tvc 1040-1060   # sadece gereken kısım
```

## Özellikler

- **Sözselleştirme** — tekrarlayan bloklar sözlük jetonuna iner, ham
  tekrar pakete girmez (18x'in sırrı).
- **Kayıpsız mod** — `--eksiksiz`: her bayt geri döner (8,4x).
- **Durum tespiti** — `oku` çıktısı `kullanici_yaniti_bekliyor` ve son
  iletin rolüyle "kaldığın yer"i bildirir; yeni oturum aynı yerden devam eder.
- **Gizlilik** — `state.db` daima READ-ONLY URI ile okunur; paket
  yalnızca `~/.hermes/session-packs` içine yazılır, hiçbir yere
  dışarıya gitmez, silme/bozma yetkisi yoktur.
- **Opsiyonel karar kapısı** — `--laya`: 1200 B üstü araç çıktılarının
  "gerekiyor mu?" kararını yerel Laya modeline toplu sorar; hata ipucu
  içeren çıktılar asla sorgulanmaz. Varsayılan kapalı; Laya kurulu değilse
  sessizce statik davranışa döner.
- **Token ekonomisi satırı** — `czip harita` çıktısı
  `COST map~Nk tok vs full~Mk tok -> %X saved` ile biter; kazanç görünür.

## Bileşenler

| Dosya | Rol |
|---|---|
| `hkp.py` | **motor** — saf stdlib (Python 3.9+, 0 bağımlılık): paketle/oku/ara, CLI, state.db RO okuyucu |
| `czip` | terminal girişi (tek satır sarmalayıcı) |
| `plugin/` | Hermes plugin'i — `/czip`, `/cunzip` slash komutları |
| `mcp_server.py` | MCP sunucusu (sikistir/kilavuz/mesajlar/hafiza/Claude oturumları) |
| `karar.py` | karar kapısı motor seçici (Laya yerel / Jev bulut) |
| `laya_kapi.py` | kalıcı Laya işçi süreci (laya-mlx venv'inde çalışır) |
| `ccd_dokum.py` | Claude Code / Desktop transcript `.jsonl` → czip iletleri |
| `scripts/claude_kur.py` | MCP sunucusunu Claude Desktop'a tanıtır (+ `--hooks` ile Claude Code otopilotu) |
| `claude_hook.py` / `hooks/hooks.json` | otopilot hook'ları: brifing, koruma, hatırlatma, sıkıştırma öncesi paket |
| `yon.py` | yön kartı: kararlar, açık işler, tek sonraki adım |
| `hafiza.py` | günlük + açılış brifingi + alaka kapılı hatırlatma |
| `temizlik.py` | çöp kutulu, ID yönlendirmeli, geri alınabilir haftalık temizlik |
| `skills/` | skill köprüsü — komut geçmişi eski süreçte bile çalışır |
| `scripts/kapak.py` | bu README kapağını üreten Pillow betiği |
| `tests/` | sentetik round-trip + CLI + aralık testleri (4/4) |

## Format: HKP1

```
"HKP1" | meta_len (4B BE) | veri_len (4B BE) | LZMA-9e(meta) | LZMA-9e(gövde)
```

Meta: başlık, sözlük, sayaçlar, format sürümü, paketleme bilgisi.
Gövde: mesaj kayıt listesi (rol, içerik, araç çağrıları, reasoning,
zaman damgası) — jetonlar çözülerek okunur.

## Test

```bash
python3 tests/test_roundtrip.py          # 4/4: paketle→oku→aralik + istatistik
python3 -m unittest discover -s tests    # manifest + Laya kapısı + Claude köprüsü
```

## Sınırlar

- `akilli` mod araç çıktılarını 2000+2000 B baş/son dilimine indirger
  (bildirilir); birebir arşiv gerekiyorsa `--eksiksiz`.
- Paket dosyaları düz metin LZMA'dır — şifreleme sağlamaz; hassas
  içerikli oturum paketlerini disk erişimi olan herkese açık tutma.
- state.db tablo şeması Hermes sürümüne göre değişebilir; motor
  PRAGMA ile eksik kolonları otomatik atlar.
- Jev motoru harici servistir (varsayılan kapalı, bulut yedeği de kapalı);
  KVKK/veri politikası gerektiren işlerde kullanma. Laya, Apple silicon
  MLX ister (laya-mlx); başka yerde kapı atlanır.

## Lisans

MIT

## Lisans

- **Bireysel / ticari olmayan kullanım: ücretsiz ve özgür.**
- **Ticari kullanım: ayrı lisans gerektirir.** Ayrıntı: [LICENSE](LICENSE)

2026-09-20 öncesi sürümler MIT altında yayımlanmıştı; o sürümlerin MIT
hakları saklıdır.
