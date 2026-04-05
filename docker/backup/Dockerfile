FROM postgres:16-alpine

RUN apk add --no-cache tzdata bash

COPY docker/backup/backup-cron.sh /backup/backup-cron.sh
COPY docker/backup/entrypoint.sh /backup/entrypoint.sh

RUN chmod +x /backup/backup-cron.sh /backup/entrypoint.sh

ENTRYPOINT ["/backup/entrypoint.sh"]

