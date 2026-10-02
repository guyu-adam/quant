#!/usr/bin/env bash
# P3-10：Mac 端每 INTERVAL 秒 ssh 到 Win 采一行资源（scripts/sample_resources.py），追加到 CSV，直到 UNTIL（epoch 秒）。
#   nohup bash scripts/poll_win_resources.sh out/p3/win_resources.csv 300 $(date -v+25H +%s) >/dev/null 2>&1 &
# ssh 失败的那次记一行 ssh_failed，不中断。
out=${1:?csv}; interval=${2:-300}; until=${3:?epoch}
py='D:\quant6\repo\.venv\Scripts\python.exe D:\quant6\repo\scripts\sample_resources.py --home D:\quant6'
mkdir -p "$(dirname "$out")"
[ -s "$out" ] || ssh win "$py --header" 2>/dev/null | head -1 > "$out"
while [ "$(date +%s)" -lt "$until" ]; do
  row=$(ssh -o ConnectTimeout=20 win "$py" 2>/dev/null | tail -1)
  case "$row" in 20*) echo "$row" >> "$out" ;; *) echo "$(date +%Y-%m-%dT%H:%M:%S),ssh_failed" >> "$out" ;; esac
  sleep "$interval"
done
