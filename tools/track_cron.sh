#!/bin/zsh
# Tracking agent — run 2x/day via cron/launchd.
# Polls Gmail replies → Lark Base status/activity, then applies the
# 7-day-no-reply → Inactive rule. Logs to ~/.abaka/track.log.
cd "/Users/yulingliu/Desktop/Data Sourcing" || exit 1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') =====" >> "$HOME/.abaka/track.log"
/usr/bin/python3 tools/track.py run --days 2 --inactive-days 7 >> "$HOME/.abaka/track.log" 2>&1
