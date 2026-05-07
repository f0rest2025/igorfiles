# Yandex Object Storage Manager

Desktop-приложение для оператора под Windows/Linux, которое работает с Yandex Object Storage без обязательного ввода static access key / secret key. Встроенной авторизации пользователей приложения нет: окно оператора открывается сразу. Основной сценарий использует IAM token и локальный backend-mediated upload flow: клиент загружает файл только в `/upload/<token>`, а приложение само отправляет объект в bucket от имени оператора.

## Новая архитектура доступа к Yandex Cloud

Основной режим:

- `Yandex CLI profile / IAM token`;
- приложение вызывает локальный `yc iam create-token`;
- HTTP-запросы к Object Storage идут с заголовком `Authorization: Bearer <IAM token>`;
- S3 request signing не используется, поэтому основной поток не зависит от AWS Signature V4 и не должен падать с `SignatureDoesNotMatch`.

Альтернативный режим:

- `Service account JSON / IAM token`;
- приложение читает authorized key JSON, создаёт JWT `PS256`, меняет его на IAM token через IAM API;
- service account JSON не отправляется клиенту.

Legacy-режим:

- `Legacy static access key`;
- оставлен только для совместимости;
- использует `boto3`, static access key / secret key, presigned PUT/GET;
- явно считается менее надёжным режимом.

## Почему IAM token

Yandex Object Storage S3 API поддерживает IAM token authentication. При IAM token запросы не нужно подписывать AWS Signature V4, достаточно Bearer token. Это убирает основной источник ошибок `SignatureDoesNotMatch`, которые часто появляются при presigned PUT: неверный region, endpoint, content-type, canonical request, clock skew или несовпадение заголовков.

## Клиентская загрузка

Новый основной сценарий:

1. Оператор задаёт object key/prefix/TTL/тип файла/лимит размера.
2. Приложение создаёт одноразовый upload session token.
3. Оператор копирует ссылку вида:

```text
http://127.0.0.1:8765/upload/<token>
```

4. Клиент открывает страницу, видит только выбор файла, кнопку загрузки и статус.
5. Клиент отправляет файл в локальный backend приложения.
6. Backend проверяет token, TTL, размер и тип файла.
7. Backend загружает файл в Object Storage через выбранный IAM/legacy backend.

Клиент не получает:

- IAM token;
- static access key / secret key;
- bucket;
- список объектов;
- download/admin API.

Важно: если клиент находится не на компьютере оператора, `public_base_url` должен указывать на адрес, по которому клиент реально видит локальный backend: LAN IP, VPN/tunnel или reverse proxy. По умолчанию backend слушает `127.0.0.1:8765`.

## Большие файлы

Загрузка в Object Storage идёт через `boto3` multipart transfer:

- `multipart_threshold = 8 MB`;
- `multipart_chunksize = 8 MB`;
- `max_concurrency = 3`;
- `connect_timeout = 30`;
- `read_timeout = 1800`;
- retries включены через botocore standard retry mode.

Operator direct upload не читает весь файл в память. Client upload через локальный backend сначала сохраняет входящий файл во временный файл на диск, затем отправляет этот temp file в Object Storage через тот же multipart path. В desktop GUI для прямой загрузки есть progress bar; клиентская upload-страница показывает прогресс передачи файла в локальное приложение и отдельный статус загрузки в Object Storage.

## Регион

Приложение настроено как KZ-only, чтобы старый RU endpoint не ломал авторизацию и не уводил запросы не туда:

- `region = kz1`
- `endpoint = https://storage.yandexcloud.kz`

Старые сохранённые настройки с RU region/endpoint автоматически переводятся на KZ. Для `kz1` приложение использует KZ IAM API при обмене service account JSON на IAM token и добавляет KZ endpoint для `yc CLI`.

## Установщик Windows 11 с GitHub

В проекте есть GitHub Actions workflow:

```text
.github/workflows/windows-installer.yml
```

Он на `windows-latest`:

- ставит Python 3.11;
- ставит зависимости;
- запускает smoke tests;
- собирает desktop `.exe` через PyInstaller;
- собирает установщик через Inno Setup;
- публикует artifact;
- при теге `v*` создаёт GitHub Release.

Чтобы получить Release с установщиком:

```bash
git push origin main
git tag v0.4.2
git push origin v0.4.2
```

После сборки в GitHub Releases появится:

```text
YandexStorageManagerSetup-0.4.2.exe
```

## Запуск из исходников

### Windows 10/11

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\run_windows.bat
```

### Linux

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
sh run_linux.sh
```

Если Tkinter не установлен:

```bash
sudo apt install python3-tk
```

## Настройка подключения

Поля:

- Bucket;
- Prefix;
- Region;
- Endpoint;
- способ аутентификации;
- Yandex CLI profile;
- Service account JSON path;
- Legacy Access Key ID / Secret Key;
- upload server bind host;
- upload server port;
- public base URL;
- debug logs.

Для основного режима достаточно:

- установить и инициализировать Yandex Cloud CLI;
- выбрать `yc_cli`;
- указать profile, если используется не default;
- указать bucket/prefix;
- нажать «Проверить подключение».

Для service account JSON:

- создайте service account authorized key JSON;
- выдайте service account права на bucket, например `storage.viewer` для списка/скачивания и `storage.editor` для загрузки;
- проверьте, что выбран `region = kz1` и `endpoint = https://storage.yandexcloud.kz`;
- выберите файл JSON в GUI;
- проверьте подключение.

## Локальные файлы

Windows:

```text
%APPDATA%\YandexStorageFileManager\
```

Linux:

```text
~/.config/yandex-storage-file-manager/
```

Файлы:

- `desktop_config.json` - operator config без пароля приложения;
- `app.log` - безопасные логи;
- `config.json` - legacy web config, если старый web-режим использовался;
- `desktop_config.secure.json` / `auth.json` - старые файлы прошлой версии с локальным входом, новой версией не используются.

В `yc_cli` режиме static secrets не сохраняются. В `service_account_json` режиме сохраняется путь к JSON, сам JSON остаётся в выбранном месте. В `legacy_static` режиме `Secret Key` не сохраняется на диск вообще: его нужно вводить заново после перезапуска приложения.

## Диагностика

Логируются этапы:

- operator config load;
- IAM auth init;
- token acquire;
- bucket check;
- object list;
- direct upload;
- temp file saved;
- object storage upload started;
- multipart upload started;
- uploaded bytes / progress;
- upload completed;
- upload failed с exception class и traceback;
- upload token generation;
- client upload consume;
- download link generation.

Не логируются открыто:

- password;
- private key;
- secret key;
- полный IAM bearer token;
- JWT.

Debug-режим включается в GUI.

## Smoke tests

```bash
pip install -r requirements-dev.txt
pytest
```

Покрыто:

- operator config без сохранения legacy secret key;
- RU/KZ endpoint config;
- migration старого static config в legacy mode;
- upload token одноразовость;
- object key generation;
- legacy presigned mock;
- multipart transfer config и progress callback;
- temp-file upload для client backend;
- понятное сообщение для SSL write error;
- XML parsing для IAM HTTP list objects;
- FastAPI health handler.

## Legacy-части

Оставлены только для совместимости:

- старый FastAPI operator web UI;
- static access key fields;
- `boto3` legacy backend;
- presigned PUT/GET generation;
- legacy `data:` HTML upload page.

Новый основной сценарий desktop GUI не отдаёт клиенту presigned PUT URL и не требует static access key.
Встроенная авторизация пользователей приложения удалена: приложение открывается сразу, без логина и пароля.

## Известные ограничения

- Backend-mediated upload требует, чтобы клиент мог достучаться до `public_base_url`.
- Local upload/download tokens хранятся в памяти и сбрасываются при перезапуске приложения.
- Service account JSON является чувствительным файлом; приложение хранит путь, но сам файл нужно защищать на диске.
- Windows installer собирается в GitHub Actions на Windows runner, локально на Linux он не собирается.
