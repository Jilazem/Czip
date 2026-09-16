# czip — Hermes oturumlarını AI-okur pakete sıkıştır (HKP1)

Uzun bir Hermes sohbetini yeni oturuma taşımak istediğinde tüm geçmişi
bağlama yüklemek hem pahalı hem gereksiz. **czip** oturumu `HKP1` paketine
sıkıştırır; yeni oturum paketi **açmadan** okur: tek satırlık indeks + son
iletiler tam, detay gereken yer ise seçili aralık olarak çekilir.

- **Küçük:** 4.3 MB / 1117 iletlik gerçek bir oturum → 237 KB (18x),
  1 MB'lık oturum → ~120 KB (8.4x kayıpsız, 8.6x akıllı).
- **Kayıpsız:** `eksiksiz` modda her bayt geri döner (rol, içerik,
  tool_calls, reasoning, sıra). Akıllı mod yalnız taşıma-anlamı olmayan
  tekrarı siler ve ne sildiğini bildirir.
- **Güvenli:** `state.db` daima READ-ONLY; paket dışarıya gitmez, yalnız
  kendi makinende kalır; silici yok, bozucu yok.

## Nasıl çalışır (HKP1 formatı)

Paket = `HKP1` + meta (sözlük jetonları, sabitler) + LZMA gövde.
Sözlükselleştirme, whitespace/satır-tekrar temizliği ve JSON-sadeleştirme
sıkıştırma oranını belirgin artırır; iceri_ac jetonları şeffaf çözer.

## Kurulum

```bash
# 1) Motor + CLI (macOS/Linux, sadece stdlib — Python 3.9+)
./install.sh            # czip komutunu PATH'e, plugin'i ~/.hermes/plugins/,
                        # skill'i ~/.hermes/skills/, MCP'yi config'e ekler
# 2) Yeni bir Hermes oturumu aç (plugin/MCP açılışta yüklenir)
```

Manuel kurulum istersen: `czip` dosyasını PATH'e kopyala; plugin'i
`plugin/` klasörüyle `~/.hermes/plugins/czip/` altına; skill'i
`skills/czip-oturum-paketle/SKILL.md` ile `~/.hermes/skills/` altına
al; MCP için config'e ekle:

```yaml
mcp_servers:
  oturum-sikistirici:
    command: python3
    args: [<repo>/mcp_server.py]
```

## Kullanım

```bash
czip paketle son            # aktif oturumu paketle (session_id|son|en-uzun)
czip oku <paket.hkp|son>    # INDEKS + son 6 ilet + durum (~40 KB)  — PAKET AÇILMAZ
czip aralik <paket> 40-55   # yalnız o aralığı tam metin oku
```

Yeni oturumda doğal dil de yeterli: _"şu paketi oku, 40-55 arasını getir,
kaldığım yerden devam et"_.

## Bileşenler

| Dosya | İş |
|---|---|
| `hkp.py` | Motor (saf stdlib): paketle/oku/aralık, state.db RO okuyucu, CLI |
| `czip` | Terminal girişi (`czip paketle/oku/aralik`) |
| `plugin/` | `/czip` + `/cunzip` Hermes slash komutları |
| `mcp_server.py` | MCP sunucusu (7 araç: sikistir, iceri_ac, kilavuz, mesajlar, bilgi, oturum_sikistir, durum_tablosu) |
| `skills/` | Skill köprüsü (slash görünmeden de kullanılır) |
| `tests/` | Sentetik round-trip testi — `python3 tests/test_roundtrip.py` |

## Sınırlar

- Plugin/MCP komutları yalnız **yeni açılan** oturumlarda görünür
  (kayıt süreç açılışında okunur); skill ve terminal girişi anında çalışır.
- Akıllı mod araç çıktılarını baş/son 2 KB dilime indirger (bildirir);
  kayıtsız-şartsız taşıma için `--eksiksiz`.
