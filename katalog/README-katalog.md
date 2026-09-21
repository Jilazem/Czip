# Katalog hazırlığı — Czip dört hedefe nasıl gider

Bu klasör ve kökteki manifestler, Czip'in hedef platform kataloglarından
"katalogdan kurulur" hâle gelmesi için HAZIRLIKTIR (21.09.2026 araştırma
raporu: `../2026-09-21-czip-hedef-katalog-entegrasyon.md`). Hiçbir başvuru
gönderilmedi; gönderimler kullanıcı onayına tabidir.

## Ne nerede

| Yol | Ne için |
|---|---|
| `.claude-plugin/plugin.json` | Claude Code plugin manifesti (Grok Build bunu da kabul eder) |
| `.grok-plugin/marketplace.json` | Grok Build `xai-org/plugin-marketplace` PR girdi taslağı |
| `skills/czip-session-pack/` | Bağımsız İngilizce beceri (CLAWHub/Codex/通用) |
| `skills/czip-oturum-paketle/` | Orijinal Türkçe beceri (dokunulmadı) |
| `katalog/codex-skills/` | OpenAI/Codex skills-only paketi: EN becerinin kopyası |

## Hedeflere göre başvuru özeti (rapor §6 aksiyon planı)

1. **ClawHub / OpenClaw** (en ucuz): `clawhub skill publish
   ./skills/czip-session-pack --slug czip ...` — CLI bu makinede kurulu
   değil, yayın anında `login` + `--dry-run` ile doğrulanmalı.
2. **Grok Build**: Czip push edildikten sonra güncel commit SHA alınır →
   `.grok-plugin/marketplace.json` içindeki `source.sha` ona güncellenir →
   fork + entry + `generate-plugin-index.py` + PR (6 adım, CONTRIBUTING).
3. **Claude Code**: kendi marketplace.json ile kapısız dağıtım, ya da
   `clau.de/plugin-directory-submission` formu (Gökhan'ın hesabı gerekir).
4. **OpenAI/Codex**: platform.openai.com/plugins portalı — kimlik doğrulama
   + 5 pozitif/3 negatif test vakası + `katalog/codex-skills/` beceri
   paketi. (ChatGPT ve Codex TEK katalog paylaşır; MCP'li dal istenirse
   remote public HTTPS MCP zorunlu — yerel stdio MCP bu dala girmez.)

## Kurallar

- Sürüm tek yerden gelir; manifestler sürüm sürükmez (1.0.0 = plugin.yaml).
- Saf stdlib, 0 bağımlılık.
- Commit/push/merge YOK — yerel dal, denetim + onay sonrası.
