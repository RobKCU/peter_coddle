(# Peter Coddle — George Washington's Dream Bot)

Small Bluesky bot that posts lines from the 1929 Parker Bros. game "George Washington's Dream".

Usage
-----

- Create a Python virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- Provide Bluesky credentials via an `.env` file (do not commit):

```
BLUESKY_HANDLE=your_handle
BLUESKY_APP_PASSWORD=your_app_password
```

- Commands:

```bash
# Preview only (no state changes)
python3 bot.py --dry-run

# Preview only (default)
python3 bot.py

# Post to Bluesky and update state.json
python3 bot.py --post

# Reset state.json to the beginning
python3 bot.py --reset-state
```

Notes
-----
- `python3 bot.py` and `python3 bot.py --dry-run` will not update `data/state.json`.
- Only `python3 bot.py --post` performs a publish and writes the updated state.
- Keep your `.env` out of the repository; `.gitignore` already contains `.env`.

GitHub Actions
---------------

A workflow is included at `.github/workflows/daily-post.yml`.

- By default the scheduled run performs a dry-run (no posting).
- Manual dispatch supports an input `do_post` — set it to `true` to run `--post`.
- To enable posting from Actions you must add repository secrets:
	- `BLUESKY_HANDLE`
	- `BLUESKY_APP_PASSWORD`

Security / Safety Recommendations
-------------------------------
- Test posting locally before enabling automated posting from Actions.
- Optionally protect the `post` job with GitHub Environments and required reviewers.

Safety guard
-----------

To reduce accidental duplicate posts, the bot includes a safety guard that blocks
automatic posting if the previous post was less than 20 hours ago. This applies
to scheduled and manual runs unless you override it with `--force`:

```bash
python3 bot.py --post        # will be blocked if last post < 20 hours ago
python3 bot.py --post --force  # force the post regardless of last_posted_at
```

When running via GitHub Actions the scheduled job will attempt to post, but the
guard will prevent posting if the last published time is too recent.

