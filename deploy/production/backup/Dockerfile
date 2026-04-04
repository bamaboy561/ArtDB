FROM postgres:16-alpine

RUN apk add --no-cache tzdata bash

COPY deploy/production/backup/backup.sh /backup/backup.sh
COPY deploy/production/backup/entrypoint.sh /backup/entrypoint.sh

RUN chmod +x /backup/backup.sh /backup/entrypoint.sh

ENTRYPOINT ["/backup/entrypoint.sh"]

