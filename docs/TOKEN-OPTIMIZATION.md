# Optimasi output tool

`capture` menyimpan salinan UTF-8 output tool dan memberi preview dengan batas byte.
`read-output` mengambil baris dari arsip tanpa menjalankan command lagi. `metrics` menghitung
byte view yang dikembalikan, termasuk retrieval berulang dan overhead JSON. Tidak ada runner
shell, proxy API, model, perubahan izin, atau pemasangan hook otomatis.

## Gunakan saat output besar

Utamakan query terarah dan output pendek. Untuk log besar, dari project target:

```powershell
# Jalankan command yang memang diizinkan melalui terminal native. Simpan hasil sebelum dibaca.
# Gunakan nama file baru; PowerShell 7 diperlukan pada contoh ini.
python -m unittest discover -s path/to/tests -v *> tool-check.txt
$checkExit = $LASTEXITCODE
python /path/to/kit/wf.py --project ROOT capture TASK --input project:tool-check.txt --exit-code $checkExit --max-bytes 6000
python /path/to/kit/wf.py --project ROOT read-output TASK KEY --find 'FAILED'
python /path/to/kit/wf.py --project ROOT read-output TASK KEY --start 120 --end 155
python /path/to/kit/wf.py --project ROOT metrics TASK
```

Ganti TASK dan KEY dengan ID task dan key yang dikembalikan capture. Pada project lain gunakan
`python /path/to/kit/wf.py --project ROOT ...` atau launcher skill. `--find` adalah teks literal,
case-insensitive. Batas JSON view 2.048–64.000 byte, default 6.000; raw maksimal 32 MB.
UTF-16/binary ditolak; ekspor ulang menjadi UTF-8 jika sumbernya memang teks.

Preview memprioritaskan baris diagnostik lalu awal/akhir file, dengan nomor baris asli.
Baris panjang dapat terpotong dan diberi `clipped: true`; `column_start` menunjukkan awal cuplikan.
Untuk baris panjang, `--find` mengambil jendela di sekitar kecocokan pertama. `complete: false` berarti ada isi yang
tidak ditampilkan; jangan menyimpulkan semua test lulus atau tidak ada error hanya dari preview.
Ambil section atau literal yang relevan. Exit code adalah nilai yang disampaikan pemanggil,
bukan bukti bahwa capture mengamati proses asli. Metadata ini tidak otomatis menandai review passed.

Arsip berada dalam `.agent-workflow/tasks/TASK/outputs/KEY/`: raw.txt dan manifest.json.
Hash raw dan metadata dicocokkan dengan event SQLite saat retrieval; metadata/raw yang berubah
ditolak. File tanpa event setelah crash tidak dipercaya. Mengubah file input asli tidak mengubah
arsip. State privat tidak untuk di-commit; hindari output yang memuat secret. Credential paths dan
file instruksi AGENTS.md/CLAUDE.md/SKILL.md tidak dapat dipakai melalui capture.

## Interpretasi pengukuran

`raw_payload_bytes` menjumlah ukuran setiap capture, bukan baseline replay seluruh percakapan.
`delivered_view_bytes` menjumlah JSON capture dan read-output yang sudah dikembalikan, termasuk
pengambilan ulang. `net_payload_bytes_proxy` adalah selisih keduanya; bisa negatif pada output
pendek. Ini perbandingan payload terhadap mengirim setiap capture lengkap sekali, bukan penghematan
token/sesi. Pemanggilan yang output terminalnya hilang tetap dihitung konservatif. Pembacaan langsung
raw file atau data di luar helper tidak terukur. `metrics` sendiri dan command workflow lain tidak
termasuk view bytes.

`pack_bytes` mencatat pack yang dibuat. Pack baru juga memisahkan `policy_bytes` dan evidence;
pack lama yang belum punya breakdown membuat agregat policy null. Aturan wajib tetap utuh.
Gunakan checkpoint/resume untuk melanjutkan state; hindari membuat pack baru tanpa kebutuhan
perubahan konteks. Helper tidak dapat menghapus instruksi/history yang telah masuk ke chat.

`native_tokens` dan `provider_cost` tetap null. Untuk mengukur biaya gunakan usage native/provider,
pisahkan cached input dan output/reasoning sesuai field yang tersedia, serta bandingkan kualitas,
latency dan retry pada tugas yang sama. `rtk gain` bukan pengganti usage tersebut.

Jalur `start`/`finish` menggabungkan operasi controller dan mengurangi pembacaan konteks berulang.
Instruksi recovery dipisahkan dari jalur normal; policy project tidak diringkas. Pengukuran sesi
native versi lama vs baru dan batas klaim ada di [WORKFLOW-EFFICIENCY.md](WORKFLOW-EFFICIENCY.md).

## Referensi instruksi yang sudah dimuat host

`pack` mengenali file instruksi yang dimuat host sendiri (`CLAUDE.md` untuk Claude Code,
`AGENTS.md` untuk Codex) dan mencantumkannya sebagai referensi: path, sha256 dan ukuran, tanpa
isi. Hash tetap diverifikasi saat pack dibuat dan pada setiap operasi berikutnya. Host dideteksi
dari `CLAUDECODE=1`; pilih eksplisit dengan `pack --host claude|codex|none`. Host tak dikenal
menyalin semua instruksi. `policy_bytes` menghitung isi yang disalin, `policy_ref_bytes` yang
hanya direferensikan; keduanya muncul di hasil `pack` dan `metrics`. Ini mengurangi byte pack,
bukan riwayat chat atau token native.

## RTK opsional

Jika RTK sudah tersedia, gunakan CLI eksplisit seperti `rtk git status`. Untuk test gagal atau
diff yang membutuhkan bukti rinci, simpan output native penuh dan gunakan capture/retrieval.
Menjalankan RTK dan command native dua kali hanya untuk perbandingan fixture read-only; jangan
melakukan replay command yang berefek demi mengukur savings. Output yang telah difilter RTK
tidak bisa direkonstruksi menjadi output native oleh capture.

RTK tidak menjadi dependency kit; lokasi instalasi, checksum dan qualification ditangani project
target. Integrasi auto-rewrite perlu verifikasi versi client, event aktual, sandbox dan hook existing.
Koneksi MCP berhasil tidak membuktikan hook berfungsi. Jangan menimpa instruksi kanonis.

## Reproduksi fixture

```powershell
python -m unittest discover -s tests -v
python scripts/verify_portability.py
python scripts/benchmark_outputs.py --output /path/to/new-report.json
```

Benchmark membuat fixture sementara, satu log verbose dan satu output yang sudah terarah, lalu
menguji capture + satu retrieval literal. Output report memakai exclusive-create. Kualitas tugas
agent end-to-end, statistik multi-run dan biaya provider belum diukur oleh fixture ini.
