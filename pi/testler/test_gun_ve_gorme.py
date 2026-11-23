"""brain.valid_day (gün adı dosya yolu olarak güvenli mi) ve brain.describe_failed (görme hatası ayırt edilir mi)."""
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
    os.environ.pop(k, None)

import brain  # noqa: E402

assert brain.valid_day("2026-11-15")
assert brain.valid_day("2024-02-29")
for bad in ("", "2026-13-01", "2026-02-30", "../memory", "2026-1-5", "2026-11-15/../x", "2026-11-15.md", None):
    assert not brain.valid_day(bad), bad
assert brain.diary_text("../persona") is None
assert brain.day_events("../memory") == []
assert "Günlüğüm" not in brain.search_past(date="../x")

assert brain.describe_failed("")
assert brain.describe_failed("Fotoğrafı çektim ama anlatamadım. gemini: Zaman aşımı")
assert brain.describe_failed("Görüntüyü anlayacak bir model bağlı değil (.env dosyasına anahtar ekle).")
assert not brain.describe_failed("Masada bir kupa ve bir laptop var.")
print("tamam")
