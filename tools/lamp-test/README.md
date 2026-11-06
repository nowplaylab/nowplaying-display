# Проверка подсветки без настоящих ламп

`fake_yandex.py` — подставной Яндекс (вход по коду + Умный дом с двумя лампами: hsv и rgb) на 127.0.0.1:47840.
Код «подтверждается» сам через 6 секунд. `GET /` показывает состояние ламп и журнал команд.

```
pc/.venv/Scripts/python tools/lamp-test/fake_yandex.py      # в отдельном окне
pc/.venv/Scripts/python tools/lamp-test/lamp_test.py        # 21 проверка, в конце FAILS 0
```

Окно настроек с подставным Яндексом (тестовый порт, свои настройки во временной папке):

```
NP_HTTP_PORT=47811 NP_UDP_PORT=47812 LOCALAPPDATA=<временная папка> \
NP_YA_CLIENT_ID=test-client NP_YA_CLIENT_SECRET= NP_YA_OAUTH=http://127.0.0.1:47840 NP_YA_IOT=http://127.0.0.1:47840 \
pc/.venv/Scripts/python pc/server.py
```
и открыть http://127.0.0.1:47811/settings → «Подсветка».
