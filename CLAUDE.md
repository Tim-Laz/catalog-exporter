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
- **After a change**, run a full export and make sure `verify.py` passes.
- **Data problems:** the steps report problems in the catalog's own data. Don't "fix"
  them silently in the output; report them.
- **Keep the repo generic:** no provider names, client names, hosts or project ids in
  code, docs or commit messages. Everything project-specific comes from the link.
- `output/` and `.cache/` are git-ignored. Never commit exported media.
- A run downloads from the catalog's API, its CDN and `firebasestorage.googleapis.com`.
  If a permission prompt stops it, ask the user to approve it, or to run the command
  themselves.
