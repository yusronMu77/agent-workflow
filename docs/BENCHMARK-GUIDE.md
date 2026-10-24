# Benchmark Agent Workflow dan feedback

## Tujuan dan status

Pisahkan pengujian correctness controller, pengukuran payload, evaluasi perilaku agent, dan evaluasi integrasi MCP. Tes hijau tidak membuktikan pemilihan tool tepat. Payload byte bukan biaya/token provider. Panduan ini menggunakan CLI yang sudah tersedia; tidak menambahkan runner model atau menginstal MCP/hook.

## 1. Pemeriksaan deterministik

Dari root kit, gunakan terminal native:

```powershell
python -m unittest discover -s tests -v
python scripts/verify_portability.py
python scripts/verify_distribution.py
python scripts/probe_codex_skill.py --project C:/absolute/target
```

Probe terakhir memeriksa discovery skill tanpa inference. Untuk release, semua tes correctness harus lulus. Jangan menafsirkan probe sebagai uji implicit invocation. Tiga pemeriksaan pertama telah lulus pada audit sebelumnya: 48 tes, portable document/code, distribusi/cache.

## 2. Benchmark payload yang tersedia

Gunakan path laporan baru karena script menolak overwrite:

```powershell
python scripts/benchmark_outputs.py --output outputs/benchmark-run-001.json
```

Hasil aktual 25 Oktober 2026, `outputs/benchmark-20261025.json`:

| Kasus | Raw byte | Capture + read JSON byte | Interpretasi |
| --- | ---: | ---: | --- |
| verbose | 117035 | 2408 | Payload view lebih kecil, diagnostik target tetap ditemukan |
| scoped | 35 | 874 | Overhead lebih besar; kembalikan output pendek langsung |

Angka tidak mencakup seluruh percakapan, pemanggilan workflow, waktu agent atau billing. Script saat ini hanya membuat fixture, capture, read, dan metrics; tidak menjalankan verify sehingga tidak otomatis menghasilkan feedback proposal.

## 3. Benchmark kualitas agent A/B

Siapkan snapshot proyek identik dan sesi chat baru untuk setiap trial. Gunakan model, effort, versi client, izin, tool catalog, batas waktu dan prompt tugas yang sama; catat jika tidak bisa dikendalikan. Jangan jalankan trial A dan B dalam percakapan yang sama karena history dapat mencemari hasil.

- A: native chat tanpa Agent Workflow; tools tersedia sama.
- B: native chat dengan Agent Workflow; tools tersedia sama.
- C opsional: B dengan MCP langsung dibandingkan B dengan gateway MetaMCP. Ubah hanya koneksi yang diuji; samakan backend/tool exposure dan tandai perbedaannya.

Untuk pilot, gunakan 10 skenario, masing-masing minimal 3 trial per arm (60 trial untuk A/B). Ini ambang praktis awal, bukan jaminan signifikansi statistik. Mulai dengan beberapa trial manual bila biaya/waktu terbatas; jangan mengklaim hasil final dari satu trial. Acak urutan A/B dan gunakan rubrik yang sama, idealnya penilai tidak mengetahui arm.

| Skenario | Hasil yang dinilai |
| --- | --- |
| Bug kode dengan tes yang sudah ada | Fix benar, tes lulus, tidak merusak perilaku lain |
| Ringkasan dokumen bersumber | Fakta dan referensi benar |
| Log besar dengan error di bagian tengah/akhir | Error ditemukan tanpa kehilangan konteks penting |
| Output pendek | Tidak menambah capture/retrieval tanpa manfaat |
| Baris JSON/Unicode panjang | Seluruh bagian yang diperlukan terbaca |
| Handoff/resume | State pulih dan instruksi tetap dipatuhi |
| Source/policy berubah | Drift terdeteksi; agent meninjau sebelum lanjut |
| Write timeout tidak pasti | Tidak replay; reconcile sebelum melanjutkan |
| Dua tool tampak relevan | Tool dipilih sesuai kemampuan yang telah ditentukan rubrik |
| Tool hilang/auth gagal | Gap dijelaskan, fallback tepat; tidak mengklaim sukses |

Skenario fault injection memakai fixture terisolasi, bukan write ke layanan nyata. Untuk pemilihan skill, tambahkan prompt tanpa tag, dengan tag, dan pertanyaan sederhana yang seharusnya tidak membuat task; nilai false positive/negative dari tindakan aktual.

Catat per trial: run_id, arm, scenario_id, commit/hash snapshot, model/effort/client, catalog tool, waktu mulai/akhir, status sukses, hasil rubrik, jumlah tool calls/retry, intervensi pengguna, pelanggaran instruksi, serta path bukti. Usage native/provider dicatat hanya jika benar-benar tersedia; lainnya null. Simpan expected capabilities dan tool yang dipilih agar routing accuracy dapat dihitung; jangan menilai hanya dari jawaban akhir.

Agregasi: success rate, routing accuracy, invocation precision/recall, jumlah intervensi, latency median/p95 (p95 eksploratif pada sampel kecil), retry, serta usage nyata jika tersedia. Laporkan nilai absolut dan selisih per skenario, bukan hanya rata-rata gabungan. Tentukan acceptance sebelum melihat hasil; keselamatan unknown-write tidak boleh dikompensasi oleh skor kecepatan.

## 4. Menyalurkan hasil menjadi feedback dengan CLI existing

Simpan laporan trial/ringkasan dalam proyek benchmark terpisah yang telah di-init. Untuk task baru, deklarasikan review semantik `benchmark_acceptance`. Contoh berikut memakai placeholder ROOT/TASK; ganti dengan path absolut dan ID unik. Output harus benar-benar dibuat dan diperiksa sebelum review.

```powershell
python skills/agent-workflow/scripts/wf.py --project ROOT begin TASK --request "Nilai hasil benchmark terhadap acceptance yang ditetapkan sebelum trial" --workflow research --output project:results/run.json --check json --review-required benchmark_acceptance
python skills/agent-workflow/scripts/wf.py --project ROOT pack TASK
# Baca context_path. Jalankan trial; simpan hasil aktual ke results/run.json.
# Tulis results/evidence.txt berisi command, outcome, ukuran/waktu, rubrik dan referensi trace.
python skills/agent-workflow/scripts/wf.py --project ROOT review TASK --criterion final_review --result passed --evidence project:results/evidence.txt --note "Laporan dan bukti aktual diperiksa"
# Pilih passed atau failed berdasarkan hasil sebenarnya, bukan agar collector membuat proposal.
python skills/agent-workflow/scripts/wf.py --project ROOT review TASK --criterion benchmark_acceptance --result failed --evidence project:results/evidence.txt --note "Jelaskan kriteria yang benar-benar gagal dan nilai terukurnya"
python skills/agent-workflow/scripts/wf.py --project ROOT verify TASK
python skills/agent-workflow/scripts/wf.py --project ROOT feedback --collect
```

Contoh memperlihatkan cabang acceptance gagal. Jika lolos, gunakan passed. `verify` dengan kegagalan menghasilkan validation_failed → proposal dan exit 3; ini hasil benchmark, bukan error runner yang harus disembunyikan. Jangan ubah output/acceptance untuk membuat hasil tampak lulus. Hasil negatif yang sengaja diuji, misalnya menolak replay write, adalah lulus bila sesuai rubrik.

Proposal existing generik dan membawa payload event serta task_id. Kumpulkan hasil lengkap, root cause, kandidat perbaikan dan bukti dalam laporan/task terkait. Collector tidak menilai benchmark JSON sendiri; capture --exit-code 1 juga tidak otomatis membuat validation_failed. Jangan menulis langsung ke database atau menyuntik event palsu.

Demonstrasi aktual dilakukan di fixture di direktori sementara (`workflow-output-benchmark-*`): task efficiency-feedback menilai syarat delivered < raw pada scoped fixture. Nilai 874 > 35 membuat payload_efficiency failed, final_review passed karena pengukuran benar; verify menghasilkan repair_needed dan 1 proposal. Collect ulang menghasilkan 0 proposal baru, total tetap 1. Kegagalan ini tidak berarti capture buruk untuk log besar. Fixture dipertahankan untuk inspeksi; bukan hasil agent A/B.

Untuk perbaikan berikutnya, buat task baru yang mereferensikan bukti benchmark, uji perubahan pada dataset pengembangan, lalu ulangi trial pada skenario holdout. CLI belum memiliki importer benchmark, lifecycle proposal accept/reject/activate, atau scheduler riset mandiri. Langkah diagnosis dan pengerjaan tetap dilakukan chat aktif.

## 5. MetaMCP dan adapter lain

Implementasi v1 sengaja memusatkan scope pada native chat + Python stdlib + state portable. PRD-PORTABLE-PLUGIN.md menempatkan MCP wrapper/gateway dan global RTK hook di luar scope; ARCHITECTURE.md mencatat lifecycle MCP dan dispatch provider sebagai backlog. Jadi belum diimplementasikan bukan bukti tidak berguna, dan juga bukan bukti komponen sudah ada hanya karena namanya muncul dalam dokumen historis.

MetaMCP yang dirujuk di sini adalah https://github.com/metatool-ai/metamcp, diverifikasi 25 Oktober 2026. README upstream menjelaskan proxy/aggregator, namespace, endpoint dan inspector. Sebagian pencarian tool dan middleware masih ditandai coming soon; pin ref dan uji kemampuan aktual. Jangan menyamakan gateway dengan agent peneliti atau loop pembelajaran.

| Komponen | Kapan masuk akal | Yang tetap harus dibuat dalam Agent Workflow |
| --- | --- | --- |
| MetaMCP | Banyak server/client membutuhkan namespace dan endpoint terpusat | Adapter, health check, provenance keputusan routing, evaluasi direct vs gateway |
| Memory eksternal | Perlu lintas proyek, pencarian semantik, atau knowledge service | Scope, freshness, provenance, retrieval eval |
| RTK/hook | Banyak command berulang dan output berlebih terukur | Kualifikasi versi/event, raw evidence, uji izin/fallback |
| Dispatcher agent/provider | Perlu delegasi atau eksekusi di luar chat saat ini | Executor, lifecycle/cancel, biaya, izin, reconciliation |

Prioritas yang disarankan: benchmark kualitas + pencatatan pemilihan tool → feedback terstruktur dan diagnosis → adapter opsional MetaMCP/direct → uji kandidat terisolasi → aktivasi dan rollback berbukti. Memasang gateway saja tidak memenuhi kebutuhan feedback yang meneliti MCP/skill/hook sendiri. Core portable dapat tetap bekerja tanpa gateway.
