#!/bin/zsh
# Daily summary bot — posts the sourcing status snapshot to the Lark group.
# Scheduled a few minutes after the morning track.py run so it reflects fresh
# replies. Logs to ~/.abaka/summary.log.
cd "/Users/yulingliu/Desktop/Data Sourcing" || exit 1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') =====" >> "$HOME/.abaka/summary.log"
/usr/bin/python3 tools/lark.py summary --chat oc_02dc4b5af12f5a08524ffd588e6d6842 >> "$HOME/.abaka/summary.log" 2>&1
