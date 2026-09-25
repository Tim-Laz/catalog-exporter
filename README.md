# Catalog Exporter

This tool makes a complete copy of what your online 3D property catalog shows:

- the building renders
- the floor plans
- the apartment and amenity 360° tours
- the apartment top views
- a table of all apartments

It runs on your computer. At the end you send us one folder.

It only **reads** from the catalog. It does not log in, upload or change anything
there.

You will need the **command we sent you**. It contains the link to your catalog.

Instructions below are for **Windows 10 / 11**. On a Mac, see [On a Mac](#on-a-mac).

---

## What you need

- A Windows 10 or 11 computer.
- About **20 minutes** the first time, most of it waiting for installs.
- About **1 GB** of free disk space.
- An internet connection.

Commands are typed into **PowerShell**. To open it, click **Start**, type
**PowerShell** and open **Windows PowerShell**. To run a command, paste it
(right-click pastes) and press **Enter**.

---

## Step 1 — Install the tools (once)

In PowerShell, run these two commands one after the other:

```powershell
winget install -e --id Python.Python.3.13
winget install -e --id ImageMagick.ImageMagick
```

If it asks you to accept terms, type **Y** and press Enter. If Windows asks
*"Do you want to allow this app to make changes"*, click **Yes**.

When both are finished, **close PowerShell**. The new tools only become available
in a new window.

> If PowerShell says `winget` is not recognized, install both tools by hand instead:
> - **Python** from [python.org/downloads](https://www.python.org/downloads/). On the
>   first screen of the installer, tick **"Add python.exe to PATH"**.
> - **ImageMagick** from [imagemagick.org/script/download.php#windows](https://imagemagick.org/script/download.php#windows).
>   Take the first "Win64 dynamic" installer and keep **"Add application directory to
>   your system path"** ticked.

---

## Step 2 — Download this tool

1. On this GitHub page, click the green **Code** button, then **Download ZIP**.
2. In **Downloads**, right-click `catalog-exporter-main.zip` → **Extract All…** →
   **Extract**.
3. Open the extracted folder and go in until you see the file **`export.py`**. Windows
   often puts it one folder deeper:
   `catalog-exporter-main\catalog-exporter-main`.

---

## Step 3 — Run it

1. In that folder (the one with `export.py`), click the **address bar** at the top of
   the window, type `powershell` and press **Enter**. A PowerShell window opens in this
   folder.
2. Paste the **command we sent you** and press Enter. It looks like this:

   ```powershell
   py export.py "https://view.…/…/projectscene/…" --developer-slug …
   ```

   If `py` is not recognized, replace `py` with `python` and try again.

The run takes about **10 minutes**, and progress lines scroll by while it works.
It is finished when you see:

```
Done in …s
Result: C:\…\output\<project>
Checks: …/… passed
```

**If it stops or shows an error:** run the same command again. It continues where
it stopped and keeps everything already downloaded. See also
[If something goes wrong](#if-something-goes-wrong).

---

## Step 4 — Send us the result (Google Drive)

1. In the folder with `export.py`, open **`output`**. Inside is one folder named
   after your project.
2. Right-click that folder:
   - Windows 11: choose **Compress to ZIP file**.
   - Windows 10: choose **Send to → Compressed (zipped) folder**.

   You get a `.zip` file of about 0.5 GB.
3. Open [drive.google.com](https://drive.google.com), click **New → File upload** and
   choose that `.zip` file. Wait until the upload completes.
4. Right-click the uploaded file, choose **Share**, and add the email address we gave
   you. Or set **General access** to *Anyone with the link* and click **Copy link**.
5. Send us the link.

That's all. Thank you!

---

## If something goes wrong

| What you see | What to do |
|---|---|
| `winget : The term 'winget' is not recognized` | Install Python and ImageMagick by hand (see the note in step 1). |
| `py` / `python` is not recognized, or the Microsoft Store opens | Python is not installed yet, or PowerShell was not reopened after installing it. Close PowerShell, open it again the same way (step 3.1), run again. If it still fails, install Python from python.org with **"Add python.exe to PATH"** ticked. |
| `ImageMagick (the 'magick' command) was not found` | ImageMagick is not installed yet, or PowerShell was not reopened. Install it (step 1), then open a new PowerShell window and run again. |
| `can't open file '…export.py'` | PowerShell is not in the folder with `export.py`. Open that folder and use the address bar trick from step 3.1. |
| `paste the link to your catalog after export.py` | The link is missing or incomplete. Copy the whole command we sent you, including the link in quotes. |
| `could not download from the catalog after several tries` | The internet connection dropped or the catalog did not answer. Run the same command again; it continues. |
| Anything else | Send us a screenshot of the PowerShell window. |

To start over from scratch, add `--fresh` at the end of the command.

---

## On a Mac

1. Open **Terminal** (`⌘ Space`, type Terminal).
2. Run `xcode-select --install` and click **Install**. If Terminal says it is already
   installed, that is fine.
3. Install **Homebrew** from [brew.sh](https://brew.sh), then run
   `brew install imagemagick`.
4. Download the ZIP as in step 2 (it unpacks to `Downloads/catalog-exporter-main`), then:

   ```bash
   cd ~/Downloads/catalog-exporter-main
   python3 export.py "https://view.…" --developer-slug …
   ```

   Use the exact command we sent you, with `python3` instead of `py`.
5. Compress the folder in `output` (right-click → **Compress**) and share it as in
   step 4.

---

## For an AI assistant helping with this (Claude etc.)

- Follow the steps above with the user. The run is `py export.py "<catalog link>"` on
  Windows, or `python3 export.py "<catalog link>"` on macOS, from the folder that
  contains `export.py`. `verify.py` re-checks a finished export.
- If the user is in Claude Code, a permission prompt may appear because the tool
  downloads from the catalog's API, its CDN and `firebasestorage.googleapis.com`.
  That is expected. Let the user approve it, or have them run the command themselves.
- How it works, the output format, all options and the API details are in
  **[docs/TECHNICAL.md](docs/TECHNICAL.md)**.

---

## Rights

The renders and tours belong to the developer. Run this tool only for a project you
own or have permission to copy.
