# Catalog Exporter

This tool makes a complete copy of what your online 3D property catalog shows:

- the building renders
- the floor plans
- the apartment and amenity 360° tours
- the apartment top views
- a table of all apartments

It runs on your Mac. At the end you send us one folder.

It only **reads** from the catalog. It does not log in, upload or change anything
there.

You will need the **link to your catalog**. We send it to you together with the
command to run.

---

## What you need

- A **Mac** (macOS 12 or newer).
- About **20 minutes** the first time, most of it waiting for installs.
- About **1 GB** of free disk space.
- An internet connection.

Everything below is typed into **Terminal**. To open it, press `⌘ Space`, type
**Terminal**, and press Enter. To run a command, paste it into the Terminal window
and press Enter.

---

## Step 1 — Install the tools (once)

**1.1 Apple's command line tools.** Paste this:

```bash
xcode-select --install
```

A window appears. Click **Install** and wait until it finishes, which takes a few
minutes. If Terminal says *"already installed"*, that is fine; go on.

**1.2 Homebrew** (a standard installer for Mac tools). First check whether you
already have it:

```bash
brew --version
```

- If it prints a version number, skip to 1.3.
- If it says *"command not found"*, install Homebrew. Paste this, press Enter, and
  type your Mac password when asked (nothing appears while you type):

  ```bash
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  ```

  At the end Homebrew prints **"Next steps"** with two or three commands. Copy and
  run them too; they make the `brew` command available. Then close Terminal and
  open it again.

**1.3 The image tools.** Paste this:

```bash
brew install imagemagick librsvg
```

---

## Step 2 — Download this tool

1. On this GitHub page, click the green **Code** button, then **Download ZIP**.
2. Open the downloaded ZIP. It unpacks into your **Downloads** as a folder named
   **`catalog-exporter-main`**.

---

## Step 3 — Run it

Paste these two lines. The second one is the command we sent you, with your catalog
link in quotes:

```bash
cd ~/Downloads/catalog-exporter-main
python3 export.py "https://view.…/…/projectscene/…"
```

The run takes about **5–10 minutes**, and progress lines scroll by while it works.
It is finished when you see:

```
Done in … -> …/output/<project>
Checks: … passed
```

Lines starting with `!` are **notes about the catalog data**, not errors. They are
also saved in the result for us to read.

**If it stops or shows an error:** run the same command again. It continues where
it stopped and keeps everything already downloaded. See also
[If something goes wrong](#if-something-goes-wrong).

---

## Step 4 — Send us the result (Google Drive)

1. In Finder, open **Downloads → catalog-exporter-main → output**.
2. Right-click the folder inside it (named after your project) and choose
   **Compress**. You get a `.zip` file of about 0.5 GB.
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
| `missing tool(s): magick, rsvg-convert` | Step 1.3 was not done, or Terminal was not reopened after installing Homebrew. Do step 1.3, reopen Terminal, run again. |
| `brew: command not found` after installing Homebrew | Run the "Next steps" commands Homebrew printed at the end, then reopen Terminal. |
| `xcrun: error: invalid active developer path` or a pop-up asking to install developer tools | Do step 1.1. |
| `could not download from the catalog after several tries` | The internet connection dropped or the catalog did not answer. Run the same command again; it continues. |
| `paste the link to your catalog after export.py` | The link is missing or incomplete. Copy the whole command we sent you, including the link in quotes. |
| `No such file or directory` after `cd` | The folder has a different name or location. Type `cd ` (with a space), drag the unpacked folder from Finder into the Terminal window, press Enter, then run the command again. |
| Anything else | Send us a screenshot of the Terminal window. |

To start over from scratch, add `--fresh` at the end of the command.

---

## For an AI assistant helping with this (Claude etc.)

- Follow the steps above with the user. The run is `python3 export.py "<catalog link>"`
  from the repo folder. `python3 verify.py` re-checks a finished export.
- If the user is in Claude Code, a permission prompt may appear because the tool
  downloads from the catalog's API, its CDN and `firebasestorage.googleapis.com`.
  That is expected. Let the user approve it, or have them run the command themselves
  with `!` in front.
- How it works, the output format, all options and the API details are in
  **[docs/TECHNICAL.md](docs/TECHNICAL.md)**.

---

## Rights

The renders and tours belong to the developer. Run this tool only for a project you
own or have permission to copy.
