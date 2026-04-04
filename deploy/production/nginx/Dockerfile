FROM nginx:1.27-alpine

COPY deploy/production/nginx/entrypoint.sh /entrypoint.sh
COPY deploy/production/nginx/templates /etc/nginx/custom-templates

RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

