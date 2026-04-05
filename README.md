# ArtDB

Система анализа продаж на Streamlit для сети салонов.

## Структура проекта

```text
ArtDB/
├── app/                  # Приложение: Streamlit, аналитика, хранилище, скрипты
│   ├── Dockerfile
│   ├── uploads/
│   └── cache/
├── docker/
│   ├── backup/
│   └── nginx/
│       └── default.conf
├── certbot/
│   ├── conf/
│   └── www/
├── docker-compose.yml
├── .env.example
├── .gitignore
├── deploy.sh
├── backup.sh
└── README.md
```

## Что внутри

- `app/app.py` — основной интерфейс Streamlit
- `app/Dockerfile` — Docker-образ приложения
- `app/auth_store.py` — пользователи, роли, сессии
- `app/salon_data_store.py` — салоны и архив выгрузок
- `app/sales_analytics.py` — расчёты и аналитика
- `app/db.py` — PostgreSQL-слой
- `app/scripts/init_db.py` — инициализация БД и создание первого админа
- `app/scripts/migrate_legacy_store.py` — перенос старых JSON/CSV данных в PostgreSQL
- `app/scripts/telegram_notifier.py` — Telegram-уведомления
- `docker-compose.yml` — прод-стек: Streamlit, PostgreSQL, Nginx, Certbot, backup, Telegram
- `deploy.sh` — быстрые команды для запуска, SSL и обновления
- `backup.sh` — ручной запуск backup

## Локальный запуск без Docker

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r app/requirements.txt
streamlit run app/app.py
```

## VPS Ubuntu 22.04: copy-paste

### 1. Установка Docker

```bash
sudo apt update
sudo apt install -y ca-certificates curl git docker.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
```

Перезайдите в SSH.

### 2. Клонирование проекта

```bash
git clone git@github.com:bamaboy561/ArtDB.git /opt/artdb
cd /opt/artdb
cp .env.example .env
```

### 3. Заполните `.env`

Минимум:

```env
DOMAIN_NAME=analytics.example.com
LETSENCRYPT_EMAIL=admin@example.com
DB_HOST=db
DB_PORT=5432
DB_NAME=artdb
DB_USER=artdb_user
DB_PASSWORD=change-me-very-strong-password
STREAMLIT_SERVER_COOKIE_SECRET=change-me-long-random-secret
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=change-me-admin
INITIAL_ADMIN_DISPLAY_NAME=Администратор
INITIAL_ADMIN_EMAIL=admin@example.com
TG_BOT_TOKEN=your_bot_token
TG_CHAT_ID=your_chat_id
```

### 4. Первый запуск

```bash
mkdir -p app/uploads app/cache certbot/conf certbot/www backups
chmod +x deploy.sh backup.sh
./deploy.sh up
```

Что поднимется:

- `app` — Streamlit-приложение
- `db` — база данных PostgreSQL
- `nginx` — reverse proxy
- `certbot` — обновление сертификатов
- `telegram-bot` — уведомления
- `backup` — cron-бэкапы

### 5. Выпуск HTTPS

DNS домена уже должен смотреть на VPS.

```bash
./deploy.sh ssl
```

### 6. Логи

```bash
./deploy.sh logs app
./deploy.sh logs nginx
./deploy.sh logs db
./deploy.sh logs telegram-bot
```

### 7. Ручной backup

```bash
./backup.sh
```

Файл бэкапа сохраняется в `/opt/artdb/backups`.

### 8. Обновление проекта

```bash
./deploy.sh update
```

Скрипт сам:

- делает `git pull origin main`
- пересобирает контейнеры через `docker compose up -d --build`
- очищает неиспользуемые Docker-образы

## Миграция старых данных

Если нужно перенести старые `users.json`, `salons.json`, `upload_manifest.csv` и архив загрузок:

```bash
docker compose exec app python scripts/migrate_legacy_store.py --truncate
```

## Прод-стек

- Streamlit
- PostgreSQL
- Nginx
- Certbot / Let's Encrypt
- Telegram-бот уведомлений
- cron-бэкапы
