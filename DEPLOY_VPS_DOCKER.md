# Прод-развёртывание на VPS (Ubuntu 22.04)

Стек:
- Docker + Docker Compose
- Streamlit-приложение
- PostgreSQL
- PgBouncer
- Nginx reverse proxy
- Let's Encrypt
- Telegram-уведомления
- cron-бэкапы

## 1. Подготовка сервера

```bash
sudo apt update
sudo apt install -y ca-certificates curl git docker.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```

Перезайдите в SSH после добавления в группу `docker`.

## 2. Клонирование проекта

```bash
git clone https://github.com/bamaboy561/ArtDB.git /opt/sales-analytics
cd /opt/sales-analytics
cp .env.example .env
```

Заполните в `.env`:
- `DOMAIN_NAME`
- `LETSENCRYPT_EMAIL`
- `POSTGRES_PASSWORD`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## 3. Первый запуск стека

```bash
docker compose up -d --build postgres pgbouncer app telegram-bot backup nginx certbot
```

На первом старте Nginx поднимется в HTTP-режиме. Это нормально: контейнер ждёт, пока появится сертификат.

## 4. Выпуск SSL-сертификата

DNS домена должен уже смотреть на ваш VPS.

```bash
docker compose run --rm certbot certonly \
  --webroot \
  -w /var/www/certbot \
  -d your-domain.com \
  --email your-email@example.com \
  --agree-tos \
  --no-eff-email
```

После успешного выпуска сертификата:

```bash
docker compose restart nginx
```

После этого Nginx автоматически перейдёт на HTTPS-конфиг.

## 5. Проверка

```bash
docker compose ps
docker compose logs -f app
docker compose logs -f nginx
docker compose logs -f telegram-bot
```

## 6. Миграция старых локальных данных

Если хотите перенести пользователей, салоны, архив загрузок и сессии из старой файловой версии:

```bash
docker compose exec app python scripts/migrate_legacy_store.py --truncate
```

Важно: сами архивные файлы выгрузок должны быть доступны внутри тома `app_data`. Если переносите проект с локальной машины, сначала скопируйте папку `data/uploads` в том контейнера или в bind mount.

## 7. Что где хранится

- PostgreSQL: метаданные, пользователи, сессии, список салонов, журнал загрузок
- том `app_data`: архивные выгрузки `uploads/`
- том `backups`: резервные копии PostgreSQL и `uploads/`
- том `letsencrypt`: SSL-сертификаты

## 8. Бэкапы

Контейнер `backup` запускает `pg_dump` и архивирует `uploads/` по cron-расписанию из `.env`.

Главные переменные:
- `BACKUP_CRON`
- `BACKUP_RETENTION_DAYS`

Проверить результаты:

```bash
docker compose exec backup ls -lah /backups
```

## 9. Telegram-уведомления

Контейнер `telegram-bot` каждый день отправляет сетевую сводку:
- сколько салонов в системе
- кто загрузил данные сегодня
- кто не загрузил
- общий итог по последнему периоду

Главные переменные:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `TELEGRAM_DAILY_REPORT_HOUR`
- `TELEGRAM_DAILY_REPORT_MINUTE`

Тест вручную:

```bash
docker compose exec telegram-bot python scripts/telegram_notifier.py test --message "Проверка Telegram"
```

## 10. Обновление проекта

```bash
cd /opt/sales-analytics
git pull
docker compose up -d --build
```

## 11. Полезные команды

```bash
docker compose logs -f app
docker compose logs -f postgres
docker compose logs -f pgbouncer
docker compose logs -f nginx
docker compose logs -f backup
docker compose logs -f telegram-bot
```
