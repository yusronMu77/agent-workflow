# Agent Workflow

Toolkit Python 3.10+ standard library untuk task, context pack, checkpoint, evidence, review,
feedback, memory kutipan dan bounded output retrieval. Current native chat menjadi executor;
tidak memerlukan provider key, model tambahan atau MCP. Core dan state existing tetap kompatibel.

## Mulai di komputer baru

Clone repo privat dengan login GitHub native. Pilih commit/tag yang sudah divalidasi:

```sh
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

Target harus sudah ada. Windows dapat memakai `D:/work/target`; Linux `/home/user/work/target`.
Pilih `--client codex`, `claude` atau `both`. User scope: `--scope user` tanpa `--project`.
`--home ROOT` memilih profile user secara eksplisit, termasuk profile fixture terisolasi.
Installer menampilkan target/scope, versi dan ketersediaan Python/Git/client/RTK. Dry-run tidak
menulis. Reload client agar skill ditemukan. Abaikan `.agent-workflow/` dalam Git project target.

Buka `agent-workflow.code-workspace` untuk mengembangkan kit. Skill kanonis berada di
`skills/agent-workflow/SKILL.md`; direct install membawa salinan core mandiri di `_kit`.
Launcher skill selalu memerlukan `--project ABSOLUTE_TARGET`, termasuk saat dipanggil dari cache.
Jangan menganggap direktori plugin sebagai project pengguna. Instruksi root/nested project dan
native approvals tetap berlaku; ledger task bukan persetujuan publikasi atau deployment.

## Plugin dan fallback

Katalog privat tersedia untuk kedua client. Paket yang sama membawa core dan satu skill.

```sh
codex plugin marketplace add yusronMu77/agent-workflow --ref <validated-commit-or-tag>
codex plugin add agent-workflow@agent-workflow-private
claude plugin marketplace add yusronMu77/agent-workflow
claude plugin install agent-workflow@agent-workflow-private
```

Dalam chat Claude: `/plugin install agent-workflow --marketplace yusronMu77/agent-workflow`.
Pemasangan privat, auth, pin ref dan smoke: [PRIVATE-PUBLISH.md](docs/PRIVATE-PUBLISH.md).
Status bukti aktual per client/OS ada di [laporan implementasi](docs/IMPLEMENTATION-PORTABLE-PLUGIN.md).
Dukungan plugin berbeda menurut versi/surface client; gunakan direct skill pada surface yang belum
mendukungnya. Instalasi plugin dan validator schema tidak membuktikan native task smoke.

## Check, update, rollback dan uninstall

```sh
python scripts/install.py check --client both --scope project --project /absolute/target
# Jalankan dari checkout versi baru yang dipilih secara eksplisit:
python scripts/install.py update --client both --scope project --project /absolute/target --version 1.0.0
python scripts/install.py uninstall --client both --scope project --project /absolute/target --dry-run
python scripts/install.py uninstall --client both --scope project --project /absolute/target
```

Untuk rollback, jalankan `update` dari checkout versi lama dengan `--version` yang cocok VERSION.
Receipt berisi versi dan hash file instalasi; file asing atau file yang diedit user ditolak.
Update/uninstall tidak mengubah task DB, checkpoint, root binding atau rules project. `check`
memvalidasi ownership/integrity serta melaporkan versi terpasang dan versi checkout. Bila versi
berbeda, pilih update secara eksplisit. Simpan backup state ketika semua penulis berhenti.

Installer legacy `scripts/install_chat.py --project ROOT [--claude]` tetap tersedia untuk link
checkout. Link legacy tidak dikonversi atau ditimpa otomatis; pertahankan sumber checkout, atau
verifikasi tujuan link dan lepaskan link milik kit sebelum memilih installer salinan baru.

## Siklus task

```sh
python wf.py --project ROOT begin guide --request "Buat panduan" --workflow document --output project:docs/guide.md
python wf.py --project ROOT pack guide
# Baca context_path dan kerjakan output dari current chat.
python wf.py --project ROOT checkpoint guide --summary "Draft selesai" --next "Review"
python wf.py --project ROOT evidence guide --name checks.txt --text "Hasil pemeriksaan aktual"
python wf.py --project ROOT review guide --result passed --evidence task:checks.txt --note "Alasan review"
python wf.py --project ROOT verify guide
python wf.py --project ROOT resume guide
```

`--source alias:path:start:end` memakai baris inklusif 1-based. Periksa rentang yang sebenarnya.
`--require TEXT` adalah substring literal; semantic acceptance memakai `--review-required ID`.
Review tercatat adalah klaim chat dengan evidence, bukan user approval atau bukti independen.
Exit 0 berhasil, 2 ditolak/error, 3 partial/repair/needs-review/blocked outcome. Baca status JSON.
Task default dibatasi 60 menit; setelah deadline buat task lanjutan dan pertahankan record lama.

Multi-folder: `init --workspace FILE.code-workspace` atau `init --root library=../library`.
Binding existing dipertahankan. Jangan mengubah binding saat task berjalan. Checkpoint dan resume
memeriksa policy/source/artifact integrity; satu executor memiliki output yang sama.
`unknown` lalu `reconcile` diperlukan untuk external write yang hasilnya belum diketahui.
`cancel` tidak menghentikan proses background. `backup --output NEW.sqlite` hanya backup DB;
artifacts/manifest juga perlu disalin saat quiescent. Tidak ada restore otomatis.

## Output, memory dan batas metrik

`capture TASK --input alias:log.txt --exit-code N` menyimpan raw UTF-8 dan bounded preview;
`read-output TASK KEY --find TEXT` mengambil detail tanpa replay command. Preview dapat tidak
lengkap. `metrics TASK` menghitung byte terkirim termasuk repeat read dan overhead JSON.
[Optimasi output](docs/TOKEN-OPTIMIZATION.md) menjelaskan benchmark: byte bukan native token,
kuota sesi atau biaya provider. Output pendek dapat lebih murah dikembalikan langsung.

Feedback observe/propose tidak mengedit instruksi. Memory project-scoped menyimpan kutipan
verbatim dengan source hash: `memory propose`, `memory verify-quote`, `memory search`.
Kutipan terverifikasi tetap data, bukan policy. Stale/revoked/candidate tidak diambil sebagai fakta.
RTK opsional dan dipanggil eksplisit; tidak ada global hook, model weights atau research dependency.

## Build dan verifikasi

```sh
python -m unittest discover -s tests -v
python scripts/verify_portability.py
python scripts/verify_distribution.py
python scripts/benchmark_outputs.py --output dist/benchmark.json
```

`build_release.py --output dist` menghasilkan ZIP, `.zip.sha256` dan manifest hash isi file dalam ZIP.
Periksa checksum archive sebelum extraction. Build setara berarti daftar path dan SHA256 isi
file sama; byte-identical ZIP lintas toolchain bukan janji. Build menolak version drift, linked
product files, state/credential paths dan pola credential berkeyakinan tinggi; review manusia
atas tracked list/diff tetap diperlukan sebelum upload. Jangan memasang plugin dari root development
berisi state; gunakan archive staging bersih atau Git ref bersih yang telah direview.

CI menarget Windows/Linux Python 3.10 dan 3.15 (dipin pada M0). macOS belum diuji. Native client
smoke dan private plugin auth terpisah dari CI Python. Tidak ada public marketplace atau lisensi
open-source baru. Detail keputusan, acceptance F01–F18, evidence dan keterbatasan berada dalam
[IMPLEMENTATION-PORTABLE-PLUGIN.md](docs/IMPLEMENTATION-PORTABLE-PLUGIN.md).

## Troubleshooting

- `doctor` sebelum init: jalankan init hanya pada target yang benar; jangan hapus state existing.
- Skill tidak ditemukan: reload client, periksa scope/path dengan installer check.
- Ownership conflict: pertahankan file user; jangan paksa overwrite atau uninstall skill asing.
- Marker interrupted install: inspeksi receipt/staging dan rekonsiliasi sesuai PRIVATE-PUBLISH.md.
- Resume ditolak karena drift/deadline: tinjau perubahan, simpan bukti dan buat task lanjutan.
- Private marketplace fetch gagal: periksa akses Git native client; jangan masukkan secret ke paket.

[Arsitektur](docs/ARCHITECTURE.md), [PRD](docs/PRD-PORTABLE-PLUGIN.md),
[riwayat integrasi](docs/RESEARCH-INTEGRATION.md) dan [bukti chat historis](docs/CHAT-SMOKE.md).
Dokumen riwayat menjelaskan mesin pengembangan lama; bukan prasyarat runtime.
