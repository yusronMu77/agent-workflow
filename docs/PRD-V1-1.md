# PRD: Agent Workflow v1.1 — bukti verifikasi, dukungan Claude Code, kesiapan publik

Status: diimplementasi di cabang kerja, belum dirilis. Tanggal: 2026-10-25.
Lanjutan dari [PRD-PORTABLE-PLUGIN.md](PRD-PORTABLE-PLUGIN.md). Dasar: review 2026-10-25
(catatan riset `2026-10-25-review-agent-workflow-optimalisasi-claude.md` di project research) dan
audit di [audits/](audits/).

## 1. Hasil yang diminta

1. Gerbang verifikasi tidak bisa dilewati hanya dengan teks bukti buatan agent sendiri.
2. Dukungan Claude Code setara Codex: tidak menyalin ulang instruksi yang sudah dimuat host,
   ada probe otomatis, dan dokumentasi konfigurasi.
3. Task tidak buntu ketika policy atau source berubah setelah ditinjau; deadline dapat diatur per task.
4. Kekeliruan kecil pada urutan event, pesan error, dan penanganan skema diperbaiki.
5. Repo siap dipublikasikan: daftar syarat tertulis dan terperiksa sebelum visibilitas diubah.

Bukan tujuan: executor, router MCP, runner shell, hook global, atau pengukuran token native.

## 2. Baseline yang terbukti

| Fakta | Bukti (2026-10-25) |
|---|---|
| Review lolos dengan bukti `ok` dan note `fine`, `verify` → `completed` | Probe Engine di project sementara |
| Pack project research 24.613 B, dari itu `CLAUDE.md` 22.620 B yang sudah dimuat Claude Code | `context-1.md` tiga task; ukuran file |
| Edit `CLAUDE.md` di tengah task menolak `checkpoint` dan `resume`; task tetap `running` | Probe Engine |
| `ORDER BY id` pada uuid4 mengembalikan event `l0,l3,l1,l4,l2` | Probe Engine |
| Source non-UTF-8 gagal dengan `UnicodeDecodeError` mentah | Probe Engine |
| Feedback: 0 proposal, memory: 0 pada project research; hanya 2 capture dan 1 read | `state.sqlite` research |
| Repo `yusronMu77/agent-workflow` berstatus PRIVATE, tanpa LICENSE | `gh repo view`, daftar file |

## 3. Persyaratan dan acceptance

| ID | Persyaratan | Acceptance | Status |
|---|---|---|---|
| R1 | Check `capture_exit_zero`: lolos hanya bila ada capture ber-exit 0 yang dibuat saat output memiliki hash yang sama dengan saat `verify`; hanya exit non-nol → `failed`; tidak ada → `not_checked` (status `needs_review`). Exit code tetap nilai yang dilaporkan chat. | Tes: tanpa capture, exit None diabaikan, exit 1 gagal, exit 0 lolos, output diedit setelah capture tidak lolos | Selesai |
| R2 | `review --evidence output:KEY` mengikat review ke arsip capture; key tak dikenal atau arsip berubah ditolak. | Tes `test_review_can_cite_captured_output`, `..._rejects_unknown_and_tampered` | Selesai |
| R3 | `pack` merujuk (path, hash, ukuran) instruksi yang dimuat host: `CLAUDE.md` untuk `claude`, `AGENTS.md` untuk `codex`; `--host claude|codex|none`; default dari `CLAUDECODE=1`, selain itu `none`; host tak dikenal ditolak; hash tetap diperiksa. Metrik `policy_ref_bytes`. | Tes pack claude/none, deteksi host, drift tetap terdeteksi; ukuran diukur 24.613 → 2.289 B pada salinan instruksi research | Selesai |
| R4 | `rebind TASK --note`: memperbarui snapshot policy/source setelah ditinjau, menghapus review, mengembalikan status `ready` (wajib `pack` ulang); menolak tanpa perubahan, task terminal, task `blocked_outcome`/`cancel_requested`, dan konfigurasi project yang berubah. Pesan drift menunjuk `rebind`. | Tes `test_rebind_*` (3 skenario) | Selesai |
| R5 | `begin --minutes N` (1–10080) menggantikan budget menit project untuk task itu. | Tes `test_task_minutes_override_budget` | Selesai |
| R6 | Urutan event deterministik (`created, rowid`). | Tes `test_events_keep_insertion_order` | Selesai |
| R7 | Source/output non-UTF-8 ditolak dengan pesan yang menjelaskan tindakan; koneksi SQLite tidak bocor saat Store menolak project atau skema. | Tes `test_non_utf8_*`, `test_newer_schema_is_refused` | Selesai |
| R8 | Guard skema: database dengan skema lebih baru dari kit ditolak; tidak ada downgrade. | Tes yang sama | Selesai (kerangka migrasi belum ada) |
| R9 | Deskripsi skill menyebut kapan tidak dipakai; `scripts/probe_claude_skill.py` memeriksa frontmatter, versi manifest, launcher dari direktori lain, link skill project, dan `claude plugin validate` bila CLI ada; CI menjalankan probe tanpa CLI. | Tes probe positif/negatif; `claude plugin validate` exit 0 pada kit ini | Selesai |
| R10 | [CLAUDE-CODE.md](CLAUDE-CODE.md): perilaku, pemasangan, pengaturan opsional (compact instructions, izin baca-saja), batas. Kit tidak mengubah `settings.json` atau CLAUDE.md global dan tidak memakai `allowed-tools`. | Dokumen ada dan dirujuk README | Selesai |
| R11 | Skenario v1.1 masuk `verify_portability.py` (copied kit, gate, rebind, host pack). | Skrip lulus untuk document dan code (2026-10-25) | Selesai |

## 4. Keputusan dan alasan

- **Referensi, bukan penghapusan, untuk instruksi host.** Integritas dijaga oleh hash yang diperiksa
  ulang, bukan oleh salinan teks. Risiko: chat yang dimulai di luar project tidak memuat `CLAUDE.md`;
  jalan keluarnya `--host none`, tertulis di SKILL.md dan CLAUDE-CODE.md.
- **Gerbang exit code dikaitkan ke hash output.** Tes yang lulus sebelum edit tidak boleh menutup
  hasil sesudah edit. Batas: perubahan file lain selain output tidak membatalkan capture.
- **`rebind` menghapus review.** Review dibuat di bawah policy lama; mengganti policy tanpa review
  ulang akan membawa klaim usang ke `completed`.
- **Tidak ada `allowed-tools` di skill.** Task record tidak boleh memberi izin; allowlist baca-saja
  ditawarkan sebagai pilihan pengguna di dokumen.
- **Feedback dan memory dibekukan.** Tidak ada penggunaan nyata; tidak ditambah sampai ada kasus.
- **Deteksi host hanya Claude.** Variabel lingkungan Codex tidak terverifikasi; host lain tetap aman
  (semua disalin).

## 5. Kesiapan publik (Public readiness)

Repo saat ini PRIVATE. Mengubah ke publik dan push adalah tindakan keluar mesin; keputusan pemilik.
Syarat sebelum visibilitas diubah:

| # | Syarat | Status |
|---|---|---|
| P1 | Pilih lisensi dan tambahkan `LICENSE` (belum ada; tanpa lisensi, kode publik tidak boleh dipakai ulang secara hukum) | Belum |
| P2 | Pindai seluruh riwayat Git dengan gitleaks (bukan hanya tree); hasil sebelumnya hanya pada kandidat rilis | Belum diulang pada v1.1 |
| P3 | Hapus path lokal dan nama fixture dari dokumen yang dilacak (`docs/RESEARCH-INTEGRATION.md` dan `docs/BENCHMARK-GUIDE.md` memuat path `C:/Users` lokal); `docs/audits/` memuat path lokal dan tidak di-stage | Sebagian |
| P4 | Ganti petunjuk instalasi privat (`PRIVATE-PUBLISH.md`, README: "Clone repo privat") dengan petunjuk publik; hapus asumsi auth Git | Belum |
| P5 | Tinjau `docs/` untuk klaim yang tidak lagi benar (mis. "tidak ada lisensi open-source baru") | Belum |
| P6 | Tag rilis `v1.1.0`, ZIP + checksum dari `build_release.py`, catatan rilis | Belum |
| P7 | Konfirmasi tertulis pemilik: visibilitas, lisensi, dan push | Menunggu |

## 6. Rencana eksekusi

| Tahap | Isi | Keluar |
|---|---|---|
| M1 | R1–R8: engine, CLI, tes | Selesai: 64 tes lulus |
| M2 | R9–R10: probe, deskripsi, dokumen, CI | Selesai |
| M3 | R11 dan verifikasi penuh: portability, distribusi, probe | Selesai: semua lulus 2026-10-25 |
| M4 | Commit lokal di cabang kerja | Menunggu perintah |
| M5 | P1–P7, lalu push dan perubahan visibilitas | Menunggu keputusan pemilik |
| M6 | Pilot A/B kecil (3–5 skenario dari BENCHMARK-GUIDE.md), tanpa fitur baru | Backlog v1.2 |

## 7. Matriks validasi

| Pemeriksaan | Perintah |
|---|---|
| Unit dan regresi | `python -m unittest discover -s tests -v` |
| Portabilitas copied kit | `python scripts/verify_portability.py` |
| Distribusi dan cache terisolasi | `python scripts/verify_distribution.py` |
| Skill/plugin Claude | `python scripts/probe_claude_skill.py [--project ROOT]` |
| Payload output | `python scripts/benchmark_outputs.py --output NEW.json` |

Tes hijau membuktikan korektness controller, bukan kualitas kerja agent, pemanggilan implisit,
atau penghematan token.

## 8. Risiko

| Risiko | Mitigasi |
|---|---|
| `capture --exit-code` dipalsukan oleh chat | Dinyatakan di SKILL.md dan README; review tetap klaim chat. Hanya mempersulit kelalaian, bukan penipuan. |
| `rebind` dipakai untuk menutup drift tanpa membaca | Catatan wajib terekam sebagai event; review dihapus; pack ulang wajib. |
| Referensi `CLAUDE.md` tidak terbaca oleh chat | `--host none`; hash dicantumkan agar ketidakcocokan terlihat. |
| Kerangka skema tanpa migrasi | Guard menolak skema baru; migrasi pertama harus menambah tes upgrade sebelum kolom baru. |
| Publikasi tanpa lisensi atau dengan data lokal | Bagian 5, semua syarat sebelum perubahan visibilitas. |

## 9. Backlog v1.2 (tidak dikerjakan di sini)

1. Pilot A/B kecil dan pencatatan hasilnya.
2. Kerangka migrasi skema dengan tes upgrade.
3. Hook SessionStart/Stop opsional untuk mengingatkan task aktif, bila terbukti berguna.
4. Jalur subagent untuk pack, bila ada kebutuhan nyata.
5. Evaluasi lifecycle proposal feedback (accept/reject) hanya bila ada data penggunaan.

## 10. Referensi

- [CLAUDE-CODE.md](CLAUDE-CODE.md), [TOKEN-OPTIMIZATION.md](TOKEN-OPTIMIZATION.md), [BENCHMARK-GUIDE.md](BENCHMARK-GUIDE.md).
- Dokumentasi Claude Code tentang skills, plugin, dan permission rule; diperiksa saat implementasi
  lewat `claude plugin validate --help`. Aturan permission path berspasi tidak diuji di sini.
