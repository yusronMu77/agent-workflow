# Pemasangan terpisah untuk research

Kode kit berada di folder `agent-workflow`, sejajar dengan research.
Buka `agent-workflow.code-workspace` untuk mengembangkan kit; buka `research.code-workspace`
untuk pekerjaan riset. Research juga menampilkan kit sebagai folder workspace eksternal.

Dari research:

```powershell
python agent-setup/workflow.py doctor
python agent-setup/workflow.py list
python agent-setup/workflow.py feedback --collect
```

Root research sudah diinisialisasi dengan folder dari workspace-nya. State project tetap di
`research/.agent-workflow/`; state kit tersendiri tidak disatukan. Kedua project memiliki UUID
berbeda. Skill Codex dan Claude project research memakai link ke sumber yang sama di kit;
Codex root link tetap dikelola generator research. Probe struktural Claude dan
`claude plugin validate` telah lulus pada v1.1 dan pemeriksaan backlog v1.2;
hasil ini tidak membuktikan pemanggilan implisit atau kualitas sesi native end-to-end.

`agent-setup/workflow.json` adalah binding relatif. Bila kit dipindahkan lagi, ubah binding secara
sengaja lalu perbarui link yang dimiliki setup; jangan menimpa skill lain. Sources/provider/MCP,
hooks dan plugin existing research dipertahankan.

Pemindahan awal memverifikasi hash seluruh 36 file kit sebelum perubahan dokumentasi/link.
DB/checkpoint/artifact historis ikut dipindahkan, tanpa ditulis ulang agar seolah-olah sudah
divalidasi pada lokasi baru. Task historis `vscode-smoke` masih tercatat completed; resume-nya
dapat ditolak karena kumpulan aturan ancestor berubah setelah keluar dari research. Ini pemeriksaan
kesegaran yang disengaja. Gunakan task baru untuk pekerjaan di lokasi baru, jangan edit spec lama.

Project lain dapat memakai kit yang sama, tanpa menyalin kode ke dalam project target:

```powershell
python scripts/install_chat.py --project "D:/work/other-project" --claude
python wf.py --project "D:/work/other-project" init
```

Jalankan contoh tersebut dari folder kit dan sesuaikan target. `--claude` hanya memasang skill;
tidak mengubah provider/plugin/hooks dan tidak membuktikan parity runtime.
