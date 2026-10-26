# Audit alur aktual — 25 Oktober 2026

## Aktivasi

Tidak perlu tag skill pada setiap pesan lanjutan. Metadata agents/openai.yaml mengaktifkan allow_implicit_invocation; instruksi proyek CLAUDE.md juga meminta workflow untuk pekerjaan substansial pada proyek yang diinisialisasi. Pertanyaan biasa tidak membutuhkan task. Pada proyek/chat baru, skill harus tersedia di katalog client dan instruksi proyek harus terbaca. Aktivasi implisit adalah keputusan agent, bukan trigger deterministik pada semua pesan.

Probe native scripts/probe_codex_skill.py berhasil menemukan agent-workflow:agent-workflow, enabled=true, scope=repo, path sumber yang benar. Probe ini tidak menjalankan inference dan tidak membuktikan konsistensi aktivasi implisit pada setiap prompt.

Dokumentasi resmi: https://learn.chatgpt.com/docs/build-skills menjelaskan explicit/implicit invocation dan allow_implicit_invocation.

## Kemampuan aktual

| Kemampuan | Status |
| --- | --- |
| begin, pack, checkpoint, review, verify, resume | Tersedia; diuji unit dan portable |
| Capture, retrieval, pagination Unicode, metrik byte | Tersedia; diuji |
| Unknown, reconcile, cancel dan reservation | Tersedia; diuji |
| Memory kutipan, freshness, revocation, backup DB | Tersedia; diuji |
| Build, instalasi terisolasi, cache runtime | Pemeriksaan distribusi lulus |
| Pemilihan MCP/skill sesuai kebutuhan | Dilakukan agent chat berdasarkan katalog host; tidak ada router dalam CLI |
| Health check setiap MCP atau kualifikasi tool | Belum tersedia; doctor hanya mengecek keberadaan executable tertentu, bukan koneksi/auth/fungsi MCP |
| Feedback | Collector idempotent mengubah event tertentu menjadi proposal generik |
| Feedback melakukan riset web/katalog MCP/skill | Belum tersedia |
| Evaluasi kandidat, instalasi, hook, uji coba, rollback otomatis | Belum tersedia sebagai loop perbaikan feedback |
| Worker/scheduler/background self-improvement | Belum tersedia |

Alur berjalan: permintaan → chat memakai skill → begin/pack → chat memilih tool dan mengerjakan → bukti/review → verify → proposal. Memilih workflow research hanya memberi label; tidak memicu mesin riset.

Collector mengenali validation_failed, outcome_unknown, route_corrected. Namun route_corrected tidak memiliki producer di implementasi core/CLI saat audit. Pengenalan nama event itu bukan bukti router sudah ada. Proposal tidak mengubah prompt, rules, MCP atau hook; tidak ada lifecycle proposal review/activation dalam CLI.

## Verifikasi aktual

- doctor: database sehat, native current chat; optional adapters not_required_not_qualified.
- 48 unittest lulus, 14,213 detik.
- verify_portability: document/code lulus di luar repository, termasuk cancellation reconciliation dan column pagination.
- verify_distribution: lulus, 48 file, cache_runtime=true, rebuild_manifest_equal=true.
- Native skills/list probe: discovered, enabled=true; inference_started=false.

Tidak menguji koneksi ke setiap MCP eksternal, seluruh variasi prompt implicit invocation, atau seluruh OS/client. Hasil ini memvalidasi fitur yang diimplementasikan, bukan fitur backlog.

## Alur tambahan untuk kebutuhan riset mandiri

Rancangan yang belum diimplementasikan: feedback → diagnosis kebutuhan → inventaris tool/skill yang tersedia → riset kandidat bila ada gap → proposal dengan sumber/izin/kompatibilitas → uji kandidat terisolasi → review → aktivasi yang diotorisasi → pengukuran hasil/rollback. Tahap read-only bisa dikerjakan chat aktif; operasi pemasangan dan perubahan konfigurasi mengikuti izin native dan instruksi pengguna. Ini membutuhkan implementasi dan acceptance terpisah agar bisa disebut kemampuan produk.
