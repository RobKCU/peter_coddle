#!/usr/bin/env python3

"""
Peter Coddle / George Washington's Dream Bluesky bot.

This script:

- Loads story text from data/gwd_text.json
- Loads lexicon from data/gwd_lexicon.json
- Loads/saves state from data/state.json
- Generates the next post in sequence
- Posts the title unnumbered
- Posts story sentences with index number / total number
- Randomly fills [BLANK] slots with unused lexicon entries
- Avoids reusing lexicon entries until all entries have been used
- Marks inserted slip-words with underscores
- Checks Bluesky's 300-character limit
- Reads Bluesky credentials from .env
- Posts to Bluesky only when run with --post

Basic commands:

    python3 bot.py --dry-run

Preview only. Does not post. Does not update state.

    python3 bot.py

Preview only. Does not post. Does not update state.

    python3 bot.py --post

Posts to Bluesky and updates state.

    python3 bot.py --reset-state

Resets state to the beginning.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency for previewing locally
    def load_dotenv(path=None):
        """Fallback no-op when python-dotenv isn't installed.

        This allows previewing the bot without requiring the dependency
        to be installed. Posting (--post) still requires valid env vars
        to be present in the environment.
        """
        return False


# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"

TEXT_PATH = DATA_DIR / "gwd_text.json"
LEXICON_PATH = DATA_DIR / "gwd_lexicon.json"
STATE_PATH = DATA_DIR / "state.json"
LOG_PATH = LOG_DIR / "bot.log"
ENV_PATH = ROOT / ".env"

load_dotenv(ENV_PATH)


# ---------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------

BLANK_TOKEN = "[BLANK]"
MAX_POST_LENGTH = 300
MAX_FILL_ATTEMPTS = 50

# How inserted slip-words should appear.
# Example: A Haunted House -> _A Haunted House_
INSERT_PREFIX = "_"
INSERT_SUFFIX = "_"

# Public Bluesky formatting.
# Title entries are not numbered.
# Non-title story entries are formatted as:
#
#     4/82
#
#     Sentence text...
#
NUMBER_STORY_POSTS = True
COUNT_TITLE_AS_STORY_ITEM = False
# Safety: minimum interval (in hours) between posts when not forced
MIN_POST_INTERVAL_HOURS = 20


# ---------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------

def setup_logging() -> None:
    """Create logs directory if needed and configure logging."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def load_json(path: Path) -> Any:
    """Load a JSON file and return its contents."""
    if not path.exists():
        raise FileNotFoundError(f"Could not find required file: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    """Save data as pretty-printed JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def post_to_bluesky(post_text: str) -> None:
    """
    Post text to Bluesky.

    Requires these values in .env:

        BLUESKY_HANDLE
        BLUESKY_APP_PASSWORD
    """
    from atproto import Client

    handle = os.environ.get("BLUESKY_HANDLE")
    app_password = os.environ.get("BLUESKY_APP_PASSWORD")

    if not handle:
        raise RuntimeError("Missing BLUESKY_HANDLE in .env")

    if not app_password:
        raise RuntimeError("Missing BLUESKY_APP_PASSWORD in .env")

    client = Client()
    client.login(handle, app_password)
    client.send_post(text=post_text)


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------

def validate_story(story: Any) -> None:
    """Check that the story JSON has the expected shape."""
    if not isinstance(story, list):
        raise ValueError("Story file must contain a JSON array.")

    if not story:
        raise ValueError("Story file is empty.")

    for i, item in enumerate(story):
        if not isinstance(item, dict):
            raise ValueError(f"Story item at index {i} is not an object.")

        if "text" not in item:
            raise ValueError(f"Story item at index {i} is missing a 'text' field.")

        if not isinstance(item["text"], str):
            raise ValueError(f"Story item at index {i} has a non-string 'text' field.")


def validate_lexicon(lexicon: Any) -> None:
    """Check that the lexicon JSON has the expected shape."""
    if not isinstance(lexicon, list):
        raise ValueError("Lexicon file must contain a JSON array.")

    if not lexicon:
        raise ValueError("Lexicon file is empty.")

    for i, word in enumerate(lexicon):
        if not isinstance(word, str):
            raise ValueError(f"Lexicon item at index {i} is not a string.")

        if not word.strip():
            raise ValueError(f"Lexicon item at index {i} is blank.")


def default_state() -> dict[str, Any]:
    """Return a fresh default state."""
    return {
        "next_index": 0,
        "used_words": [],
        "cycle": 1,
        "last_posted_at": None,
        "last_post_text": None,
    }


def normalize_state(state: Any) -> dict[str, Any]:
    """
    Make sure state has the fields this script needs.

    This lets a minimal state.json like this work:

        {
          "next_index": 0,
          "used_words": [],
          "cycle": 1
        }
    """
    if not isinstance(state, dict):
        raise ValueError("State file must contain a JSON object.")

    normalized = default_state()
    normalized.update(state)

    if not isinstance(normalized["next_index"], int):
        raise ValueError("state['next_index'] must be an integer.")

    if not isinstance(normalized["used_words"], list):
        raise ValueError("state['used_words'] must be a list.")

    if not isinstance(normalized["cycle"], int):
        raise ValueError("state['cycle'] must be an integer.")

    return normalized


# ---------------------------------------------------------------------
# Public post formatting
# ---------------------------------------------------------------------

def is_title_item(item: dict[str, Any]) -> bool:
    """Return True if this story item should be treated as a title."""
    return item.get("type") == "title"


def story_post_number(item_index: int, story: list[dict[str, Any]]) -> tuple[int, int]:
    """
    Return the public story number and total.

    If the title is not counted, and the title is at index 0, then:

        item_index 1 -> 1/82
        item_index 2 -> 2/82
        etc.
    """
    if COUNT_TITLE_AS_STORY_ITEM:
        return item_index + 1, len(story)

    title_count_before_this_item = sum(
        1
        for earlier_item in story[:item_index]
        if is_title_item(earlier_item)
    )

    total_title_count = sum(
        1
        for story_item in story
        if is_title_item(story_item)
    )

    number = item_index + 1 - title_count_before_this_item
    total = len(story) - total_title_count

    return number, total


def make_public_post_text(
    item: dict[str, Any],
    item_index: int,
    story: list[dict[str, Any]],
    filled_text: str,
) -> str:
    """
    Format the text exactly as it should appear on Bluesky.

    Title entries are posted without numbering.

    Story sentence entries are posted as:

        4/82

        Sentence text...
    """
    if is_title_item(item):
        return filled_text

    if not NUMBER_STORY_POSTS:
        return filled_text

    number, total = story_post_number(item_index, story)

    return f"{number}/{total}\n\n{filled_text}"


# ---------------------------------------------------------------------
# Core generation logic
# ---------------------------------------------------------------------

def count_blanks(text: str) -> int:
    """Count [BLANK] slots in a story item."""
    return text.count(BLANK_TOKEN)


def format_inserted_word(word: str) -> str:
    """Format a slip-word so it is visibly inserted."""
    return f"{INSERT_PREFIX}{word}{INSERT_SUFFIX}"


def available_words(lexicon: list[str], used_words: list[str]) -> list[str]:
    """
    Return words that have not yet been used.

    If all words have been used, reset availability to the full lexicon.
    This preserves the rule: no reuse until the whole lexicon is exhausted.
    """
    used_set = set(used_words)
    unused = [word for word in lexicon if word not in used_set]

    if unused:
        return unused

    return list(lexicon)


def choose_words_for_blanks(
    lexicon: list[str],
    used_words: list[str],
    blank_count: int,
) -> list[str]:
    """
    Choose random words for a sentence's blanks.

    This prefers unused words. If the sentence needs more words than remain
    unused, it uses the remaining unused words first, then begins a fresh
    shuffled cycle.
    """
    chosen: list[str] = []
    simulated_used = list(used_words)
    lexicon_set = set(lexicon)

    for _ in range(blank_count):
        # If the full lexicon has already been used, begin a fresh cycle.
        if lexicon_set.issubset(set(simulated_used)):
            simulated_used = []

        choices = available_words(lexicon, simulated_used)
        word = random.choice(choices)

        chosen.append(word)
        simulated_used.append(word)

    return chosen


def fill_blanks(text: str, words: list[str]) -> str:
    """Replace [BLANK] tokens one by one with formatted words."""
    filled = text

    for word in words:
        filled = filled.replace(BLANK_TOKEN, format_inserted_word(word), 1)

    return filled


def generate_filled_sentence(
    text: str,
    lexicon: list[str],
    used_words: list[str],
) -> tuple[str, list[str]]:
    """
    Fill a sentence's blanks.

    This function only fills the sentence itself.
    Public numbering is added later.

    Returns:
        filled_text, inserted_words
    """
    blank_count = count_blanks(text)

    if blank_count == 0:
        return text, []

    words = choose_words_for_blanks(
        lexicon=lexicon,
        used_words=used_words,
        blank_count=blank_count,
    )

    filled = fill_blanks(text, words)
    return filled, words


def generate_numbered_post_with_length_safety(
    item: dict[str, Any],
    item_index: int,
    story: list[dict[str, Any]],
    lexicon: list[str],
    used_words: list[str],
) -> tuple[str, list[str]]:
    """
    Generate the public post text, trying several fillings if needed.

    This is where the 300-character check happens, because the public post
    includes the story number prefix.
    """
    raw_text = item["text"]
    blank_count = count_blanks(raw_text)

    # Title items and sentences without blanks only need one check.
    if blank_count == 0:
        filled_text = raw_text
        post_text = make_public_post_text(
            item=item,
            item_index=item_index,
            story=story,
            filled_text=filled_text,
        )

        if len(post_text) > MAX_POST_LENGTH:
            raise ValueError(
                f"Generated post is {len(post_text)} characters, "
                f"which exceeds the {MAX_POST_LENGTH}-character limit:\n\n"
                f"{post_text}"
            )

        return post_text, []

    best_post_text = ""
    best_words: list[str] = []
    best_length = 10**9

    for _ in range(MAX_FILL_ATTEMPTS):
        filled_text, inserted_words = generate_filled_sentence(
            text=raw_text,
            lexicon=lexicon,
            used_words=used_words,
        )

        post_text = make_public_post_text(
            item=item,
            item_index=item_index,
            story=story,
            filled_text=filled_text,
        )

        length = len(post_text)

        if length < best_length:
            best_post_text = post_text
            best_words = inserted_words
            best_length = length

        if length <= MAX_POST_LENGTH:
            return post_text, inserted_words

    raise ValueError(
        "Could not generate a post under "
        f"{MAX_POST_LENGTH} characters after {MAX_FILL_ATTEMPTS} attempts. "
        f"Shortest attempt was {best_length} characters:\n\n{best_post_text}\n\n"
        f"Words in shortest attempt: {best_words}"
    )


def update_used_words(
    old_used_words: list[str],
    newly_used_words: list[str],
    lexicon: list[str],
) -> list[str]:
    """
    Update used_words after a successful post.

    If the lexicon has already been exhausted, begin a new used-word cycle.
    """
    used_words = list(old_used_words)
    lexicon_set = set(lexicon)

    for word in newly_used_words:
        # If every lexicon item has been used, start a fresh cycle.
        if lexicon_set.issubset(set(used_words)):
            used_words = []

        used_words.append(word)

    return used_words


def advance_index(current_index: int, story_length: int) -> tuple[int, bool]:
    """
    Move to the next story item.

    Returns:
        next_index, restarted

    If we reach the end, restart at 0.
    """
    next_index = current_index + 1

    if next_index >= story_length:
        return 0, True

    return next_index, False


def generate_next_post(
    story: list[dict[str, Any]],
    lexicon: list[str],
    state: dict[str, Any],
) -> tuple[str, dict[str, Any], list[str]]:
    """
    Generate the next post and return:

        post_text, updated_state, inserted_words
    """
    next_index = state["next_index"]

    if next_index < 0 or next_index >= len(story):
        raise ValueError(
            f"state['next_index'] is {next_index}, "
            f"but story has {len(story)} items."
        )

    item = story[next_index]

    post_text, inserted_words = generate_numbered_post_with_length_safety(
        item=item,
        item_index=next_index,
        story=story,
        lexicon=lexicon,
        used_words=state["used_words"],
    )

    updated_state = deepcopy(state)

    updated_state["used_words"] = update_used_words(
        old_used_words=state["used_words"],
        newly_used_words=inserted_words,
        lexicon=lexicon,
    )

    new_index, restarted = advance_index(next_index, len(story))
    updated_state["next_index"] = new_index

    if restarted:
        updated_state["cycle"] = int(updated_state.get("cycle", 1)) + 1
        updated_state["used_words"] = []

    updated_state["last_posted_at"] = datetime.now().isoformat(timespec="seconds")
    updated_state["last_post_text"] = post_text

    return post_text, updated_state, inserted_words


# ---------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------

def print_preview(
    post_text: str,
    state: dict[str, Any],
    inserted_words: list[str],
    dry_run: bool,
    will_post: bool,
) -> None:
    """
    Print a human-readable local preview.

    This is only for your terminal. Only post_text itself is sent to Bluesky.
    """
    if dry_run:
        mode = "DRY RUN"
    elif will_post:
        mode = "POST TO BLUESKY"
    else:
        mode = "PREVIEW ONLY"

    print()
    print("=" * 72)
    print(mode)
    print("=" * 72)
    print(f"Cycle: {state.get('cycle', 1)}")
    print(f"Post index: {state['next_index']}")
    print(f"Characters: {len(post_text)} / {MAX_POST_LENGTH}")
    print("-" * 72)
    print(post_text)
    print("-" * 72)

    if inserted_words:
        print("Inserted words:")
        for word in inserted_words:
            print(f"  - {word}")
    else:
        print("Inserted words: none")

    print("=" * 72)
    print()


# ---------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the next Peter Coddle / George Washington's Dream post."
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview the next post without saving state or posting.",
    )

    parser.add_argument(
        "--post",
        action="store_true",
        help="Actually post the generated text to Bluesky and save state.",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Override safety guard and force posting even if last post was recent.",
    )

    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Reset data/state.json to the initial state and exit.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging()

    if args.reset_state:
        save_json(STATE_PATH, default_state())
        print(f"Reset state file: {STATE_PATH}")
        logging.info("State reset.")
        return

    story = load_json(TEXT_PATH)
    lexicon = load_json(LEXICON_PATH)

    validate_story(story)
    validate_lexicon(lexicon)

    raw_state = load_json(STATE_PATH)
    state = normalize_state(raw_state)

    post_text, updated_state, inserted_words = generate_next_post(
        story=story,
        lexicon=lexicon,
        state=state,
    )

    print_preview(
        post_text=post_text,
        state=state,
        inserted_words=inserted_words,
        dry_run=args.dry_run,
        will_post=args.post,
    )

    # Safety guard: prevent posting too frequently unless forced.
    if args.post and not args.force:
        last = state.get("last_posted_at")
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                now = datetime.now()
                delta_hours = (now - last_dt).total_seconds() / 3600.0

                if delta_hours < MIN_POST_INTERVAL_HOURS:
                    msg = (
                        f"Last post was {delta_hours:.1f} hours ago, "
                        f"which is less than the minimum {MIN_POST_INTERVAL_HOURS} hours.\n"
                        "Use `--force` to override if you really want to post."
                    )
                    print(msg)
                    logging.info(
                        "Post blocked by safety guard | last_posted=%s | hours_since=%.2f",
                        last,
                        delta_hours,
                    )
                    return
            except Exception:
                # If parsing fails, continue but log the issue.
                logging.warning("Could not parse state['last_posted_at']=%s", last)

    if args.dry_run:
        logging.info(
            "Dry run | index=%s | chars=%s | inserted=%s",
            state["next_index"],
            len(post_text),
            inserted_words,
        )
        print("Dry run only. State not updated.")
        return

    if not args.post:
        logging.info(
            "Preview only | index=%s | chars=%s | inserted=%s",
            state["next_index"],
            len(post_text),
            inserted_words,
        )
        print("Preview only. State not updated. Use --post when ready.")
        return

    post_to_bluesky(post_text)
    print("Posted to Bluesky.")

    save_json(STATE_PATH, updated_state)

    logging.info(
        "Posted to Bluesky | old_index=%s | new_index=%s | chars=%s | inserted=%s",
        state["next_index"],
        updated_state["next_index"],
        len(post_text),
        inserted_words,
    )

    print(f"State updated: {STATE_PATH}")


if __name__ == "__main__":
    main()