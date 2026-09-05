# Build Logs Archive

This folder collects historical build/install transcripts that previously
landed at the FANS-C repository root (e.g. `build_err.txt`, `inno_log.txt`,
`pyinstaller_log.txt`).

Contents are **not** committed (see `.gitignore`); the folder itself is
kept so build scripts and developers always have a stable place to drop
transcripts when running:

```powershell
powershell -ExecutionPolicy Bypass -File .\dev\build_exe.ps1 -Clean -SkipCollectStatic
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "C:\FANS\dev\installer\fans_c.iss"
```

Keep the latest transcript here while debugging a failed installer build,
then delete or rotate before committing other changes. The folder is
**never** included in the installer payload.

Related files at the top level (now ignored via `.gitignore`):
- `build_err.txt`, `build_log.txt`, `build_out.txt`
- `build_stderr.txt`, `build_stdout.txt`
- `inno_log.txt`, `iscc_out.txt`, `pyinstaller_log.txt`
