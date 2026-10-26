# HANDOFF

## 2026-10-26 — laju pengulangan terukur, K6–K9 dibekukan

- Pemilik menolak menunggu 2 minggu. Laju pengulangan ternyata bisa dihitung dari transkrip Claude Code lama (ledger memang tidak menyimpan prompt, transkrip mentah iya): ≈ 0% antar sesi (167 prompt bertugas, 32 sesi). Detail: `research/notes/2026-10-26-agent-workflow-laju-pengulangan-request.md`.
- Dengan ambang yang ditulis sebelumnya (< ~20–30% berarti hentikan), K6–K9 (recipe/replay, `recipes.py`) dibekukan: kode dibiarkan, tidak ada pengembangan lanjutan. Batasnya: 32 sesi riset/desain, Jaccard kata kasar, Codex tidak dihitung.
- Agent Workflow tetap native-default; workflow hanya bila diminta eksplisit atau untuk tulis berisiko. Tidak perlu pencatat prompt baru.
- Selesai: perubahan v1.2 di-commit sebagai snapshot (74fe50c, 226 tes lulus, path lokal diredaksi dari docs/audits). Belum di-push; `VERSION` masih 1.1.0, belum ada rilis v1.2.
- Terbuka: tahap Claude Code K5 (lengan workflow + `/clear`) belum dijalankan.
- Terbuka (opsional): cek pengulangan berdasarkan niat, bukan kata (pemilihan rute Qwen 0,8B dari PoC selector).
- Dipertahankan: `usage_sensor.py`, `usage_ledger.py`.
