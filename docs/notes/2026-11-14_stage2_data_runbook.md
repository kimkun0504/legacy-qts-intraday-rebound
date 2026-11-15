# Stage 2 data: downloading Massive's minute bars (runbook for the owner)

All commands run in **Terminal on the Mac**, from the project folder. The research VM cannot
reach Massive (its network filter blocks the host), so the download happens here and the
analysis reads the files from this folder. `scripts/massive.py` uses only Python's standard
library: nothing to install.

```bash
cd ~/Desktop/QTS/qts-intraday-rebound
PY=~/miniconda3/bin/python3          # any Python 3.9+ works; this one is known to be there
```

## 1. Subscribe and put the keys in `.env`
1. Massive dashboard: subscribe to **Stocks Advanced, monthly** ($199). Advanced is the only
   individual plan with minute bars back to 2003-09-10, plus the quotes the stage-2 spread
   check uses.
2. Copy three keys from the dashboard: the **API key**, and the flat-file (S3) **Access Key ID**
   and **Secret Access Key**. The S3 pair is separate from the API key.
3. Create `.env` (gitignored; it never leaves this Mac) and paste each key after its `=`:
   ```bash
   cp .env.example .env
   open -e .env        # TextEdit: replace the three paste-... values, save, close
   ```
   Do not paste keys into chat.

## 2. Check the keys (about 10 MB)
```bash
$PY scripts/massive.py check
```
Five lines should read OK: keys present; listing works; the first day of daily bars and the
first day of minute bars (2003-09-10) download with matching checksums; delisted names are
present (LEH, Lehman Brothers); the API key works. If the minute-bar step says access refused,
the plan is not Stocks Advanced.

## 3. See the size
```bash
$PY scripts/massive.py plan
```
Expected: about 5,850 files per dataset; minute bars about 90-100 GB, daily bars a few GB. The
holdout part (2023-11-09 on) is listed separately. Free space here: about 456 GB.

## 4. Download (hours; resumable)
```bash
caffeinate -i $PY scripts/massive.py get
```
- `caffeinate -i` keeps the Mac awake while the command runs. Keep it plugged in.
- Daily bars come first (minutes), then minute bars. Progress prints every 15 seconds.
- Stopped for any reason (Ctrl-C, sleep, Wi-Fi drop)? Run the same command again. Finished
  files are kept and skipped. Each file is checked against Massive's MD5 before it is kept.
- Faster connection: add `--workers 8`.
- Rough time for ~95 GB: about 2 hours at 100 Mbit/s, under an hour at 300+ Mbit/s if
  Massive's server keeps up.

## 5. Ticker lists, then a final check
```bash
$PY scripts/massive.py tickers     # active and delisted tickers (a minute or two)
$PY scripts/massive.py verify      # re-checks every file; must end with 0 bad
```
If `verify` reports bad files: `$PY scripts/massive.py verify --fix` (renames them to `*.bad`),
then `get` again.

## Where the files go
- `data/massive/raw/...`: research dates (before 2023-11-09), mirroring Massive's paths.
- `data/massive/sealed/...`: holdout dates. Downloaded now because the plan is paid by the
  month, but no research code reads this folder until you lift the seal.
- `data/massive/manifest/` (listings, download log) and `data/massive/reference/` (tickers).
- `data/` is gitignored: nothing here is ever committed.

**iCloud:** if *Desktop & Documents Folders* sync is on (System Settings > your name > iCloud
> iCloud Drive), ~95 GB on the Desktop would upload to iCloud. In that case don't run `get` yet;
tell me, and we'll pick a folder outside the Desktop (add `--root <folder>` and connect that
folder to the session).

## Keep the subscription until
1. `verify` ends with 0 bad files, and
2. the stage-2 run is done. Stage 2 fetches quotes for a random sample of fills through the
   API (`docs/notes/2026-11-14_preregistration_stage2.md`, data-quality checks), which needs
   an active plan.
