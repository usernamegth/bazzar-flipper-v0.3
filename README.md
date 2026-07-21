# Bazaar Flip Scanner (GitHub Pages edition)

Fully free, no server needed. A scheduled GitHub Actions workflow fetches the
Hypixel Skyblock Bazaar API, filters for items with high liquidity and a wide
instant-buy/instant-sell spread, flags likely price manipulation, and commits
the result to `docs/data.json`. GitHub Pages serves a static page
(`docs/index.html`) that just reads that file.

Default filter:
- Estimated volume of **60,000+** over **5 days**, on both the buy side and sell side
- Spread of **20%+** between instant-buy and instant-sell price
- Ranked by **average of instant-buy and instant-sell price, highest first**
- Any item whose instant-buy or instant-sell price has swung **>100%** in the
  last **10 minutes**, while its current instant-sell price is **over 1,000
  coins**, is flagged red as **"likely manipulation"** and shows its price
  before the change

## Setup (one-time)

1. **Push this folder to a GitHub repo** (see the earlier steps you already used, or:
   `git init && git add . && git commit -m "Initial commit" && git remote add origin <your-repo-url> && git push -u origin main`).

2. **Allow the workflow to commit back to the repo.**
   Go to your repo's **Settings -> Actions -> General -> Workflow permissions**,
   select **"Read and write permissions"**, and save. (Without this, the
   workflow can fetch data but will fail to push `data.json` back.)

3. **Turn on GitHub Pages.**
   Go to **Settings -> Pages**. Under "Build and deployment", set:
   - Source: **Deploy from a branch**
   - Branch: **main**, folder: **/docs**

   Save. GitHub will give you a URL like
   `https://your-username.github.io/your-repo-name/` within a minute or two.

4. **Run the workflow once manually** so you don't have to wait for the schedule.
   Go to the **Actions** tab -> **Update Bazaar Data** (left sidebar) ->
   **Run workflow** button -> **Run workflow**. It takes a few minutes now
   (see the refresh cadence note below), rather than a few seconds.

5. Visit your Pages URL. You should see live data. If it still says "No data
   yet", give it a minute - GitHub Pages caches through a CDN and can lag
   slightly behind a fresh commit.

After that, it runs itself: no further action needed.

## Refresh cadence: why it's not *exactly* every minute

GitHub enforces a hard **5-minute minimum** between scheduled workflow
triggers - even a `* * * * *` (every-minute) cron entry is silently
throttled to every 5 minutes by GitHub itself. There's no workflow-file
setting that gets around this.

To get close to what you asked for anyway, each 5-minute trigger now runs an
internal loop: it fetches, filters, and commits **4 times, about 60 seconds
apart**, before the job ends. So in practice the published data is usually
30-90 seconds old rather than up to 5 minutes old - as close to "every
minute" as GitHub Actions can get without paying for a third-party scheduler
or always-on server. Hypixel's own bazaar data only updates ~once a minute
server-side anyway, so this is close to the real ceiling of usefulness.

One side effect: this means up to 4 commits per 5-minute window (so, up to
~1,150/day) instead of one every 30 minutes. See "commit history" note below.

## How manipulation detection works

Every run, the script also appends the current instant-buy/instant-sell
price for **every** bazaar product (not just ones currently passing the
volume/spread filter) to `price_history.json`, keeping roughly the last 25
minutes of samples per item.

For each item that's currently in the filtered results, it looks back for a
price sample from ~10 minutes ago and compares it to the current price. If
either the buy price or the sell price has more than doubled *or* more than
halved since then (a "100%+ swing" in either direction), **and** the current
instant-sell price is over 1,000 coins, the item is flagged:

- The row is highlighted red with a **"likely manipulation"** badge
- The price before the change is shown alongside the current price

Two things worth knowing:
- **It needs ~10 minutes of history to start working.** Right after your
  first run (or after a long gap, e.g. the 60-day auto-disable kicking in),
  there won't be a 10-minutes-ago sample yet, so nothing will be flagged
  until history builds back up. This is intentional - better to say nothing
  than guess wrong.
- **This is a heuristic, not proof of manipulation.** A genuine, big
  legitimate event (a new recipe release, patch notes, an event ending) can
  also swing a price this much. Treat the flag as "look closer here," not as
  a verdict.

## How the "5-day volume" figure works

Hypixel's Bazaar API doesn't hand you a 5-day number directly - it gives you
`buyMovingWeek` / `sellMovingWeek`, the actual traded volume over the
trailing **7** days, live and server-maintained. This scales that down
(`x 5/7`) to estimate a 5-day figure. It's an estimate, not a true 5-day
rolling window, but it means the dashboard is useful from the very first run
instead of needing 5 real days of your own polling before it can show
anything.

## Changing the filter thresholds, sort, or schedule

Edit `.github/workflows/update-bazaar.yml`:

```yaml
on:
  schedule:
    - cron: "*/5 * * * *"   # this is the fastest GitHub allows - see note above

...

      - name: Fetch, check, and commit every ~60s (4x per trigger)
        env:
          VOLUME_THRESHOLD: "60000"          # <- change thresholds here
          SPREAD_THRESHOLD_PCT: "20"
          WINDOW_DAYS: "5"
          POLL_INTERVAL_MINUTES: "1"          # just used to label the page's countdown
          MANIPULATION_WINDOW_MINUTES: "10"
          MANIPULATION_PCT_THRESHOLD: "100"
          MANIPULATION_MIN_SELL_PRICE: "1000"
```

Sort order (by average price, highest first) lives in `compute_filtered()`
in `scripts/poll_bazaar.py` if you want to change it to spread or volume
instead - it's one `.sort(key=..., reverse=True)` line.

Commit the change and it takes effect on the next scheduled (or manual) run.

## A few things worth knowing

- **Public repos get free Actions minutes**; a private repo has a monthly
  free allowance too (2,000 minutes/month as of writing). At ~4 short
  Python calls plus some sleeping per 5-minute trigger, this comfortably
  fits within either.
- **GitHub disables scheduled workflows automatically after 60 days with no
  repository activity.** If you go quiet on the repo for two months, just
  re-enable it from the Actions tab (or push any commit) to wake it back up.
- **Commit history grows faster now** - up to 4 commits per 5-minute window.
  That's normal and harmless for a repo like this, but if the commit log
  bothers you, you can periodically squash it, or move to a dedicated "data"
  branch you force-push instead - not necessary to start, just an option.

## Project layout

```
.github/workflows/update-bazaar.yml   Scheduled job: loops fetch/filter/commit ~4x per trigger
scripts/poll_bazaar.py                 Fetch + filter + manipulation-detection logic
docs/index.html                        The dashboard page (served by GitHub Pages)
docs/style.css
docs/app.js                            Reads docs/data.json and renders the table
docs/data.json                         Generated/overwritten by the workflow
price_history.json                     Rolling ~25-minute price log, used for manipulation detection
requirements.txt                        Just `requests`, for the workflow's Python step
```

## Extending it

- **True 5-day volume**: use `price_history.json`'s samples (or a longer-
  retention version of it) to compute an exact windowed volume from your own
  data instead of the 7/5 scaling estimate.
- **Discord bot**: `compute_filtered()` in `scripts/poll_bazaar.py` is
  already decoupled from the file-writing - straightforward to reuse it in a
  `discord.py` bot loop instead of (or alongside) the website.
- **More alerts**: diff each run's `items` against the previous commit's and
  flag items that just started (or stopped) qualifying, separately from the
  manipulation check.
