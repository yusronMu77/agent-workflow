# Jalankan PRD dari workspace Agent Workflow

Buka `agent-workflow.code-workspace` di root project ini. Pada mesin awal lokasinya
`agent-workflow/agent-workflow.code-workspace` di folder project.
Pilih Claude atau Codex, kemudian kirim salah satu prompt berikut. PRD adalah spesifikasi;
workspace/task VS Code tidak otomatis mengeksekusinya saat dibuka.

## Implementasi sampai repo privat dan release

Prompt ini secara eksplisit mengizinkan Git init/commit, create private repo, push dan release
untuk target yang disebut, setelah validasi dan pemeriksaan file. Gunakan jika memang siap
melakukan publikasi privat pada eksekusi nanti.

```text
Gunakan skill agent-workflow yang ada di project ini.
Baca AGENTS.md, CLAUDE.md, dan docs/PRD-PORTABLE-PLUGIN.md, lalu implementasikan PRD tersebut
di root workspace ini sampai selesai. Pertahankan core dan state existing.

Target GitHub: yusronMu77/agent-workflow, visibility PRIVATE.
Saya mengizinkan git init bila diperlukan, commit file produk yang sudah direview,
membuat repo privat tersebut bila belum ada, push candidate, menjalankan/memeriksa CI,
serta membuat tag dan release privat setelah acceptance lulus.
Verifikasi akun/owner, remote, isi paket, secret scan, dan visibility sebelum upload.
Jangan force push, menimpa repo lain, mengubah repo publik, atau mengunggah state/credential.
Jika identitas, izin akses, atau existing repo bertentangan, minta klarifikasi untuk konflik itu.

Kerjakan M0 sampai M6 secara bertahap. Simpan checkpoint dan evidence setelah setiap milestone.
Gunakan satu executor untuk file/output yang sama. Jangan berhenti hanya pada proposal desain.
Jika auth/approval native diperlukan, selesaikan pekerjaan lokal yang independen lalu jelaskan
langkah user yang benar-benar diperlukan. Jangan mengklaim runtime client/OS atau savings
provider yang belum diuji.

Hasil akhir: kode dan paket yang reusable di komputer baru, adapter Claude dan Codex,
fallback skill langsung, panduan instalasi/update/rollback, hasil tes/CI,
docs/IMPLEMENTATION-PORTABLE-PLUGIN.md, docs/PRIVATE-PUBLISH.md,
URL repo privat dan release/commit yang tervalidasi, serta batasan yang masih ada.
```

Target owner di atas dapat diganti **dalam pesan eksekusi user** jika akun tujuan berbeda.
Agent harus memeriksa akses; jangan otomatis mengganti owner dengan akun GitHub yang aktif.

## Implementasi lokal dahulu

```text
Gunakan agent-workflow dan implementasikan docs/PRD-PORTABLE-PLUGIN.md di root workspace ini.
Kerjakan M0 sampai M4, siapkan M5/M6 dan dokumentasinya tanpa membuat repo, commit, push,
tag atau release GitHub. Jalankan seluruh validasi lokal yang tersedia, simpan checkpoint,
dan laporkan kesiapan publikasi beserta blocker yang masih ada.
```

## Melanjutkan pada chat/client lain

```text
Baca instruksi project dan docs/PRD-PORTABLE-PLUGIN.md. Gunakan agent-workflow untuk
memeriksa daftar task, resume task implementasi yang relevan beserta integrity check,
lalu lanjutkan dari checkpoint dan evidence terakhir. Jangan mengulang external writes
yang hasilnya belum pasti; inspeksi dan reconcile terlebih dahulu.
Izin publikasi pada sesi ini: [nyatakan lokal saja, atau berikan izin eksplisit dengan target repo].
```

Bagian dalam kurung siku pada prompt resume perlu diganti dengan izin yang Anda maksud.
Pergantian client tidak otomatis memindahkan riwayat chat; checkpoint tidak menjadi izin baru.

## Yang belum dilakukan oleh dokumen ini

Dokumen ini belum mengubah kit menjadi plugin, belum membuat Git repo, dan belum melakukan
upload/release. Kemampuan current core dijelaskan pada README; acceptance PRD diuji ketika
prompt implementasi dijalankan. Nama command installer baru pada PRD adalah target implementasi.
