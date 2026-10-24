# Bukti workflow dari chat VS Code

Pengujian dilakukan pada 2026-10-24 melalui tool terminal pada chat Codex yang sedang dipakai.
Chat ini membuat task `vscode-smoke`, membaca context pack, menulis laporan ini, lalu mencatat
bukti dan review sebelum menjalankan validator. Ini menguji jalur native chat yang tersedia.

## Native skill discovery

`python scripts/probe_codex_skill.py` menjalankan owned Codex app-server tanpa thread/inference,
lalu meminta `skills/list`. Hasil: `agent-workflow`, enabled `true`, scope `repo`, dengan sumber
`skills/agent-workflow/SKILL.md`. Codex CLI: `0.164.0-alpha.11.1`; extension `openai.chatgpt`
`26.1021.22027`, VS Code `1.143.1`. Ini membuktikan discovery engine; klik UI/new chat belum diuji.

## Portabilitas

`python scripts/verify_portability.py` menyalin kit ke dua project terpisah di direktori temporary,
dengan nama folder berspasi, UUID berbeda, dan library multi-folder melalui workspace JSONC.
Kedua case (`document`, `code`) berhasil: init, skill link, pack, kegagalan pertama, repair,
review, completed, resume, memory kutipan, stale/revoked filtering, dan backup SQLite.
Tes unit juga memindahkan project dan melanjutkan checkpoint, termasuk reservasi output aktif.
Hasil tidak berarti semua OS, agent, atau plugin sudah kompatibel.

## Pemeriksaan

| Pemeriksaan | Hasil |
|---|---|
| Core baru | 24 tes lulus |
| Workflow/selector PoC lama | 24 tes lulus |
| Adapter hooks lama | 6 tes lulus |
| Skill quick_validate | Valid; PyYAML dijalankan melalui environment uv terpisah |
| Sync sumber | 28 live skills; MCP/provider existing dipertahankan |

Bukti terminal dicatat dalam task evidence privat; hasil fixture bukan penilaian independen atas
kualitas dokumen. Review task ini adalah penilaian agent dengan bukti, bukan persetujuan user.

## Batas

Workflow menggunakan chat ini sebagai executor. Context pack menyertakan aturan utuh dan sumber
terpilih; pack task ini 28.641 byte. Tidak ada pengukuran token native atau penghapusan history.
Basic Memory, MetaMCP, acpx, Rulesync dan provider gateway belum menjadi adapter aktif.
Qwen kecil tetap PoC opsional terpisah. Tidak ada model baru atau daemon yang dijalankan otomatis.
Untuk discovery pada UI, mulai chat baru/reload klien; chat berjalan dapat memakai CLI langsung.
