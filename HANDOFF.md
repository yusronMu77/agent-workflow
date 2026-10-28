# HANDOFF

## 2026-10-28 — roadmap B/C/D diimplementasikan (tes unit saja)

- Rilis v1.2.0 atas perintah pemilik: `VERSION` dan tiga manifest plugin 1.2.0, tag `v1.2.0`, release GitHub dengan ZIP + `.sha256`. Belum diuji native: pemanggilan implisit skill, overhead token, fitur roadmap baru di sesi Claude/Codex sungguhan.
- Di-push atas perintah pemilik. Run CI pertama gagal 5/6 job; diperbaiki: `wf.py` memotong argv di `--` sendiri (argparse Python 3.10), path temp di `qualify_io`/`test_qualification` di-`resolve()` (8.3 Windows, `/var` macOS), job wrapper Windows masuk Job Object kill-on-close (anak yatim memegang log; reproduksi lokal 2/20 → 0/20). Run 40628527531 hijau 6/6 termasuk macOS. Butir lama di bawah tentang "belum dipasang/di-push" sudah tidak berlaku.
- Workflow task `roadmap-impl-2` completed: 15 butir fitur dari checklist research diimplementasikan; status per butir di `docs/ROADMAP-IMPLEMENTATION.md`. 249 tes lulus (23 baru di `tests/test_roadmap.py`), portability/distribusi/probe exit 0.
- Keputusan: skema DB tetap 1; semua record baru (job, lease, change, candidate, route, benchmark) disimpan sebagai event outbox, bukan tabel baru, supaya tes migrasi dan state lama tidak terganggu.
- Keputusan: `harness_exit_zero` hanya menerima exit code dari proses (`finish --run`/`harness run`); `capture_exit_zero` lama tetap menerima laporan agen.
- Belum dipasang: hook Stop `harness hook`, jadwal `scripts/worker.py`, broker MCP, profile dispatch terkualifikasi. Belum di-commit/push; CI `macos-latest` baru jalan setelah push.
- Terbuka: uji native fitur baru, M6 gateway/model tambahan, acceptance pekerjaan nyata, keputusan rilis v1.2.
- Terpasang di research: harness check `default` = `notes-index.ps1 check`, timeout 30 s (`.agent-workflow/harness.json`); VS Code task "Workflow: health MCP (statis/probe live)", "harness check task", "budget", "worker sekali jalan", "status UI"; **hook Stop `harness hook`** di `.claude/settings.json` (dipilih pemilik; "mode manual" tadinya berarti mode izin Claude, otomasi boleh). Hook diam saat mengizinkan dan fail-open saat error; `agent-setup/test_hooks.py` menyaringnya. Belum dipasang (tidak dipilih): jadwal worker, broker, profile dispatch.
- Lanjutan hari yang sama: M6 (`workflow_core/models.py` worker OpenAI-compatible + selector model, `scripts/gateway.py` bertoken read/propose) dan `acceptance` dibangun; acceptance nyata kit 0,905 (22 task), research 1,0 (3 task). K7 dijalankan: 324 sesi, biaya per hasil null karena belum ada label K6. Checklist research 29/38; sisa 9 = uji native, input pemilik K6/K8/K9, macOS (perlu push), keputusan E.
- Perbaikan: job memakai CREATE_NO_WINDOW (sebelumnya jendela terminal muncul tiap job di Windows); guard pilot hanya mengenali `.agent-workflow/project.json` (ledger `~/.agent-workflow` membuat 12 tes pilot gagal). 256 tes lulus.
- Perbaikan: argumen perintah memakai `nargs="*"`, bukan REMAINDER (opsi seperti `--timeout` setelah nama sebelumnya ikut tertelan ke argv); tes regresi ditambah, 250 tes lulus.

## 2026-10-27 — sinkron checklist research ↔ kit

- Checklist di `research/notes/2026-10-25-agent-workflow-checklist-belum-diimplementasi.md` disinkronkan dengan kit: 38 butir, 12 selesai, 26 terbuka (A 0, B 9, C 6, D 7, E 4).
- Terverifikasi hari ini: 226 tes lulus (Python 3.13), `doctor` ok, `main` maju 4 commit dari `origin/main` (belum di-push), `VERSION` 1.1.0 dan belum ada tag v1.2.0, `docs/audits/` tracked dan bersih dari path lokal.
- Terbuka untuk pemilik: push 4 commit lokal; putuskan rilis v1.2 (snapshot 74fe50c).
- Terbuka (usulan, belum diputuskan): verifikasi oleh harness (hook Stop menjalankan tes, exit code dari proses) menggantikan `--exit-code` yang dilaporkan agen. Sumber: `research/notes/2026-10-27-agent-workflow-akar-masalah-tidak-masuk-akal.md`.
- Terbuka: alat feedback K6–K9 (`owner_feedback.py`, `cost_per_outcome.py`, `change_proposals.py`, `assignment.py`) belum pernah jalan pada data nyata; `route_corrected` masih tanpa producer.

## 2026-10-26 — laju pengulangan terukur, K6–K9 dibekukan

- Pemilik menolak menunggu 2 minggu. Laju pengulangan ternyata bisa dihitung dari transkrip Claude Code lama (ledger memang tidak menyimpan prompt, transkrip mentah iya): ≈ 0% antar sesi (167 prompt bertugas, 32 sesi). Detail: `research/notes/2026-10-26-agent-workflow-laju-pengulangan-request.md`.
- Dengan ambang yang ditulis sebelumnya (< ~20–30% berarti hentikan), K6–K9 (recipe/replay, `recipes.py`) dibekukan: kode dibiarkan, tidak ada pengembangan lanjutan. Batasnya: 32 sesi riset/desain, Jaccard kata kasar, Codex tidak dihitung.
- Agent Workflow tetap native-default; workflow hanya bila diminta eksplisit atau untuk tulis berisiko. Tidak perlu pencatat prompt baru.
- Selesai: perubahan v1.2 di-commit sebagai snapshot (74fe50c, 226 tes lulus, path lokal diredaksi dari docs/audits). Belum di-push; `VERSION` masih 1.1.0, belum ada rilis v1.2.
- Terbuka: tahap Claude Code K5 (lengan workflow + `/clear`) belum dijalankan.
- Terbuka (opsional): cek pengulangan berdasarkan niat, bukan kata (pemilihan rute Qwen 0,8B dari PoC selector).
- Dipertahankan: `usage_sensor.py`, `usage_ledger.py`.
