# Развёртывание на Hoster KG

Это приложение лучше разворачивать на VPS, а не на обычном shared-hosting.

Почему:
- приложение работает на Streamlit и требует постоянно запущенного Python-процесса;
- приложению нужен обратный прокси на 80/443 порт;
- данные сейчас хранятся локально в папке `data`, поэтому сервер должен хранить файлы постоянно.

## Рекомендуемый вариант

1. Заказать VPS в Hoster KG.
2. Подключить домен или субдомен.
3. Установить Python, `venv`, `nginx`.
4. Залить проект на сервер.
5. Запустить приложение как `systemd`-сервис.
6. Подключить Nginx и SSL.

## Минимальный сервер

Для старта обычно достаточно:
- 1 vCPU
- 1 GB RAM
- 15 GB SSD

Если салонов будет несколько и отчёты будут загружаться ежедневно, лучше сразу брать план выше.

## Структура на сервере

Рекомендуемый путь:

```bash
/opt/sales-analytics
```

Внутри должны быть:
- `app.py`
- `auth_store.py`
- `sales_analytics.py`
- `salon_data_store.py`
- `requirements.txt`
- папка `data`
- папка `.streamlit`

## Команды установки на VPS

Пример для Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx

sudo mkdir -p /opt/sales-analytics
sudo chown -R $USER:$USER /opt/sales-analytics
```

Загрузите проект в `/opt/sales-analytics`, затем:

```bash
cd /opt/sales-analytics
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Проверка ручного запуска:

```bash
source /opt/sales-analytics/.venv/bin/activate
streamlit run app.py --server.port 8501 --server.address 127.0.0.1 --server.headless true
```

## Автозапуск через systemd

1. Скопируйте файл `deploy/hoster-kg/streamlit-sales-analytics.service`
2. Положите его в:

```bash
/etc/systemd/system/streamlit-sales-analytics.service
```

3. При необходимости замените:
- `User=deploy`
- путь `/opt/sales-analytics`

4. Затем выполните:

```bash
sudo systemctl daemon-reload
sudo systemctl enable streamlit-sales-analytics
sudo systemctl start streamlit-sales-analytics
sudo systemctl status streamlit-sales-analytics
```

## Nginx

1. Скопируйте файл `deploy/hoster-kg/nginx-sales-analytics.conf`
2. Замените `analytics.example.kg` на ваш домен
3. Положите конфиг в:

```bash
/etc/nginx/sites-available/sales-analytics
```

4. Включите сайт:

```bash
sudo ln -s /etc/nginx/sites-available/sales-analytics /etc/nginx/sites-enabled/sales-analytics
sudo nginx -t
sudo systemctl reload nginx
```

## SSL

Если домен уже смотрит на VPS, можно выпустить Let's Encrypt:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d analytics.example.kg
```

## Что важно не забыть

- Папка `data` должна сохраняться на сервере и попадать в бэкапы.
- Если будете обновлять код, не удаляйте `data/users.json`, `data/salons.json`, `data/upload_manifest.csv` и папку `data/uploads`.
- Для нескольких салонов и реальной работы через интернет позже лучше перевести хранение из JSON/CSV в базу данных.

## Обновление приложения

```bash
cd /opt/sales-analytics
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart streamlit-sales-analytics
```
