---
description: czip komutlarının tam listesi — ne yapabileceğini tek ekranda hatırlat
allowed-tools: Bash
---

Aşağıdaki listeyi **olduğu gibi** göster. Kullanıcı bir konu adı yazmışsa
(`$ARGUMENTS`), yalnız o bölümü göster ve tek satırda örnek ver.

```
📦 czip — oturum paketleme ve arşiv komutları

PAKETLE
  /czip [son|aktif|<id>|<dosya>] [--oto] [--oto-pasif] [--jev] [--eksiksiz]
      Oturumu .hkp paketine sıkıştırır. --oto: aynı işi yapan oturumları da
      aynı pakete alır. --oto-pasif: kaynakları ayrıca pasife alır.
      --jev: büyük araç çıktılarını karar kapısına sorar (motor: Laya, yerel).
      --eksiksiz: kayıpsız mod (kırpma ve budama yok).

OKU (paketi açmadan)
  /czip_ex [<paket|ID|son>] [ara "<sorgu>" | <bas-bit> | tam]
      Harita + son iletiler; aralık verirsen o iletilerin tam metni.

ARA
  /czip_search "<sorgu>"          tüm arşivde ara
  /czip_search <ID> "<sorgu>"     tek pakette ara
  /czip_search index              arşiv indeksini tazele

BİRLEŞTİR
  /czip_merge [oto|<id1> <id2> ...] [--jev] [--gun=7]
      Aynı işi yapan oturumları tek pakete alır; kararı karar kapısı verir.

OTOMATİK MOD
  /czip_auto [on|off]             eşiği geçince sormadan paketler
  /czip_esik [50 | 50,75,90]      eşik = context penceresinin yüzdesi

GÖREV DEPOSU (kalıcı promt/plan)
  /czip_promt <ham görev>         prompta çevirir, onayını alır, kaydeder, işe başlar
  /czip_plan  <ham görev>         önce keşfeder, adım adım plan çıkarır, onay alır
  /czip_promt oku <kid|son>  ·  /czip_promt listele
  /czip_plan  oku <kid|son>  ·  /czip_plan  listele

NOTLAR
  • Paketin tamamı asla bağlama yüklenmez; oku/ara/aralık ile parça parça okunur.
  • Kaynak oturumlara dokunulmaz (salt-okuma); --oto-pasif hariç.
  • Karar kapısı Laya ile yerel çalışır (anahtar istemez); Türkçe içerik node1 ile
    İngilizceye çevrilip sorulur. CZIP_KARAR_MOTORU=jev ile eski motora dönülür.
  • Telegram/Hermes tarafında aynı komutlar: /czip_help, /czipauto, /czipara, /czipgorev …
```
