# Space Quest V — Voice App

Community web app + desktop tool for recording / collecting voice acting for
the SQ5 voice-acting mod.

## Two entry points

| | Where | What |
|---|---|---|
| **`web/`** | Deployed on Railway | Public site: visitors browse the line catalog, drop in audio files or record in-browser, and submit them to a shared community pool. The site owner picks which contribution is canonical for each line and downloads a zip of SCI audio36 patches ready to drop into the SQ5 game folder. |
| **`voicestudio/`** | Run locally | Desktop PyQt6 app (the original tool). Single-user record/edit pipeline that injects patches and rebuilds `RESOURCE.AUD` against a local SQ5 install. Useful for power-user editing offline. |

Both share `voicestudio/audio/sol.py`, `voicestudio/audio/loader.py`, and
`voicestudio/packer/` for the SCI audio36 conversion logic.  The web app
imports these directly — no duplication.

The line catalog (`voicestudio/sq5_lines.db`) is committed so both the web
and desktop entry points work without a manual extraction step.

## Quickstart

### Web (local)
```bash
pip install -r web/requirements.txt
SQ5_ADMIN_TOKEN=dev uvicorn web.app:app --reload --port 8000
# → http://localhost:8000
```

### Web (Railway)
See `web/README.md` for the env vars and persistent-volume setup.

### Desktop
Double-click `voicestudio/run.bat`, or:
```bash
pip install -r voicestudio/requirements.txt
python -m voicestudio.main
```

## License

For the voice mod itself, see the upstream mod repo and credit list.  Code in
this repository is provided as-is for community use.
