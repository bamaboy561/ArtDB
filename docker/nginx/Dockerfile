FROM nginx:1.27-alpine

COPY docker/nginx/entrypoint.sh /entrypoint.sh
COPY docker/nginx/default.conf /etc/nginx/custom-templates/default.conf
COPY docker/nginx/default.ssl.conf /etc/nginx/custom-templates/default.ssl.conf

RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]

