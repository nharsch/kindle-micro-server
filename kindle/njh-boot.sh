#!/bin/sh
# Kindle family calendar: boot-time setup (called by /etc/upstart/njh.conf)
LOG=/mnt/us/njh-boot.log
date > $LOG
# /mnt/us (fuse) is noexec; the vfat underneath can be remounted exec
mount -o remount,exec /mnt/base-us >> $LOG 2>&1
# SSH for maintenance: KOReader's dropbear, key-only, port 2222
cp /mnt/base-us/koreader/dropbear /var/tmp/dropbear && chmod 755 /var/tmp/dropbear
iptables -I INPUT -p tcp --dport 2222 -j ACCEPT >> $LOG 2>&1
( cd /mnt/base-us/koreader && /var/tmp/dropbear -E -R -s -p 2222 -P /var/tmp/dropbear.pid ) >> $LOG 2>&1
echo "dropbear rc=$?" >> $LOG
# Dashboard power: never go to the screensaver, frontlight off
lipc-set-prop com.lab126.powerd preventScreenSaver 1 >> $LOG 2>&1
lipc-set-prop com.lab126.powerd flIntensity 0 >> $LOG 2>&1
# PW1: powerd's 0 still leaves the LEDs at hardware level 1; write 0 directly (again after KOReader starts)
FL=/sys/devices/system/fl_tps6116x/fl_tps6116x0/fl_intensity
echo 0 > $FL
setsid sh -c "sleep 90; echo 0 > $FL" < /dev/null > /dev/null 2>&1 &
# KOReader, once the UI has settled (skip with NO_KOREADER)
if [ ! -f /mnt/us/NO_KOREADER ]; then
	setsid sh -c 'sleep 30; cd /mnt/base-us/koreader && KOREADER_DIR=/mnt/base-us/koreader exec sh ./koreader.sh --kual' > /mnt/us/koreader-launch.log 2>&1 < /dev/null &
	echo "koreader scheduled" >> $LOG
fi
