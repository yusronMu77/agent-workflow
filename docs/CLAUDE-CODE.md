# Agent Workflow di Claude Code

Status per 2026-10-25 (Claude Code 2.1.302). Kit ini awalnya dibangun lewat Codex; halaman ini
mencatat apa yang sudah disetel untuk Claude Code dan apa yang belum.

## Yang berlaku di Claude Code

| Aspek | Perilaku |
|---|---|
| Discovery | Skill `agent-workflow` dimuat dari `.claude/skills/agent-workflow` (link ke kit) atau dari plugin `agent-workflow@agent-workflow-private`. |
| Pemanggilan | Eksplisit lewat `/agent-workflow`, atau implisit bila model menilai deskripsi cocok. Deskripsi sengaja menyebut kapan **tidak** dipakai (pertanyaan, lookup, edit satu langkah). Konsistensi pemanggilan implisit belum diukur. |
| Eksekusi | Chat Claude yang sedang berjalan menjadi executor; `wf.py` dipanggil lewat tool Bash. Tidak ada MCP atau model tambahan. |
| Pack | `CLAUDE.md` dimuat Claude Code sendiri, jadi `pack` mencantumkannya sebagai referensi (path, sha256, ukuran) dan tidak menyalin isinya. `AGENTS.md` tetap disalin karena Claude Code tidak memuatnya. |
| Deteksi host | `CLAUDECODE=1` pada terminal Claude Code memilih `--host claude`. Host lain atau variabel tidak ada → semua instruksi disalin (`none`). |
| Drift | Hash `CLAUDE.md` tetap diperiksa sebelum pack dan operasi lain. Perubahan di tengah task ditangani dengan `rebind`. |

Gunakan `pack --host none` bila chat dimulai di luar folder project (Claude Code tidak memuat
`CLAUDE.md` project itu) atau bila Anda tidak melihat isi `CLAUDE.md` di konteks.

Diukur 2026-10-25 pada salinan `CLAUDE.md` (22.620 B) dan `AGENTS.md` project research, task tanpa
source: pack 24.613 B (`--host none`) menjadi 2.289 B (`--host claude`). Angka ini ukuran byte
pack, bukan token native atau biaya; riwayat chat dan instruksi yang dimuat host tidak berubah.

## Pasang

```sh
python scripts/install.py install --client claude --scope project --project /absolute/target --dry-run
python scripts/install.py install --client claude --scope project --project /absolute/target
python scripts/probe_claude_skill.py --project /absolute/target
```

Mulai chat baru atau muat ulang klien agar skill ditemukan. Probe membaca file saja: memeriksa
frontmatter skill, versi manifest, launcher dari direktori lain, link skill project, dan
menjalankan `claude plugin validate` bila CLI `claude` tersedia. Probe tidak membuka chat dan
tidak memanggil model, jadi tidak membuktikan pemanggilan implisit.

## Pengaturan opsional (keputusan pengguna)

Kit tidak mengubah `settings.json` atau `CLAUDE.md` global. Dua penyesuaian berikut berguna,
dipasang sendiri bila diinginkan.

**1. Ingatkan task aktif setelah `/compact`.** State aman di SQLite, tetapi ringkasan percakapan
dapat melupakan ID task. Tambahkan ke compact instructions (`~/.claude/CLAUDE.md` atau CLAUDE.md
project):

```markdown
- ID task Agent Workflow yang aktif dan path project-nya. Setelah compact, jalankan
  `wf.py --project <path> resume <task>` sebelum melanjutkan.
```

**2. Izin untuk perintah baca-saja.** Setiap panggilan `wf.py` meminta persetujuan. Perintah yang
hanya membaca state bisa diizinkan di `.claude/settings.local.json`; perintah yang mengubah state
(`begin`, `pack`, `review`, `verify`, `rebind`, `cancel`, `revoke`, `backup`, `init`) tetap meminta
persetujuan. Pola berikut perlu dicoba dengan `/permissions` karena pencocokan path berspasi
bergantung pada versi klien:

```json
{
  "permissions": {
    "allow": [
      "Bash(python *wf.py --project * doctor)",
      "Bash(python *wf.py --project * list)",
      "Bash(python *wf.py --project * show *)",
      "Bash(python *wf.py --project * metrics *)"
    ]
  }
}
```

Kit tidak memasang izin lewat frontmatter skill (`allowed-tools`): task record tidak boleh
memberi izin tambahan.

## Batas

- Pemanggilan implisit, jalur plugin-install di chat Claude, dan penghematan token nyata belum diuji.
- Hook (SessionStart/Stop) dan integrasi subagent belum ada. Pack tidak dirancang untuk diteruskan
  ke subagent; satu task tetap dikerjakan satu chat.
- `capture --exit-code` adalah nilai yang Anda laporkan; kit tidak mengamati proses aslinya.
