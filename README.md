# ArtDB

Система анализа продаж на Streamlit для сети салонов.

## Модель доступа

- `admin` — управляет салонами, пользователями и видит всю сеть
- `manager` — видит все салоны и аналитику по сети, но не управляет учётными записями
- `salon` — видит только свои данные и свой архив загрузок

В приложении уже действует ролевая модель: салон не может выйти за пределы своего контура, а административные действия доступны только администратору.

## Чек-лист перед первым запуском

- Замените `your-domain.com` и тестовые домены на реальный домен или IP
- Заполните `.env` сильными паролями и уникальным `STREAMLIT_SERVER_COOKIE_SECRET`
- Убедитесь, что `.env` не попадёт в Git: файл уже добавлен в [`.gitignore`](./.gitignore)
- Добавьте SSH-ключ VPS в GitHub, если сервер будет делать `git pull`
- После запуска выполните `docker compose up -d` и проверьте логи `docker compose logs -f app`
- Настройте `cron` для [backup.sh](./backup.sh)
- В приложении уже нет хардкодных паролей и токенов в `app.py`, а загрузка файлов валидируется по имени, расширению, размеру и сигнатуре

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
- `app/db.py` — PostgreSQL-слой и шифрование чувствительных полей через `pgcrypto`
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

### 1.1. Ограничение доступа через UFW

Для сервера достаточно открыть только SSH, HTTP и HTTPS. Порт Streamlit `8501` и порт PostgreSQL `5432` наружу открывать не нужно.

```bash
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

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
APP_PGCRYPTO_KEY=change-me-long-random-pgcrypto-key
BASIC_AUTH_ENABLED=false
BASIC_AUTH_USERNAME=
BASIC_AUTH_PASSWORD=
BASIC_AUTH_PASSWORD_HASH=
BASIC_AUTH_REALM=ArtDB Protected Area
INITIAL_ADMIN_USERNAME=admin
INITIAL_ADMIN_PASSWORD=change-me-admin
INITIAL_ADMIN_DISPLAY_NAME=Администратор
INITIAL_ADMIN_EMAIL=admin@example.com
TG_BOT_TOKEN=your_bot_token
TG_CHAT_ID=your_chat_id
```

`APP_PGCRYPTO_KEY` обязателен для PostgreSQL-режима: email и телефон пользователей хранятся в базе в зашифрованном виде, а поиск и уникальность работают через хеши.

`BASIC_AUTH_*` — опциональны. Если включить `BASIC_AUTH_ENABLED=true`, то Nginx попросит дополнительный логин/пароль ещё до экрана входа в приложение. Это удобно для закрытого корпоративного контура или временного доступа по VPN/интернету.

Рекомендуемый вариант:

- для быстрого запуска задайте `BASIC_AUTH_USERNAME` и `BASIC_AUTH_PASSWORD`
- для более строгого варианта задайте `BASIC_AUTH_USERNAME` и `BASIC_AUTH_PASSWORD_HASH`, а поле `BASIC_AUTH_PASSWORD` оставьте пустым

Если используете `BASIC_AUTH_PASSWORD_HASH`, это должен быть готовый `htpasswd`-совместимый bcrypt/APR1 хеш.

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

После выпуска сертификата `nginx` сам переключится на HTTPS-конфиг и будет проксировать Streamlit только через TLS.

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

### 7.1. Backup по cron

Если проект лежит в `/opt/artdb/ArtDB`, добавьте задачу в `crontab`:

```bash
0 2 * * * /opt/artdb/ArtDB/backup.sh >> /var/log/artdb_backup.log 2>&1
```

Удобнее всего добавить её так:

```bash
crontab -e
```

Скрипт уже готов к запуску из `cron`: сам загружает `.env`, использует системный `PATH` и сохраняет архивы в каталог backup.

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
- Nginx с HTTPS и reverse proxy
- Certbot / Let's Encrypt
- Telegram-бот уведомлений
- cron-бэкапы
