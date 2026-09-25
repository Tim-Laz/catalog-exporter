# CLAUDE.md — notes for an AI assistant working in this repo

This repo exports everything an online 3D property catalog shows: renders and masks,
floor plans, unique 360 tours, top views, amenities and an apartments table. The
catalog is given by its viewer link, and every host is derived from that link.

- **Helping someone run it:** follow the [README](README.md) steps with them. The run is
  `python3 export.py "<catalog link>"`, and a finished export is re-checked with
  `python3 verify.py`.
- **Changing the code:** read [docs/TECHNICAL.md](docs/TECHNICAL.md) first. It holds
  the verified data model, the output conventions, the checks and the robustness
  rules to keep.
- **After a change**, run a full export and make sure `verify.py` passes on **both
  Windows and macOS**. The client runs Windows. On macOS, running with
  `LC_ALL=C PYTHONCOERCECLOCALE=0 python3 -X utf8=0` mimics Windows' non-UTF-8 defaults.
- **Data problems:** the steps report problems in the catalog's own data. Don't "fix"
  them silently in the output; report them.
- **Keep the repo generic:** no provider or client names in code, docs or commit
  messages. The only project-specific value is `DEFAULT_LINK` in `export.py`, so the
  client can run `py export.py` with nothing to paste. Everything else is derived from
  the link: hosts from its domain, slugs from the API.
- `output/` and `.cache/` are git-ignored. Never commit exported media.
- A run downloads from the catalog's API, its CDN and `firebasestorage.googleapis.com`.
  If a permission prompt stops it, ask the user to approve it, or to run the command
  themselves.
