# Publikasi dan pemasangan privat

Target yang diizinkan: `yusronMu77/agent-workflow`, visibility **PRIVATE**.
Candidate bukan final release. Status aktual terdapat di IMPLEMENTATION-PORTABLE-PLUGIN.md.
Tidak ada credential yang dibundel. Tidak ada lisensi open-source yang ditambahkan.

## Candidate pertama (2026-10-25)

Repo https://github.com/yusronMu77/agent-workflow dibuat dengan visibility PRIVATE, permission
ADMIN dan akun aktif yusronMu77. Remote origin tepat ke repo tersebut. Candidate pertama
`8af87460855ee817e4d3afe32087c12d226bba7c` dipush tanpa force; remote SHA dicocokkan ulang.
48 file produk direview; staging tepat sama dengan allowlist; Gitleaks 8.30.1 memindai commit
dengan nol temuan. `.agent-workflow`/auth/env/session DB/link tidak tracked. Ini catatan candidate,
bukan bukti final release. Qualification akhir dicatat dalam laporan implementasi.

## Komputer baru

Pasang Git, Python 3.10+ dan client pilihan melalui installer resminya. Gunakan login GitHub
native (misalnya `gh auth login`, lalu `gh auth setup-git`) atau SSH yang sudah diotorisasi.
Jangan menempelkan token ke chat, URL clone, file produk atau environment yang direkam.

```sh
gh api user --jq .login
gh repo view yusronMu77/agent-workflow --json nameWithOwner,visibility
gh repo clone yusronMu77/agent-workflow
cd agent-workflow
git checkout <validated-commit-or-tag>
python wf.py --version
python scripts/verify_distribution.py
python scripts/install.py install --client both --scope project --project /absolute/target --dry-run
python scripts/install.py install --client both --scope project --project /absolute/target
python wf.py --project /absolute/target init
python wf.py --project /absolute/target doctor
```

Gunakan path Windows absolut seperti `D:/work/target` pada Windows. Target harus sudah ada.
Untuk user scope gunakan `--scope user` tanpa `--project`; default profile adalah home user.
Setelah memasang skill, reload client atau mulai chat baru untuk discovery.

## Plugin melalui katalog privat

Hanya tambahkan katalog repo/ref yang dipercaya. Katalog menunjuk root paket dalam Git yang
berisi core dan skill, bukan folder dist. Distribusi remote memakai tracked files bersih;
untuk install lokal, extract ZIP allowlist ke staging kosong terlebih dahulu.

Codex CLI yang menyediakan perintah plugin:

```sh
codex plugin marketplace add yusronMu77/agent-workflow --ref <validated-commit-or-tag>
codex plugin add agent-workflow@agent-workflow-private --json
codex plugin list --marketplace agent-workflow-private --json
```

Claude Code:

```sh
claude plugin marketplace add yusronMu77/agent-workflow
claude plugin install agent-workflow@agent-workflow-private
claude plugin details agent-workflow
```

Dalam chat Claude versi yang mendukung:
`/plugin install agent-workflow --marketplace yusronMu77/agent-workflow`.
Private Git auth setiap client harus dibuktikan sendiri; login gh bukan bukti marketplace auth.
Untuk pin Claude gunakan URL Git dengan ref yang didukung versi client, atau checkout commit
terverifikasi lalu add katalog lokal dari checkout bersih. Jalur lokal itu bukan bukti private fetch.

Native smoke: buka project fixture kosong dalam client yang memasang plugin; pastikan skill
`agent-workflow` ditemukan dari cache plugin privat, baca skill tersebut, jalankan launcher dengan
`--project ABSOLUTE_TARGET doctor`, lalu begin/pack/evidence/review/verify satu task kecil.
Periksa DB hanya di target dan simpan versi client, SHA sumber katalog, path cache, output verify,
dan evidence discovery. Jangan menjalankan model kedua otomatis untuk memperoleh status parity.

## Candidate, CI, release

1. Periksa `git rev-parse --show-toplevel`, status, remote dan `gh api user`. Jika owner/remote,
   history atau visibility bertentangan, hentikan upload dan klarifikasi konflik.
2. Review daftar tracked/staged dan diff, jalankan tes, build dan audit credential. Build hanya
   mengambil allowlist. `.agent-workflow`, credential, env dan native session DB tetap lokal.
3. Untuk repo baru yang belum ada: `gh repo create yusronMu77/agent-workflow --private`.
   Verifikasi visibility kembali sebelum push. Jangan force push atau mengganti remote repo lain.
4. Push candidate, periksa `gh run list` dan `gh run view RUN --log-failed` pada SHA yang tepat.
   Tunggu Windows/Linux Python 3.10/3.15 lulus. Fresh clone private ref dan jalankan install/smoke.
5. F16 memerlukan private marketplace fetch/install dan native plugin chat smoke minimal satu client.
   Bila belum tersedia, simpan candidate/checkpoint; jangan membuat tag/release stabil.
6. Setelah semua acceptance inti lulus, build dari checkout bersih commit yang diuji, catat
   toolchain, bandingkan MANIFEST.sha256.json, lalu buat tag `v<VERSION>` pada SHA itu.
   Buat release memakai ZIP dan `.zip.sha256`. Verifikasi asset download checksum, tag SHA,
   visibility PRIVATE dan remote SHA setelah upload. Jangan mengganti tag/release diam-diam.

## Recovery

Jika create/push/release terputus dan hasilnya tidak diketahui, tandai task `unknown`, periksa
`gh repo view`, `git ls-remote`, daftar release/assets atau run CI sebelum retry. Catat bukti dan
gunakan `reconcile`; jangan mengulang external write dengan asumsi gagal.

Installer menyimpan `.agent-workflow-install-pending` bila terputus. Hentikan penulis lain,
baca target/action/version di marker dan periksa receipt/hash target serta direktori staging
di parent yang sama. Bila backup `previous` ada dan target hilang, pulihkan backup ke target
setelah memastikan path absolut berada dalam scope instalasi tersebut. Bila target sudah cocok
dengan receipt dan versi yang diinginkan, catat hasil sebagai sukses. Arsipkan staging/marker
sebagai evidence atau bersihkan hanya setelah rekonsiliasi eksplisit. Jangan menghapus DB target.
`both` diproses berurutan; kegagalan client kedua dapat meninggalkan client pertama sudah selesai.
Periksa masing-masing target, lalu lanjutkan hanya scope yang belum selesai setelah rekonsiliasi.
