# Verifikasi lanjutan v1.2: pengujian lokal dan batas yang tersisa

Sisa pengujian lokal sudah dijalankan: sepuluh sesi Codex holdout, hook Claude startup/resume/compact/Stop,
plugin project dan pemanggilan skill, izin path berspasi, konkurensi antarproses serta batas rebind.
Seluruh 103 tes regresi lulus pada Python 3.10 dan 3.13. macOS belum diuji karena runner tidak tersedia.
Hasil negatif pilot 3D lama dipertahankan; holdout baru tidak mengubah hasil lama tersebut.

## Log besar lulus holdout tanpa klaim penghematan

Tiga fixture baru berisi 14.000 baris: log biasa, Unicode, serta warning yang menyebut ERROR lama.
Arm A memakai tools native langsung, B memakai workflow dengan preflight sebelum membaca data.
Semua menerima batas output per perintah 16.000 byte. Skill diperjelas agar task/context disiapkan
lebih dahulu dan log besar dicari secara terbatas. Ini perubahan panduan, bukan intersepsi tools.

Enam sesi baru menghasilkan jawaban yang benar, mempertahankan hash input/tools, dan seluruh
output per perintah di bawah batas. Nilai maksimum yang diamati 8.681 byte. Ketiga task B completed.
Dua pengulangan tambahan per arm pada input plain-log yang sama juga lulus. Total sepuluh sesi
memiliki ID berbeda; lima task B completed. Codex CLI/model/effort tetap sesuai konfigurasi lokal
sebelumnya: gpt-6.1-sol, low. Ini holdout diagnosis log, bukan uji ulang efektivitas desain 3D.

| Plain-log identik, tiga run per arm | Native A | Workflow B |
| --- | ---: | ---: |
| Median waktu proses CLI | 26,846 s | 51,664 s |
| Rentang waktu | 21,891–27,255 s | 47,850–61,007 s |
| Median input+output token termasuk cached input | 90.179 | 200.827 |

Usage berasal dari event native, bukan estimasi byte. Cached input disimpan terpisah dalam JSON.
Sampel masih kecil, cache/client/model latency tidak diisolasi secara kausal, dan biaya tertagih
provider tidak tersedia. Hasil ini menunjukkan overhead pada tugas log tersebut; tidak mendukung
klaim hemat token atau atribusi semua selisih kepada ledger saja.

## Reminder diuji pada klien nyata tanpa pemasangan global

Pengguna mengizinkan konfigurasi --settings hanya untuk fixture terisolasi setelah adapter dan
konfigurasi siap ditinjau. Adapter opsional session_reminder.py membaca SQLite secara read-only,
memeriksa root, identitas/schema, dan mengembalikan ID/status/revisi saja. Tidak ada request,
summary bebas atau kredensial yang dimasukkan ke context hook. Adapter tidak mengubah status task.

Kontrol tanpa hook menjawab UNKNOWN. SessionStart startup dan resume berhasil mengembalikan
resume-check/running/revisi 3. Stop memblokir penghentian satu kali per turn dan memicu balasan tambahan; callback dengan
stop_hook_active=true tidak meminta kelanjutan lagi. Native model tetap menaati larangan tools,
tidak menjalankan task, dan melaporkan sisa pekerjaan. State tetap running/revisi 3 setelah uji.
Resume native diulang setelah guard identitas adapter diperketat, dengan hasil sama.

Perintah /compact dijalankan pada sesi native yang sama: trace mencatat compact_boundary dan
SessionStart:compact berstatus success, dengan payload ID/state yang sama dan tanpa mutasi task.
Manfaat yang terbukti terbatas pada pengingat state dan event lifecycle yang diuji; tidak
membuktikan manfaat pada semua pekerjaan. Generator settings memakai PowerShell dan baru
terkualifikasi pada Windows. Hook tidak dipasang atau diaktifkan pada settings global.

Format SessionStart additionalContext, kontrol Stop dan guard stop_hook_active mengikuti
[dokumentasi hook resmi](https://code.claude.com/docs/en/hooks). Halaman /hooks pada versi ini
merupakan browser konfigurasi; aturan workspace tetap meminta review/trust pemilik sebelum aktivasi.

## Plugin Claude, pemanggilan dan izin sudah melewati runtime

Marketplace lokal disalin dari product source, lalu didaftarkan dan plugin diinstal pada scope
project di direktori uji berspasi. Ini pemasangan runtime project, bukan sekadar plugin validate.
Claude Code 2.1.302 memakai model default claude-opus-5-5 menurut metadata sesi; autentikasi
existing dipakai tanpa menyalin credential atau database sesi native.

Pemanggilan eksplisit berhasil menyelesaikan explicit-answer; resume implisit berhasil memanggil
skill dan menyelesaikan native-resume. Kedua output JSON berisi answer 87 dan task completed.
Dua negative prompt tanpa larangan workflow tambahan (pertanyaan aritmetika dan edit satu baris)
tidak memanggil skill. Dua percobaan resume implisit diamati termasuk run awal yang terminalnya
ditolak. Sampel ini belum mengukur precision/recall pada prompt luas atau seluruh versi klien.

Run awal tidak menyelesaikan workflow karena perintah memakai assignment/compound shell yang
berada di luar allowlist python sempit. Kegagalan disimpan. Run berikut memakai satu perintah
python per call, tanpa memperluas izin menjadi shell bebas; kedua task berhasil selesai.

Dua probe izin terpisah mengizinkan doctor dan menolak begin pada mode dontAsk. Probe kedua
menggunakan literal path script dan --project yang keduanya mengandung spasi. Setiap run
mencatat satu denial; database tetap memiliki nol task dan file target tidak dibuat. Hasil ini
mengkualifikasi pola literal/wrapper PowerShell yang diuji, bukan setiap wildcard Bash pada versi lain.
Konsep scope plugin sesuai [dokumentasi plugin resmi](https://code.claude.com/docs/en/plugins).

## Pengujian konkurensi menemukan dan memperbaiki race

Smoke awal tiga putaran tidak menemukan kegagalan. Probe dengan barrier yang tercatat kemudian
mereproduksi FileNotFoundError: dua proses berbagi staging .tmp yang sama. Mengganti staging
menjadi unik mengungkap PermissionError MoveFileEx Windows yang sementara; tujuh penulis gagal
pada sepuluh putaran sebelum penanganan tersebut ditambahkan.

write_owned kini memakai staging unik, flush/fsync dan retry Windows terbatas untuk PermissionError.
Kegagalan replace mempertahankan artefak lama dan hanya membersihkan staging milik invocation itu.
Sesudah perbaikan, sepuluh putaran/dua puluh penulis lulus: setiap artefak utuh dan tidak ada staging
tersisa. Probe reservasi terpisah mempertemukan dua proses pada satu output: satu diterima, satu
ditolak, dan tepat satu task/reservation/event tersimpan; quick_check=ok. Ini tidak mengimplementasi
ownership job atau scheduler untuk agent lain.

## Rebind dan deteksi host memiliki batas yang eksplisit

Drift policy menolak resume; rebind dengan note nonempty menerima refresh, menghapus review lama
dan mewajibkan pack baru. Probe juga membuktikan bahwa note tetap klaim chat: controller tidak
membuktikan manusia/agent membaca policy. Risiko ini belum hilang dan bukan klaim review independen.

CODEX_THREAD_ID, CODEX_SESSION_ID dan CODEX_CI ditemukan sebagai booleans presence-only pada
sepuluh sesi. Nilainya tidak dicetak. Kehadiran env yang diwariskan tidak membuktikan instruksi
project target sudah dimuat host. Deteksi otomatis Codex tidak ditambahkan; fallback inline tetap
konservatif dan host eksplisit tetap tersedia. Ini batas implementasi, bukan hasil test yang dipalsukan.

## Bukti dan sisa yang belum teruji

- Regresi akhir: 103 tes lulus masing-masing pada Python 3.13.15 dan 3.10.21 Windows.
- outputs/v12-log-holdout-results.json, v12-log-repeat-results.json dan v12-repeat-analysis.json.
- outputs/v12-reminder-qualification-final.json dan v12-claude-runtime-qualification.json; raw trace tetap disimpan.
- outputs/v12-io-baseline-instrumented.json, v12-io-after.json, v12-io-fixed.json, v12-reservation-qualification.json dan v12-rebind-qualification.json.
- Arsip riset: resource note implementasi, subfolder 2026-10-25-v12-qualification. Hasil gagal tidak ditimpa.

Belum diuji: macOS, biaya provider tertagih, dan konsistensi skill pada
sampel prompt/versi klien yang luas. Deteksi host Codex otomatis serta loop aktivasi feedback/MCP
masih membutuhkan implementasi tersendiri. Tidak ada commit, push, perubahan hook global atau rilis baru.

Catatan audit: task v12-remaining-qualification mencapai batas waktu ledger dan tetap partial; hasil eksperimen tidak diubah. Penutupan/review dilakukan terpisah melalui v12-qualification-delivery.
