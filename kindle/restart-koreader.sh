#!/bin/sh
# Restart KOReader on the Kindle without racing its startup script.
# Usage (from the Mac): ssh -p 2222 root@<kindle> 'sh -s' < kindle/restart-koreader.sh
#
# koreader.sh runs from a copy at /var/tmp/koreader.sh and deletes it on exit. Relaunching
# before the old koreader.sh has exited lets that cleanup delete the new copy, and KOReader
# then blocks on a "startup script has been updated" dialog. So wait for both processes to go.
killall reader.lua 2>/dev/null
i=0
while ps | grep -q -e "[r]eader.lua" -e "[k]oreader.sh" && [ $i -lt 30 ]; do sleep 1; i=$((i+1)); done
if ps | grep -q -e "[r]eader.lua" -e "[k]oreader.sh"; then
	echo "KOReader did not exit after ${i}s; not relaunching" >&2
	exit 1
fi
setsid sh -c 'cd /mnt/base-us/koreader && KOREADER_DIR=/mnt/base-us/koreader exec sh ./koreader.sh --kual' \
	> /mnt/us/koreader-launch.log 2>&1 < /dev/null &
echo "KOReader relaunched (old instance exited after ${i}s)"
