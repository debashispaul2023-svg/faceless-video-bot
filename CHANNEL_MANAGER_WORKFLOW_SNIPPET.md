Adding the AI Channel Manager as a daily check

Add this as a SECOND job in `.github/workflows/daily_bot.yml` (or a new
workflow file) so it runs once a day, separately from video generation:

```yaml
  channel-manager:
    runs-on: ubuntu-latest
    steps:
      - name: Check out repository
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.10'

      - name: Install Python Packages
        run: pip install -r requirements.txt

      - name: Run AI Channel Manager
        env:
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          YOUTUBE_CLIENT_ID: ${{ secrets.YOUTUBE_CLIENT_ID }}
          YOUTUBE_CLIENT_SECRET: ${{ secrets.YOUTUBE_CLIENT_SECRET }}
          YOUTUBE_REFRESH_TOKEN: ${{ secrets.YOUTUBE_REFRESH_TOKEN }}
          # Leave this OUT (or "false") for a few days to review dry-run
          # logs first. Only add CHANNEL_MANAGER_APPLY_CHANGES=true once
          # you trust what it's deciding to do.
          # CHANNEL_MANAGER_APPLY_CHANGES: "true"
        run: python channel_manager.py
```

Requires the SAME one-time re-auth (yt-analytics.readonly scope) described
in analytics_feedback.py — do that once, and both features work.

**Start in dry-run mode.** Read a few days of its logs (Actions tab) before
setting `CHANNEL_MANAGER_APPLY_CHANGES: "true"` — this script can edit live
video titles/descriptions on your real channel.
