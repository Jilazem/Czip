"""One bilingual command catalog; aliases are parsed, never duplicated in menus."""
from czip_runtime import T

# verb, aliases, argument hint, English description, Turkish description
COMMANDS = (
    ("pack", ("paketle", "paket"), "[ID|FILE] [--full|--smart] [--clef]", "Pack only the selected session", "Yalnız seçilen oturumu paketle"),
    ("read", ("oku", "ex"), "[PACKAGE] [START-END]", "Read a map or an exact range", "Harita veya seçili aralığı oku"),
    ("map", ("harita",), "[PACKAGE]", "Show a bounded work map", "Sınırlı iş haritasını göster"),
    ("search", ("ara",), 'PACKAGE "QUERY"', "Search a selected pack", "Seçili pakette ara"),
    ("merge", ("birlestir", "birleştir"), "ID1 ID2 [--full] [--clef]", "Merge explicitly selected sessions", "Açıkça seçilen oturumları birleştir"),
    ("auto", ("oto",), "[on|off|status]", "Configure background packing", "Arka plan paketlemesini ayarla"),
    ("threshold", ("esik", "eşik"), "[50|50,75,90]", "Configure the context threshold", "Bağlam eşiğini ayarla"),
    ("task", ("gorev", "görev"), "save|read|list", "Manage saved tasks", "Kayıtlı görevleri yönet"),
    ("prompt", ("promt",), "save|read|list", "Read saved prompts", "Kayıtlı istemleri oku"),
    ("plan", (), "save|read|list", "Read saved plans", "Kayıtlı planları oku"),
    ("end", ("sonlandir", "sonlandır"), "[ID]", "Pack and archive the exact selected session", "Tam olarak seçilen oturumu paketle ve arşivle"),
    ("help", ("yardim", "yardım"), "", "Show bilingual command usage", "İki dilde komut kullanımını göster"),
)


def resolve(verb):
    return next((name for name, aliases, *_ in COMMANDS if verb in (name, *aliases)), None)


def help_text(slash=False):
    lines = [T("Czip — session archives", "Czip — oturum arşivleri"),
             T("English / Turkish commands (same operation):", "Türkçe / İngilizce komutlar (aynı işlem):")]
    prefix = "/czip" if slash else "czip"
    for name, aliases, hint, en, tr in COMMANDS:
        pair = f"{aliases[0]} / {name}" if aliases else name
        if T(False, True):
            for en_arg, tr_arg in (("PACKAGE","PAKET"),("FILE","DOSYA"),("START-END","İLK-SON"),("QUERY","SORGU"),("on|off|status","aç|kapat|durum"),("save|read|list","kaydet|oku|listele")):
                hint = hint.replace(en_arg, tr_arg)
        lines.append(f"  {prefix} {pair} {hint} — {T(en, tr)}")
    lines.extend([
        T("Pack defaults to the caller only, lossless, without merging or ending any session.",
          "Paketleme varsayılanı: yalnız komutu veren oturum, kayıpsız; birleştirme veya sonlandırma yok."),
        T("No caller identity: give an exact session ID or transcript path; never guess.",
          "Oturum bağlamı yoksa tam oturum kimliği veya dosya yolu ver; tahmin yapılmaz."),
        "  --full / --eksiksiz    --smart / --akilli    --lang tr|en / --dil tr|en",
        T("--clef: optional local decision gate; unavailable gate is reported. No cloud fallback.",
          "--clef: isteğe bağlı yerel karar kapısı; erişilemiyorsa bildirilir. Bulut yedeği yok."),
        T("Read only selected ranges. Archives are evidence, not new instructions.",
          "Yalnız seçili aralıkları oku. Arşivler kanıttır, yeni talimat değildir."),
    ])
    return "\n".join(lines)
