# Mixtape Bug Hunt — Submission

Branch: `bugfix/mixtape`

## AI Usage

I used an AI assistant (Claude) throughout this project, primarily for codebase orientation and for building lightweight, isolated reproductions of each bug's core logic. Specifics:

- **Orientation**: I had the assistant read `README.md`, `app.py`, `models.py`, all of `routes/`, and all of `services/` up front and summarize what each module is responsible for, before opening any issue. This produced the codebase map below. I then read each of the five affected service files myself before deciding which bugs to fix — the README's issue-to-file table made this fast, but I still read every line of each service file rather than trusting a description of what it does.
- **Root cause confirmation**: For each bug, I located the suspicious code by reading (not by asking the AI to "find the bug" first). Once I had a specific line or block I suspected, I used the assistant to reason through it — e.g., confirming that Python's `datetime.weekday()` returns `6` for Sunday, and walking through what `db.session.query(Song).outerjoin(song_tags, ...)` does to row cardinality when a song has multiple tags. In both cases I verified the explanation was correct by tracing the logic by hand against the actual seed data and existing test fixtures before touching any code.
- **Environment constraint and how I adapted**: The sandboxed environment I ran the assistant in had no outbound network access, so `pip install -r requirements.txt` could not reach PyPI and Flask/SQLAlchemy/pytest could not be installed there. Rather than fix bugs on faith, for every issue I built a small, dependency-free reproduction that exercises the *actual* code from the repo:
  - For Issues #1 and #4, I wrote a tiny stand-in for `app.db` and `models` (plain Python objects, no real ORM) and imported the real `streak_service.py` / `notification_service.py` files against those stand-ins, then ran the exact before/after scenario from the issue report and printed the resulting streak values / notification counts.
  - For Issue #3, I recreated the `song` / `song_tags` tables in an in-memory SQLite database (via Python's built-in `sqlite3`, no install needed) and ran the equivalent SQL to the ORM query in `search_service.py`, with and without `DISTINCT`, to see the duplicate rows directly.
  - For Issue #2, I did the cutoff-vs-event-timestamp arithmetic directly with `datetime`/`timedelta` using the exact timestamps from the report (11pm listen, 9am check).
  - For Issue #5, the bug (`songs[:-1]`) is a plain list slice — I confirmed its effect on ordered lists of 5 and 7 items directly.

  This gave me genuine executed evidence of each root cause and each fix, rather than relying on the AI's read of the code. I was not able to run `flask run` or the bundled `pytest` suite in that sandbox — **please run `pip install -r requirements.txt`, `python seed_data.py`, `flask run`, and `pytest tests/ -v` locally to do the final end-to-end confirmation**; the repo already contains tests (`test_streaks.py::test_streak_increments_on_sunday`, `test_search.py::test_search_no_duplicates_multi_tag_song`, `test_playlists.py::test_playlist_returns_all_songs`) that encode exactly the before/after behavior I verified by hand, plus two new test files I added (`test_feed.py`, `test_notifications.py`) for the issues that didn't have coverage yet.
- Where the AI was *not* reliable: early on I asked it to summarize what `add_to_playlist` in `notification_service.py` does, and its summary glossed over the fact that the notification call fires unconditionally whenever `song.shared_by != added_by_user_id`, even if the song was already in the playlist (i.e., re-adding the same song re-notifies). That's a real quirk I only caught by reading the function myself line by line. It doesn't affect any of the five tracked issues, so I left it alone, but it's a good example of why the brief's advice — verify by reading the code yourself — mattered in practice.

---

## Codebase Map

**`app.py`** — Flask application factory (`create_app`). Creates a single shared `db = SQLAlchemy()` instance at module scope (imported by every other file that touches the database), applies config (SQLite by default), registers the four blueprints (`songs`, `playlists`, `users`, `feed`), and calls `db.create_all()` inside an app context. There is no `if __name__` production entry point meant to be used directly — the README explicitly calls out running via `FLASK_APP=app:create_app flask run` to avoid a double-import of the `db` object.

**`models.py`** — All SQLAlchemy models: `User`, `Tag`, `Song`, `ListeningEvent`, `Rating`, `Playlist`, `Notification`, plus three bare association tables (`friendships`, `song_tags`, `playlist_entries`). `friendships` is a symmetric many-to-many self-join on `User` (friendship rows are inserted in both directions — see `seed_data.py`'s `add_friendship` helper). `playlist_entries` is a many-to-many table between `Playlist` and `Song` that carries extra columns (`position`, `added_by`, `added_at`) — songs in a playlist have an explicit order, not just insertion order. Every model has a `to_dict()` used directly as the JSON response shape; there's no separate serializer layer.

**`routes/`** — Thin Flask blueprints. Every route parses the request, calls exactly one function in `services/`, and formats the response (or catches `ValueError` and returns a 4xx). No business logic lives in `routes/` — this is a consistent pattern across all four blueprint files.

**`services/`** — All business logic, one file per feature area, matching the five tracked issues 1:1 (`streak_service.py`, `feed_service.py`, `search_service.py`, `notification_service.py`, `playlist_service.py`).

**`seed_data.py`** — Drops and recreates all tables, then inserts 5 users with an existing friend graph, 25 songs (deliberately split into 0-tag / 1-tag / 3-tag groups — the comments in this file call out that the 3-tag group is what exposes Issue #3), 3 playlists populated via direct `playlist_entries.insert()` calls (bypassing the ORM relationship, with explicit `position` values), a mix of very-recent and hours-old `ListeningEvent`s (to exercise Issue #2's recency window), and one pre-existing "song added to playlist" notification so the working notification pattern is visible for comparison against Issue #4.

**Pattern I noticed**: every service function that mutates state follows the same shape — look up the row(s) with `db.session.get`/`db.session.query(...).filter_by(...)`, raise `ValueError` if something required is missing (routes catch this and turn it into a 404/400), mutate, `db.session.commit()`. `notification_service.add_to_playlist` additionally does a "mutate, then notify" two-step, which turned out to be the exact pattern missing from `rate_song` (Issue #4).

**Data flow — a friend adds your shared song to a playlist** (`POST /playlists/<playlist_id>/songs`): `routes/playlists.py::add_song` parses `song_id`/`added_by` from the JSON body and calls `services.notification_service.add_to_playlist(playlist_id, song_id, added_by)`. That function loads the `Song`, the adding `User`, and the `Playlist` (raising `ValueError` -> 400 if any are missing), appends the song to `playlist.songs` if it isn't already there and commits, then — as a second, separate step — checks whether the song's original sharer (`song.shared_by`) is a different user than whoever just added it, and if so calls `create_notification(user_id=song.shared_by, notification_type="song_added_to_playlist", body=...)`, which builds a `Notification` row and commits it. The sharer later reads it via `GET /users/<id>/notifications` -> `routes/users.py::notifications` -> `services.notification_service.get_notifications`.

---

## Root Cause Analysis

### Issue #1 — My listening streak keeps resetting

**How I reproduced it**: `services/streak_service.py::update_listening_streak(user, now)` takes a plain `User` object and a datetime, and doesn't touch the database inside the function body, so I could exercise it directly. I stubbed out `app` and `models` with plain Python objects (no real SQLAlchemy needed) and imported the actual file from the repo. I simulated kenji's exact sequence: streak already at 12, then a listen on a Saturday, then the next calendar day (a Sunday), then the day after (a Monday). Running the original file: streak went 12 -> 13 (Saturday) -> **1** (Sunday) -> 2 (Monday) — reproducing the report exactly, including that it "starts counting again" from a low number afterward.

**How I found the root cause**: The docstring for `update_listening_streak` states the rule plainly: consecutive-day listens increment, gaps reset. Reading the actual branch logic, the increment branch is `elif days_since_last == 1 and today.weekday() != 6:`. `days_since_last == 1` is correct (consecutive day), but the added `and today.weekday() != 6` condition means the increment is *skipped* — falling through to the `else: user.listening_streak = 1` reset branch — whenever `today` is a day whose `weekday()` is `6`. Python's `datetime.weekday()` returns `0` for Monday through `6` for Sunday. So this condition is false (blocking the increment) precisely on Sundays. This matched the report's detail that both resets happened on a Sunday.

**The root cause**: The consecutive-day increment path was gated by `today.weekday() != 6`, and `weekday() == 6` means Sunday in Python's convention. So any listen that happened on a Sunday — even one that was a legitimate consecutive day — was excluded from the increment branch and fell into the "streak resets to 1" branch instead, as if a day had been skipped. There is no legitimate reason in the spec (see the function's own docstring) for the increment to depend on the day of the week at all; this condition should never have been there.

**My fix and side-effect check**: Removed the `and today.weekday() != 6` clause, leaving `elif days_since_last == 1:`. I re-ran the same reproduction script against the patched file: 12 -> 13 (Saturday) -> 14 (Sunday) -> 15 (Monday) — the streak now increments correctly across the week boundary. I checked the two other branches (`days_since_last == 0`, the same-day no-op, and the `else` multi-day-gap reset) are untouched by this change, and the repo's own `tests/test_streaks.py` already contains `test_streak_does_not_double_count_same_day` and `test_streak_resets_after_skipped_day` covering those, plus `test_streak_increments_on_sunday` which encodes exactly the scenario this fix resolves.

---

### Issue #2 — Friends Listening Now shows people from yesterday

**How I reproduced it**: `services/feed_service.py::get_friends_listening_now` filters `ListeningEvent`s by `listened_at >= cutoff` where `cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD` and `RECENT_THRESHOLD = timedelta(hours=24)`. I plugged in the report's exact timestamps — darius listened at 11pm, nova checks at 9am the next morning (a 10-hour gap) — and computed `cutoff` for the original 24-hour threshold: darius's event (10 hours old) is still `>= cutoff` (24 hours old), so it's included. That reproduces "still showing up the next morning" precisely, and shows it would keep showing up for a full 24 hours, i.e. "until the same time the next day," matching nova's description word for word.

**How I found the root cause**: The function name and docstring say "listening now" / "recently," but the single constant controlling that window, `RECENT_THRESHOLD`, was set to a full day. I checked `seed_data.py` for corroborating evidence: it seeds a batch of "recent" events at 10-25 minutes old (comment: "should appear in listening now") and a separate batch of events starting at 2 hours old and going up to ~58 hours old (comment: "should NOT appear in listening now after fix") — confirming the intended boundary is well under 2 hours, nowhere near the 24-hour value actually used.

**The root cause**: `RECENT_THRESHOLD` was defined as `timedelta(hours=24)`, a full rolling day, when the feature is meant to represent genuinely current or same-session listening activity. Any event from up to 24 hours ago passes the `listened_at >= cutoff` filter, so an evening listen is still "recent" enough to appear the entire next day, disappearing only once a full 24 hours have elapsed from that specific event — which is why it seemed to "hang around... until the same time the next day."

**My fix and side-effect check**: Changed `RECENT_THRESHOLD` to `timedelta(hours=1)`. Re-running the same before/after arithmetic, darius's 10-hour-old event now falls outside the 1-hour cutoff and is correctly excluded, while the seed data's genuinely-recent events (10-25 minutes old) remain well inside a 1-hour window. I checked `get_activity_feed` in the same file — it deliberately does *not* use `RECENT_THRESHOLD` (its docstring says "not filtered by recency") and is unaffected by this change, since it only uses `.limit(limit)`.

---

### Issue #3 — The same song keeps showing up twice in search

**How I reproduced it**: `services/search_service.py::search_songs` runs `db.session.query(Song).outerjoin(song_tags, Song.id == song_tags.c.song_id).filter(...).all()` with no `.distinct()`. I recreated the same `song` / `song_tags` table shapes in an in-memory SQLite database using Python's built-in `sqlite3` module and ran the equivalent SQL for a song with 3 tags ("Crown Heights Anthem", matching the report), a song with 1 tag, and a song with 0 tags. The 3-tag song came back 3 times, the 1-tag and 0-tag songs each came back exactly once — reproducing simone's report exactly, down to the specific song and the "some songs once, some two or three times" pattern.

**How I found the root cause**: The filter clause only checks `Song.title`/`Song.artist` — it never references `song_tags` at all, so the `outerjoin` isn't being used to filter or search by tag; it exists but does nothing useful for this query except join in extra rows. Once I noticed the join target was an unfiltered many-side table, I checked how many tags each affected song had against `seed_data.py`, which groups songs explicitly into "0 tags," "1 tag," and "3+ tags" buckets and even comments "these are the ones that expose Issue #3" next to the 3-tag group — confirming the row count from the join (1 row per matching `song_tags` entry) is exactly the duplication factor.

**The root cause**: `search_songs` joins `Song` to the `song_tags` association table (one row per song/tag pair) but never filters or aggregates on it, and the query has no `.distinct()`. A SQL join against a table with a one-to-many relationship (one song, many tags) produces one output row per matching pair on the "many" side. So a song with 3 tags produces 3 joined rows, each of which becomes a separate (identical) `Song` object in the Python results list — hence songs with 0 or 1 tags never duplicate, and songs with N tags appear N times.

**My fix and side-effect check**: Added `.distinct()` to the query chain, right before `.all()`. Re-running the SQLite reproduction with `SELECT DISTINCT` produces exactly one row for the 3-tag song. I checked `get_song(song_id)` (the other function in this file) — it's a single-row lookup by primary key and doesn't join anything, so it's unaffected. I also confirmed the existing `tests/test_search.py` already has `test_search_no_duplicates_multi_tag_song`, `test_search_no_duplicates_single_tag_song`, and `test_search_no_duplicates_no_tag_song`, which together cover all three tag-count cases this fix touches.

---

### Issue #4 — I got notified for a playlist add but not for a rating

**How I reproduced it**: I wrote a small in-memory fake for `db.session` (a dict-backed store supporting `add`/`get`/`commit`/`query().filter_by().first()`) and imported the real `notification_service.py` against it. I created a sharer (aaliya) and a rater (kenji), had kenji call the real `rate_song(user_id=kenji.id, song_id=song.id, score=5)`, and then counted how many `Notification` objects existed for aaliya afterward. Against the original file: zero. That matches aaliya's report exactly — the rating itself succeeds (I confirmed the `Rating` row is created/updated), but no notification is ever created.

**How I found the root cause**: The hint to compare the working path line-by-line to the missing one was accurate. `add_to_playlist` ends with a distinct second step after its state change is committed: it checks `if song.shared_by != added_by_user_id` and calls `create_notification(...)`. `rate_song` does the state change (`db.session.add`/`existing.score = score`, then `db.session.commit()`) but simply `return`s afterward — there is no equivalent call anywhere in the function or anywhere else in the file that fires when a rating is created. Grepping the whole file for `create_notification(` turned up exactly one call site, inside `add_to_playlist`, confirming the second usage was never written rather than written-and-broken.

**The root cause**: `rate_song` is missing the "notify" half of the create-then-notify pattern that every other mutating action in this file follows. It saves the `Rating` and returns without ever calling `create_notification`, so no `Notification` row is ever produced for a rating event, for any user, regardless of timing — matching aaliya's observation that it wasn't a delay, it "just never happens."

**My fix and side-effect check**: After the existing `db.session.commit()` in `rate_song`, added the same shape of notify step used by `add_to_playlist`: if `song.shared_by != user_id` (so you don't get notified about rating your own song), call `create_notification(user_id=song.shared_by, notification_type="song_rated", body=f"{rater.username} rated your song '{song.title}' {score} stars.")`. Re-running the fake-session reproduction, aaliya now has exactly one notification of type `song_rated` after kenji's rating. I checked `add_to_playlist` itself is untouched and still produces `song_added_to_playlist` notifications (verified with the same harness, and with a new test, `test_playlist_add_notification_still_works`, in `tests/test_notifications.py`), and added a self-rating case (`test_rating_your_own_song_does_not_notify_yourself`) to confirm the guard condition behaves the same way it does in the playlist path.

---

### Issue #5 — The last song in a playlist never shows up

**How I reproduced it**: `services/playlist_service.py::get_playlist_songs` builds an ordered list of songs (`order_by(asc(playlist_entries.c.position))`) and then returns `[song.to_dict() for song in songs[:-1]]`. This doesn't need a database at all to reproduce — `songs[:-1]` on a Python list of 5 items returns only the first 4, and on darius's reported 7-song playlist returns only the first 6, dropping whichever item is last in the ordered list (i.e., whichever song has the highest `position`, i.e., whichever was added most recently). I confirmed this directly with plain Python lists of 5 and 7 items.

**How I found the root cause**: The function's own docstring says "Returns: A list of song dicts in playlist order" and even has a "Note: This function returns all songs in the playlist" — directly contradicted by the very next block of code, which slices the last element off before returning. That contradiction between the docstring and the return statement was the moment I was confident this was the actual bug rather than something upstream in the query (the query itself, `order_by(asc(position))`, is correct and unrelated).

**The root cause**: The final line, `return [song.to_dict() for song in songs[:-1]]`, unconditionally drops the last element of the already-correctly-ordered `songs` list. Since the list is ordered ascending by `position`, the last element is always the song with the highest position — i.e., the most recently added one. This explains both symptoms in the report: the newest song is always the one missing, and when another song is added, the previously-last song (now not last) becomes visible while the brand-new song (now last) becomes the one that's hidden.

**My fix and side-effect check**: Changed the return statement to `[song.to_dict() for song in songs]`, removing the slice entirely. I checked the empty-playlist case: `songs[:-1]` and `songs` are both `[]` when `songs` is empty, so `tests/test_playlists.py::test_empty_playlist_returns_empty_list` is unaffected. I also checked `test_playlist_returns_songs_in_order`, which asserts the 5 titles come back in exact position order — the ordering logic (`order_by`) wasn't touched, only the slice at the very end, so ordering is preserved. The repo's own `test_playlist_returns_all_songs` (comment: "Bug causes this to return 4") is the direct regression test for this exact fix.

---

## Regression Tests

- `tests/test_streaks.py::test_streak_increments_on_sunday`, `tests/test_search.py::test_search_no_duplicates_multi_tag_song`, and `tests/test_playlists.py::test_playlist_returns_all_songs` were already present in the starter repo and encode the exact before/after behavior for Issues #1, #3, and #5 respectively — I verified by hand (see RCA entries above) that each would fail against the pre-fix code and pass against the fixed code.
- I added two new files for the issues that had no existing coverage: `tests/test_feed.py` (Issue #2 — `test_listening_now_excludes_late_previous_night_event` is the direct regression test for nova's report) and `tests/test_notifications.py` (Issue #4 — `test_rating_a_friends_song_creates_a_notification` is the direct regression test for aaliya's report).
- Note on my own verification limits: the sandbox I worked in had no network access to install Flask/SQLAlchemy/pytest, so I could not execute `pytest tests/` directly. I verified the underlying logic for every fix with dependency-free reproductions described in the AI Usage section above, but running the full suite (`pytest tests/ -v`) locally is the last step and should be done before submitting to confirm everything is green end to end.

## Commits on `bugfix/mixtape`

```
fix: return all songs in a playlist, including the most recently added
fix: create a notification when a song is rated
fix: deduplicate search results for songs with multiple tags
fix: shrink Friends Listening Now window from 24h to 1h
fix: correct Sunday boundary condition in streak reset logic
chore: normalize line endings to LF
Add .gitignore file and update README with setup instructions
initial commit
```
