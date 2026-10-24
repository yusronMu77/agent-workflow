# Implementasi portable plugin

Status (2026-10-25): M0–M4 lokal diimplementasikan. Candidate privat/CI/M6 belum selesai.
Final release belum qualified; tidak ada klaim parity native kedua client.

## M0 — baseline dan keputusan (2026-10-24)

- Root diverifikasi dari workspace; tidak ada Git lokal; state schema 1 dipertahankan.
- Python lokal 3.13.15, SQLite 3.53.1; 37 tes lulus dan dua copied-kit fixture lulus.
- GitHub CLI 2.97.0; akun aktif cocok dengan target yusronMu77; target belum ditemukan.
- Codex CLI 0.164.0-alpha.11.1 dan Claude Code 2.1.302 tersedia. Kehadiran binary bukan native smoke.
- Matriks qualification dipin Python 3.10 / 3.15, Windows / Linux. Python 3.15 stabil dipilih dari
  https://www.python.org/downloads/release/python-3150/ pada 2026-10-24.
- Adapter mengikuti https://developers.openai.com/plugins/build/plugins dan
  https://code.claude.com/docs/en/plugins-reference serta
  https://code.claude.com/docs/en/plugin-marketplaces (diperiksa 2026-10-24).
- Root package berisi core dan skill; katalog merujuk root tracked, bukan dist yang di-ignore.
- Direct install memakai salinan mandiri dengan receipt hash, tanpa junction dan tanpa mengubah rules.
- Tidak ada migrasi database, provider, hook, MCP atau model tambahan.

## Perubahan dan keputusan M1–M4

- VERSION 1.0.0 menjadi sumber versi. `wf.py --version` tidak memerlukan init. Doctor menampilkan
  versi dan ketersediaan Git/Codex/Claude/RTK tanpa memasang dependency opsional.
- `scripts/build_release.py` menghasilkan ZIP allowlist, checksum archive, dan MANIFEST.sha256.json.
  Metadata timestamp/mode dinormalisasi. Rebuild dibandingkan dengan path/hash isi, bukan hanya hash ZIP.
  Build menolak version drift, link/junction, state paths dan pola credential. Gitleaks digunakan
  sebagai pemeriksaan tambahan saat tersedia, bukan dependency runtime produk.
- `verify_distribution.py` membangun dari source fixture, menghilangkan akses pada path source
  fixture itu, lalu menjalankan launcher isolated Python dari cache Unicode/berspasi dan cwd asing.
  Task init sampai verify berhasil; tidak ada state di cache; implicit target ditolak.
- Direct installer baru membawa skill kanonis serta `_kit` core dalam satu folder skill, memakai
  receipt hash, staging dan exclusive pending marker. Tidak ada absolute source pointer/junction.
  Scope project/user dan codex/claude/both didukung; dry-run tidak menulis. Legacy installer link
  dipertahankan tanpa mengambil alih link/file existing.
- Update/rollback memilih checkout VERSION secara eksplisit. Tidak ada migrasi schema DB atau
  mutasi state/rules target. Interrupted mutation memblokir replay sampai rekonsiliasi.
  `both` berurutan, bukan transaksi atomik lintas client; check setiap scope setelah kegagalan.
- Root portable manifest dan compatibility overlays Claude/Codex memakai versi sama.
  Katalog tracked menunjuk `./`; tidak ada katalog remote yang menunjuk dist.
- Claude 2.1.302 `plugin validate` lulus. Ada warning yang disengaja: CLAUDE.md di root paket
  adalah instruksi pengembangan, bukan context plugin. Runtime memakai skill kanonis. Strict
  mode menolak warning ini; instruksi source tidak dihapus hanya untuk menghilangkan warning.
  Marketplace validation lulus tanpa warning setelah description ditambahkan.
- Instalasi lokal diuji dari ZIP staging bersih dengan CODEX_HOME/CLAUDE_CONFIG_DIR fixture,
  bukan dari root development atau profile aktif pengguna. Codex add/list melaporkan enabled
  versi 1.0.0; Claude details menemukan satu skill dan nol MCP/hook/agent tambahan.
- README sekarang memberi alur komputer baru tanpa research/C:/apps. Riwayat integrasi lama
  tetap berlabel sejarah, bukan prasyarat runtime. Tidak ada perubahan legal grant/LICENSE.

## Bukti lokal

45 tes lulus pada Windows/Python 3.13.15: 37 regression existing dan delapan acceptance distribusi.
`tests/test_distribution.py` mencakup reproducibility, version drift, semua client/scope,
dry-run/idempotensi, collision, modified files, rollback/state/rules preservation, interruption,
independent launcher, audit secret/private paths dan file receipt asing yang nested.
`verify_portability.py` lulus dua project document/code dengan multi-folder, UUID berbeda,
capture/retrieval, repair, feedback, memory/revocation dan backup.
`verify_distribution.py` lulus 48 file produk sebelum pembaruan laporan ini; jumlah akhir dicatat
bersama ref candidate. Benchmark verbose: raw 117035 byte, delivered 2408 byte; scoped:
raw 35 byte, delivered 874 byte. Native tokens/provider cost null. Ini proxy byte fixture,
bukan penghematan token/biaya provider atau total history sesi.

Log mentah dan checkpoint berada di `.agent-workflow/`, tidak masuk source/release.
Native Codex `skills/list` diperiksa ulang pada M4 tanpa thread/inference: ditemukan
`agent-workflow:agent-workflow`, enabled, dari skill kanonis root. Probe kini menerima nama
namespaced dan dapat mewajibkan exact cache path untuk qualification plugin privat nanti.
Gitleaks 8.30.1 memindai staging 48 file produk: tidak ada temuan. Scan awal archive extract
menandai SHA256 untuk docs/TOKEN-OPTIMIZATION.md dalam generated MANIFEST.sha256.json sebagai
generic-api-key; itu checksum isi file yang diverifikasi, bukan credential. Scan ulang exact
product sources tanpa generated checksum menghasilkan nol temuan, tanpa mematikan rule secret.
M3 awal melewati deadline saat jeda; record partial dipertahankan, continuation `portable-m3-cont`
mencatat bukti aktual. Tidak ada reset DB atau perubahan metadata untuk memalsukan qualification.

## Support level saat ini

| Jalur | Versi yang diperiksa | Bukti | Status |
|---|---|---|---|
| Codex VS Code / direct skill | CLI engine 0.164.0-alpha.11.1 | Current chat memakai core; historical discovery tersedia | Native direct path, bukan bukti private plugin |
| Codex CLI / local plugin | 0.164.0-alpha.11.1 | Marketplace add, plugin add/list dan cache mandiri | installed |
| Claude Code / local plugin | 2.1.302 | Validate, marketplace add, install dan details menemukan skill | installed |
| Private plugin / native chat | Pending M6 | Harus fetch privat, discovery cache dan task sampai verify | belum qualified |
| Windows / Python 3.13.15 | Lokal | 45 tes + portability + distribution | passed |
| Windows/Linux Python 3.10/3.15 | CI dipin | Menunggu candidate CI | pending |
| macOS | Tidak dijalankan | Prosedur generik tersedia | belum diuji |

## Acceptance F01–F18

| ID | Bukti / status |
|---|---|
| F01 | 37 regression existing lulus; schema 1 dan command exit semantics dipertahankan |
| F02 | VERSION, CLI --version dan tiga manifest; build menolak drift |
| F03 | Allowlist ZIP, audit, per-file manifest equality dan archive checksum; candidate review pending |
| F04 | verify_distribution cache task completed dengan source fixture unavailable |
| F05 | test_clients_scopes_dry_run_idempotence meliputi enam kombinasi |
| F06 | Collision/modified/nested foreign receipt tests lulus |
| F07 | Upgrade 1.0.0 → fixture 1.1.0 → 1.0.0; state/checkpoint/rules byte-identical |
| F08 | Uninstall fixture hanya owned files; modified skill ditolak |
| F09 | Claude actual validator dan local install/discovery lulus; native parity tidak diklaim |
| F10 | Codex actual marketplace parser/install/list lulus; direct fallback fixture lulus |
| F11 | Launcher explicit target, no cache state; multi-folder fixtures lulus |
| F12 | Existing outputs tests + portability + benchmark; repeat-read accounting, native usage null |
| F13 | Windows Unicode/spasi/foreign cwd lulus; Linux menunggu CI |
| F14 | Doctor/install tool availability; tanpa provider/model/hook install |
| F15 | Akun owner cocok; target belum ada pada M0; private upload menunggu M5 |
| F16 | Fresh private clone/install, CI dan private native plugin smoke menunggu M6; final tag ditahan |
| F17 | README, PRIVATE-PUBLISH, laporan ini mencakup setup/update/rollback/recovery/support |
| F18 | Checkpoint M0–M3-cont dan M4; interrupted install blocks replay, unknown external flow didokumentasikan |

## Cara pakai, upgrade dan recovery

Ikuti README untuk direct skill dan task lifecycle. Ikuti PRIVATE-PUBLISH.md untuk auth privat,
plugin install, release dan rekonsiliasi external write. Rollback menggunakan checkout lama,
`install.py update --version VERSION`; tidak ada download/self-updater atau DB rewrite.
Legacy link tidak diambil alih. File yang berubah manual harus diperiksa/dipertahankan dahulu.
Backup DB terpisah dari artifacts saat executor quiescent. Setelah rollback, resume tetap memeriksa
hash/policy; versi lama tidak otomatis dianggap qualified pada state atau lokasi baru.

## Sisa qualification

M5: reviewed tracked list/diff, secret scan staging, create PRIVATE repository, push candidate.
M6: periksa CI pada SHA tepat, fresh private clone/ref, private marketplace auth/install,
native discovery dan satu task current chat dari plugin cache. Hanya sesudah acceptance inti
lulus boleh membuat stable tag/release. UI discovery/new chat berbeda dari engine discovery;
tidak ada model/client kedua yang dipanggil untuk memperoleh badge.
