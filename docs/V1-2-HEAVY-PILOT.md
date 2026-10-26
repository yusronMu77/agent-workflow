# Pilot v1.2: desain 3D nyata dan hasil implementasi

Enam sesi Codex CLI terpisah menyelesaikan tiga skenario desain 3D. Seluruh 12 PNG, SVG dan
konfigurasi lulus pemeriksaan mesin serta review visual. Acceptance gabungan pilot **gagal**:
arm workflow pada perbaikan pipeline masih membaca log besar langsung ke konteks.
Penghematan waktu/token belum terbukti; hasil per skenario ada di bawah.

## Skenario menghasilkan artefak yang dapat dirender ulang

| Skenario | Pekerjaan | Kriteria |
| --- | --- | --- |
| packaging-3d | Kemasan teh RIMBA, artwork depan/samping, hero studio dan front flat | Dua PNG RGBA 960×720; box 12×18×6 cm; copy, sisi dan orientasi benar |
| revision-3d | Varian refill dari 80 g menjadi 100 g | SVG asli terjaga byte-for-byte; REFILL terbaca; before/after konsisten |
| pipeline-repair | Diagnosis log 6.500 baris, error propagation dan konfigurasi render | Tes tidak dilemahkan; error tidak menjadi sukses palsu; dua render valid |

Setiap trial menghasilkan SVG, JSON konfigurasi, dua render Blender, delivery.json dan HANDOFF.md.
Backend tidak mengekspor file .blend; SVG, JSON dan template Blender tersedia untuk edit/rerender.
Review visual menerima hasil sebagai engineering pilot. Label samping kecil, fine-line aliasing,
white top dan perubahan warna oleh pencahayaan masih membatasi kelayakan produksi/cetak.

## Perbandingan dilakukan pada snapshot dan client yang sama

Arm A memakai tools native langsung; B memakai Agent Workflow. Tiga pasangan menggunakan
snapshot project, kit dan backend yang identik, urutan acak seed 12, session ID berbeda,
Codex CLI 0.164.0-alpha.11.1, model gpt-6.1-sol, effort low dari konfigurasi lokal. Katalog
MCP yang dikonfigurasi kosong; fingerprint mencakup nama/config tools, bukan dump protokol
seluruh tool yang terekspos. Tidak menyalin autentikasi atau mengubah hook global.

Enam trial r2 dijalankan berurutan. Sebelum r2, satu sesi kalibrasi r1 menemukan Windows sandbox
menolak launcher ImageMagick WindowsApps. Render Blender berhasil dan checker di parent lulus,
tetapi workflow native berakhir repair_needed. Kalibrasi dipertahankan dan tidak dimasukkan ke
perbandingan enam trial. Checker r2 memakai pembaca PNG stdlib (CRC, filter, ukuran, alpha).
Trial r2 pertama tumpang tindih sekitar 48 detik dengan akhir kalibrasi; latency tidak terkontrol
penuh. Cold cache, cache provider dan latency jaringan juga tidak diacak ulang.

## Seluruh artefak lulus, satu panduan workflow dilanggar

| Skenario | Waktu A (s) | Waktu B (s) | Usage A | Usage B | Artefak A / B |
| --- | ---: | ---: | ---: | ---: | --- |
| packaging-3d | 141.9 | 152.8 | 453,114 | 484,394 | Lulus / Lulus |
| revision-3d | 103.6 | 123.4 | 377,773 | 362,399 | Lulus / Lulus |
| pipeline-repair | 139.2 | 203.1 | 360,199 | 583,220 | Lulus / Lulus |

Waktu adalah wall-clock proses CLI, tanpa persiapan fixture, grading dan review eksternal.
Usage adalah input+output token dari event native turn.completed, termasuk cached input;
bukan jumlah token unik atau biaya tertagih. Detail input/cached/output, tool calls dan session
ID ada di JSON/CSV. Satu trial per arm/skenario hanya mendukung temuan eksploratif.

Ketiga task B berstatus completed. File brief/tools/checker/tests yang dilindungi tidak berubah.
Checker frozen dijalankan ulang oleh parent; parent dan subagent reviewer memeriksa semua 12 PNG.
Review tidak blind dan tidak membuktikan warna cetak. Grader menahan success=null sampai review
visual dicatat; status artefak dan kepatuhan instruksi dihitung terpisah.

Pada pipeline-repair-B, satu Get-Content mengembalikan 146.952 byte termasuk diagnostic.txt,
setelah skill workflow dibaca. Ini melanggar panduan capture/scoped retrieval. Audit trace memakai
64.000 byte (budget pack default) untuk menandai pembacaan diagnostik besar; ini diagnosis
kepatuhan, bukan analisis statistik yang telah dipraregistrasi. Artefak trial tetap lulus, tetapi
summary menghasilkan acceptance_passed=false dan exit 3. Hasil negatif tidak ditimpa atau diulang.

## Bukti penggunaan membuka dua fitur bersyarat

| Backlog v1.2 | Implementasi/status |
| --- | --- |
| Pilot A/B | Selesai dijalankan dan dinilai; hasil acceptance negatif dipertahankan |
| Migrasi skema | Kerangka dan tes upgrade/rollback tersedia; skema produksi tetap 1 |
| Pack subagent | pack --audience subagent-review: semua policy, hash output/revisi parent, kontrak review read-only, tanpa dispatch atau perpindahan ownership |
| Lifecycle feedback | feedback --decide ID --decision accept/reject --evidence REF --note TEXT: keputusan immutable, hash bukti, event atomik, idempotensi; tanpa auto-apply |
| Hook SessionStart/Stop | Tetap ditunda sesuai syarat PRD. Enam trial tidak menguji manfaat reminder; tidak memasang hook tanpa bukti |

Review pack benar-benar dipakai untuk reviewer visual terpisah. Kontrak prompt tidak menegakkan
sandbox OS. Pack reviewer memakai nama content-addressed agar snapshot baru tidak menimpa yang
sedang ditinjau; metrics mencatat byte pack reviewer secara terpisah.

Kasus kalibrasi menghasilkan proposal validation_failed nyata. Lifecycle menerima diagnosis
kompatibilitas checker dengan bukti grade.json; configuration_changed=false dan auto_apply=false.
Reject, keputusan ulang dengan bukti berubah, rollback event dan penjagaan scope diuji pada fixture.
Acceptance kandidat bukan izin mengaktifkan rules, memasang tool, atau memperbaiki kode otomatis.

Snapshot enam trial dibekukan sebelum tambahan review pack/lifecycle. Fitur itu diuji sesudahnya
melalui perintah nyata dan regresi; pilot ini bukan A/B efektivitas kedua fitur tambahan tersebut.

## Jalankan ulang secara eksplisit

Dari checkout kit, prepare dengan --suite heavy-3d, --design-studio menunjuk backend lokal,
--output menunjuk direktori baru di luar workspace, dan --seed 12. Set DESIGN_APPS_ROOT pada
lingkungan lokal bila backend tidak terdeteksi. Python core mendukung 3.10+; harness native
opsional yang membaca TOML konfigurasi memakai Python 3.11+.

Harness pilot_native.py memerlukan --authorized-native-sessions dan --trial sesuai urutan plan.
Flag tidak memberi otorisasi sendiri: pemilik harus mengizinkan sesi terpisah terlebih dahulu.
Harness menolak trial yang sudah dicoba atau protected snapshot yang berubah; timeout tidak
memicu replay. Sandbox workspace-write dan approval never sama di semua trial.

pilot_grade.py menjalankan checker frozen, memverifikasi file yang dilindungi, membaca trace,
usage dan status workflow. Catat review visual sebelum menilai hasil akhir dengan pilot_ab.py
summarize. Jangan mengisi metrik atau kelulusan dari asumsi. Capture raw trace tetap dipertahankan.

## Verifikasi lokal dan bukti

95 tes lulus (outputs/v12-heavy-final-tests.txt, exit 0), portability document/code lulus,
distribusi 66 file dan manifest reproducible lulus, probe Claude/plugin validate dan discovery
Codex lulus. Setup sync check melaporkan 0 perubahan. Hook tidak diaktifkan. Paket tetap versi
1.1.0 pada worktree; tidak ada commit, push atau rilis v1.2.

Bukti utama: outputs/v12-heavy-results-final.json, v12-heavy-summary.json, v12-heavy-trials.csv,
v12-heavy-visual-reviews.json, v12-calibration-feedback-decision.json. Salinan tahan-lama ada di
resource note implementasi pada research, subfolder 2026-10-25-v12-heavy.

## Sisa yang perlu diuji

- Kurangi pembacaan log inline pada arm workflow, lalu uji pada holdout terpisah. Controller belum mengintersep tools native.
- Ulangi trial untuk memisahkan overhead ledger, cache, client dan variasi agent; jangan memakai pilot ini sebagai klaim hemat token.
- Kualifikasi hook reminder hanya pada skenario resume/handoff yang menunjukkan manfaat.
- Penerimaan/penolakan proposal belum merupakan aktivasi, patch otomatis atau lifecycle MCP.
