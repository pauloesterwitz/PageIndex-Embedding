#!/bin/bash
# Notify progress to Telegram every 30 min while run_index.py works;
# one final ping when it exits. Needs TELEGRAM env already exported.
LOG="${1:?usage: watch.sh LOGFILE}"
while pgrep -f "run_index.py" >/dev/null; do
  sleep 1800
  python3 ~/telegram-tool/send_progress.py "${OSTWACHT_CHAT_ID:?}" \
    "index: $(grep -c ' -> ' "$LOG") docs | $(tail -c 200 "$LOG" | tr -s '\n ' ' ')"
done
python3 ~/telegram-tool/send_progress.py "${OSTWACHT_CHAT_ID:?}" \
  "index finished: $(grep -c ' -> ' "$LOG") docs, watermark $(date -Iminutes)"
