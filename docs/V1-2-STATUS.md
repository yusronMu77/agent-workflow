# Agent Workflow v1.2: hasil audit dan implementasi lokal

Kerangka migrasi tersedia tanpa mengubah skema produksi 1. Pilot berat tiga skenario sudah
dijalankan dalam enam sesi native terpisah: seluruh artefak lulus, acceptance gabungan gagal
karena satu pembacaan log inline. Review pack subagent dan lifecycle feedback sudah tersedia.
Hasil pilot lama: [V1-2-HEAVY-PILOT.md](V1-2-HEAVY-PILOT.md). Verifikasi lanjutan dan bug yang ditemukan: [V1-2-QUALIFICATION.md](V1-2-QUALIFICATION.md).
Dirilis sebagai v1.2.0 pada 2026-10-28 bersama implementasi roadmap ([ROADMAP-IMPLEMENTATION.md](ROADMAP-IMPLEMENTATION.md)).

## Verifikasi sebelum implementasi

Baseline lokal: 64 tes lulus; doctor kit dan research berstatus ok. Kode sebelumnya hanya
menolak skema lebih baru. Research memiliki 0 proposal feedback nyata dan 0 pending event.
Catatan lama tentang rilis di PRD v1.1 sudah usang; audit lokal tidak melakukan push atau
mengubah visibilitas repository.

| Backlog resmi | Hasil pemeriksaan | Pengerjaan dan status |
| --- | --- | --- |
| Pilot A/B 3–5 skenario | Sebelum iterasi belum ada sesi terpisah | **Selesai dijalankan/dinilai**: enam sesi desain 3D, artefak 6/6 lulus, acceptance negatif dicatat |
| Kerangka migrasi + tes upgrade | Hanya guard versi, belum ada jalur upgrade | Registry langkah berurutan dalam transaksi `BEGIN IMMEDIATE`; tes upgrade, idempotensi, rollback DDL/data/event, identitas dan versi. **Diimplementasi** |
| Hook SessionStart/Stop opsional | Kontrol UNKNOWN; hook memulihkan ID/state pada startup/resume dan memberi satu Stop reminder | **Terkualifikasi pada fixture Windows**, opt-in session saja; native /compact memicu SessionStart:compact dengan sukses, tidak dipasang global |
| Jalur subagent untuk pack | Pilot membutuhkan reviewer visual terpisah | **Diimplementasi dan dipakai**: pack --audience subagent-review; tanpa dispatch/transfer ownership |
| Evaluasi accept/reject feedback | Kalibrasi native menghasilkan proposal validation_failed nyata | **Diimplementasi**: keputusan dengan bukti hash, immutable/idempotent, tanpa auto-apply; accept diuji pada proposal nyata |

## Jalankan fixture ringan dari sesi baru

Bagian ini mempertahankan petunjuk suite smoke. Suite berat dan hasil aktual ada di V1-2-HEAVY-PILOT.md.

Pilih direktori baru di luar workspace agent. Jalankan dari checkout kit:

```powershell
python scripts/pilot_ab.py prepare --output C:/Temp/agent-workflow-pilot-v12 --seed 12
```

`plan.json` menentukan urutan enam trial. Buka setiap direktori trial sebagai project dalam sesi
chat native baru, gunakan prompt dari file `<trial_id>.prompt.txt` di direktori induknya.
Samakan model, effort, versi client, izin, tool catalog dan budget. Arm A memakai tools native;
arm B memakai kit snapshot yang juga tersedia pada arm A. Jangan menjalankan dua trial dalam
satu chat. Jangan memakai percakapan pengembangan ini sebagai salah satu trial.

Skenario: bug dengan tes existing, diagnostik pendek, dan log panjang dengan error tengah/akhir.
Rubrik ditulis sebelum trial di plan. Salin `results.template.json` menjadi `results.json`;
isi hasil aktual, session ID berbeda, metadata client/model dan hash katalog tools yang sama,
waktu, jumlah tool/retry/intervensi/pelanggaran, serta path relatif bukti di direktori trial.
`success` adalah hasil penilaian rubrik; token native tetap null bila tidak tersedia.

```powershell
python scripts/pilot_ab.py summarize --plan C:/Temp/agent-workflow-pilot-v12/plan.json --results C:/Temp/agent-workflow-pilot-v12/results.json --output C:/Temp/agent-workflow-pilot-v12/summary.json
```

Template kosong menghasilkan `pending_fresh_sessions`, exit 3. Bukti hilang, sesi dipakai ulang,
metadata berbeda, atau angka tidak valid ditolak (exit 2). Hasil lengkap bersifat eksploratif;
acceptance gagal menghasilkan exit 3. Validator memeriksa struktur dan hash bukti; penilai tetap
harus memeriksa isi bukti dan kebenaran klaim sesi. Tiga pasangan bukan bukti signifikansi,
penghematan token atau keunggulan kausal. Hasil sintetis unit test bukan hasil pilot.

## Tambahkan migrasi hanya saat skema benar-benar berubah

`workflow_core/state.py` menyediakan `MIGRATIONS`: key versi asal, callable yang menaikkan
tepat satu versi dengan `db.execute()`. Tambahkan langkah dan naikkan `SCHEMA_VERSION` bersama
tes fixture versi lama. Jangan memakai `executescript()`, commit manual atau operasi filesystem
di callable. Semua langkah, metadata versi dan event upgrade harus berhasil dalam satu transaksi.

Inisialisasi database kosong membuat skema dasar 1 kemudian menjalankan registry. Database
berversi lebih baru, identitas berbeda, metadata rusak atau langkah migrasi hilang ditolak.
Database existing tidak lagi mendapat tabel yang hilang melalui `CREATE IF NOT EXISTS` sebelum
guard versi. Belum ada migrasi produk nyata karena belum ada perubahan skema yang dibutuhkan.
Backup sebelum upgrade produk dengan CLI `backup`; kerangka ini tidak membuat backup otomatis.

## Batas penerimaan

Verifikasi lokal: 79 tes lulus (`outputs/v12-final-tests.txt`, exit 0); portability document/code
lulus (`outputs/v12-portability.json`, exit 0); distribusi 58 file dan manifest reproducible lulus
(`outputs/v12-distribution.json`, exit 0). Probe Claude dan `claude plugin validate` lulus
(`outputs/v12-claude-probe.json`, exit 0); discovery Codex lulus tanpa inference
(`outputs/v12-codex-probe.json`, exit 0). Regresi akhir setelah review metadata juga 79 tes lulus
(`outputs/v12-final-reviewed-tests.txt`, exit 0). Doctor kit/research tetap ok, skema 1.
Delapan tes migrasi menggunakan langkah sintetis
1→2/1→3; tujuh tes pilot menggunakan catatan sintetis, tanpa menjalankan inference.

Fixture pilot aktual disiapkan di `C:/Temp/agent-workflow-pilot-v12-verified/plan.json`.
Ringkasan template kosong di `outputs/v12-pilot-pending.json` menghasilkan
`pending_fresh_sessions` dan exit 3 sesuai harapan. Path ini artefak lokal, bukan dependensi kit.

Iterasi lanjutan: 95 tes, portability, distribusi 66 file dan probe lulus. Enam sesi pilot berat
sudah dinilai; satu pelanggaran panduan output membuat acceptance false. Review pack dan
lifecycle feedback tersedia sesuai bukti kebutuhan/penggunaan. Hook reminder kemudian diuji terisolasi pada startup/resume/compact/Stop;
core tidak mengintersep semua tools native. Saat iterasi ini ditulis belum ada commit, push, atau rilis baru; sejak itu snapshot v1.2 di-commit sebagai `74fe50c` (226 tes lulus per 2026-10-27), kemudian di-push dan dirilis sebagai v1.2.0 pada 2026-10-28.

Iterasi kualifikasi lanjutan: 103 tes lulus pada Windows Python 3.10 dan 3.13; sepuluh native log holdout/repetisi lulus. Race staging antarproses ditemukan dan diperbaiki. Runtime plugin Claude dan izin path berspasi teruji. Hasil serta batas macOS/compact/precision dan biaya provider ada di V1-2-QUALIFICATION.md.

Iterasi efisiensi menambahkan `start`/`finish`, konteks langsung dan skill bertahap tanpa
memotong policy. Enam sesi pembanding dengan acceptance semantik identik lulus;
median input+output turun 29,7% dibanding workflow lama pada satu tugas ekstraksi log.
Enam percobaan awal dengan checker keliru tetap `repair_needed` dan dikecualikan.
Detail, bukti serta batas generalisasi ada di [WORKFLOW-EFFICIENCY.md](WORKFLOW-EFFICIENCY.md).
