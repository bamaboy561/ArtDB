#!/bin/sh
set -eu

mkdir -p /backups

cat > /etc/crontabs/root <<EOF
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
${BACKUP_CRON:-15 3 * * *} /backup/backup-cron.sh >> /var/log/backup-cron.log 2>&1
EOF

exec crond -f -l 2

