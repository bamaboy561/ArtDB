# Render Deployment

Если нужен собственный домен без кнопки `Manage app` из Streamlit Community Cloud, самый простой путь для этого проекта — деплой из GitHub на Render.

## Что уже подготовлено

- [render.yaml](C:/Users/user/Documents/TaekwondoProject/render.yaml)
- приложение запускается из [app/app.py](C:/Users/user/Documents/TaekwondoProject/app/app.py)
- PostgreSQL создаётся как отдельный managed database
- выгрузки и локальный кэш хранятся на persistent disk

## Как развернуть

1. Откройте [Render Dashboard](https://dashboard.render.com/).
2. Нажмите `New` -> `Blueprint`.
3. Подключите репозиторий [bamaboy561/ArtDB](https://github.com/bamaboy561/ArtDB).
4. Подтвердите ресурсы из [render.yaml](C:/Users/user/Documents/TaekwondoProject/render.yaml).
5. Заполните приватные переменные:
   - `INITIAL_ADMIN_PASSWORD`
   - `INITIAL_ADMIN_EMAIL`
6. Дождитесь завершения первого деплоя.

## Домен с Hoster KG

1. В Render откройте настройки домена у web service.
2. Оставьте `artisandb.shop.kg` как custom domain.
3. Render покажет точную DNS-запись для подтверждения домена.
4. В Hoster KG создайте именно эту запись.

Не подставляйте A/CNAME вручную наугад: используйте значение, которое покажет Render для вашего сервиса.

## Что получится

- сайт будет открываться на вашем домене
- кнопки Streamlit Community Cloud вроде `Manage app` исчезнут
- деплой будет идти из GitHub без SSH и без ручной настройки сервера
