# API-контракты

Базовый путь Admin API — `/api/v1/`. Формат — JSON (UTF-8). Схема OpenAPI
генерируется FastAPI автоматически: `/api/docs` (Swagger UI, только для авторизованных
в продакшене) и `/api/openapi.json`.

## 1. Общие соглашения

**Авторизация.** `Authorization: Bearer <access_token>`. Access-токен — JWT (HS256,
15 минут) с `sub` (user_id), `salon_id`, `role`. Refresh-токен — случайная строка
(256 бит), хранится в БД как хеш, живёт 30 дней, ротируется при каждом обновлении.
Для веб-панели refresh-токен передаётся в cookie `HttpOnly; Secure; SameSite=Strict`
на путь `/api/v1/auth`.

**Изоляция.** `salon_id` берётся только из токена. В путях и телах запросов его нет:
сотрудник не может обратиться к данным другого салона, даже подставив чужой ID — ответ
`404`.

**Ошибки** — единый формат:

```json
{"error": {"code": "slot_taken", "message": "Это время уже занято", "details": {"alternatives": ["2026-11-10T12:00:00+03:00"]}}}
```

| HTTP | Когда |
|---|---|
| 400 | Нарушено бизнес-правило (вне графика, услуга выключена) |
| 401 | Нет токена или он истёк |
| 403 | Недостаточно прав для роли |
| 404 | Объект не найден в салоне пользователя |
| 409 | Конфликт: время занято, дубль телефона, неверный переход статуса |
| 422 | Ошибка валидации (формат FastAPI/Pydantic) |
| 429 | Превышен лимит частоты (`Retry-After`) |

**Списки** — `?limit=50&offset=0` (limit ≤ 200), ответ
`{"items": [...], "total": 123, "limit": 50, "offset": 0}`.

**Даты и время.** Моменты — ISO 8601 со смещением (`2026-11-10T14:00:00+03:00`), в
ответах — в часовом поясе салона. Даты — `YYYY-MM-DD`, время по графику — `HH:MM`.
Деньги — строкой (`"1500.00"`), чтобы не терять точность.

**Лимиты частоты** (Redis): вход — 5 неудачных попыток на e-mail и 20 на IP за 15 минут;
остальной API — 300 запросов в минуту на пользователя; вебхуки — отдельный лимит на
интеграцию.

## 2. Права ролей

| Действие | owner | administrator | master |
|---|:-:|:-:|:-:|
| Настройки салона, каналы, сотрудники | ✓ | — | — |
| Услуги, мастера, графики | ✓ | ✓ | — |
| Клиенты: просмотр и редактирование | ✓ | ✓ | только свои клиенты, без телефона* |
| Записи: все | ✓ | ✓ | только свои |
| Диалоги: просмотр, ответ, назначение | ✓ | ✓ | — |
| Аналитика | ✓ | ✓ | только своя |
| Рассылки: создание | ✓ | ✓ | — |
| Рассылки: подтверждение отправки | ✓ | — | — |
| Удаление (обезличивание) клиента | ✓ | — | — |

\* Видимость телефона клиента для мастера — настройка салона (по умолчанию скрыт).

## 3. Авторизация

| Метод и путь | Тело / ответ |
|---|---|
| `POST /auth/login` | `{email, password}` → `{access_token, refresh_token, user}` |
| `POST /auth/refresh` | `{refresh_token}` (или cookie) → новая пара токенов |
| `POST /auth/logout` | отзывает текущую сессию → `204` |
| `GET /auth/me` | `{id, name, email, role, salon: {id, name, timezone}}` |
| `POST /auth/telegram-link` (+) | одноразовый код для привязки Telegram сотрудника → `{deep_link: "https://t.me/<bot>?start=staff_<код>", expires_at}` |

## 4. Салон, настройки, сотрудники, каналы

| Метод и путь | Назначение |
|---|---|
| `GET /salon`, `PATCH /salon` | Название, описание, адрес, телефон, часовой пояс, валюта, часы работы, `bot_enabled` |
| `GET /salon/settings`, `PATCH /salon/settings` | Параметры записи, тон, тексты, правила отмены, уведомления (поля — [database.md](database.md#31-салон-и-сотрудники)) |
| `GET/POST /users`, `PATCH/DELETE /users/{id}` (+) | Сотрудники салона (удаление = `is_active=false`) |
| `GET /integrations` (+) | Подключённые каналы, без токенов |
| `POST /integrations/telegram` (+) | `{bot_token}` → backend проверяет токен (`getMe`), создаёт интеграцию, ставит вебхук с новым секретом → `{id, bot_username, webhook_url, status}` |
| `POST /integrations/vk` (+) | `{group_id, access_token, secret_key}` → `{id, callback_url, confirmation_required: true}` (этап VK) |
| `POST /integrations/{id}/check` (+) | Проверка: токен действителен, вебхук на месте, последняя ошибка |
| `DELETE /integrations/{id}` (+) | Отключение: снять вебхук, `is_active=false` |

## 5. Клиенты

| Метод и путь | Назначение |
|---|---|
| `GET /clients?search=&channel=&has_upcoming=&last_visit_before=&segment=` | Поиск по имени и телефону, фильтры, пагинация |
| `GET /clients/{id}` | Карточка: данные, каналы, согласия, ближайшие записи |
| `GET /clients/{id}/appointments` | История записей |
| `GET /clients/{id}/conversations` | История обращений |
| `POST /clients` | `{name, phone?, notes?, marketing_consent?}` → `201`; дубль телефона → `409` |
| `PATCH /clients/{id}` | Частичное обновление |
| `DELETE /clients/{id}` | Обезличивание по запросу клиента (только owner) → `204` |

## 6. Услуги и мастера

| Метод и путь | Назначение |
|---|---|
| `GET /services?category=&is_active=` | Список |
| `POST /services`, `PATCH /services/{id}` | `{name, description, price, duration_minutes, category, is_active}` |
| `DELETE /services/{id}` | Если есть будущие записи — `409`; иначе мягкое удаление (`is_active=false`) |
| `GET /masters?service_id=&is_active=` | Список |
| `POST /masters`, `PATCH /masters/{id}` | `{name, description, photo_url, user_id?, is_active}` |
| `PUT /masters/{id}/services` (+) | Полная замена списка услуг мастера: `[{service_id, price_override?, duration_override?}]` |
| `DELETE /masters/{id}` | Как у услуг: `409` при будущих записях |

## 7. Расписание

| Метод и путь | Назначение |
|---|---|
| `GET /schedules?master_id=` | Недельные графики |
| `POST /schedules` | `{master_id, weekday, start_time, end_time, break_start?, break_end?, is_working}` |
| `PATCH /schedules/{id}` | Изменение |
| `GET/POST /schedules/exceptions`, `DELETE /schedules/exceptions/{id}` (+) | Отпуска, выходные, больничные, изменённые часы, доп. смены |
| `GET /schedules/availability` | Свободные слоты |

`GET /schedules/availability?service_id=…&master_id=…&date=2026-11-10`
(`master_id` можно не указывать — тогда «любой мастер»):

```json
{
  "date": "2026-11-10",
  "timezone": "Europe/Moscow",
  "service_id": "…",
  "available_slots": ["10:00", "11:30", "14:00", "16:30"],
  "slots": [
    {"start_at": "2026-11-10T10:00:00+03:00", "master_ids": ["…"]}
  ]
}
```

`available_slots` — формат из ТЗ, `slots` (+) — с мастерами (нужно для «любого мастера»).

## 8. Записи

| Метод и путь | Назначение |
|---|---|
| `GET /appointments?date_from=&date_to=&master_id=&client_id=&status=` | Список (для календаря) |
| `GET /appointments/{id}` | Подробно |
| `POST /appointments` | Создание (атомарно) |
| `PATCH /appointments/{id}` | Перенос (`start_at`, `master_id`), комментарий |
| `POST /appointments/{id}/confirm` (+) | `pending` → `confirmed` (если `auto_confirm=false`) |
| `POST /appointments/{id}/cancel` | `{reason?, notify_client: true}` |
| `POST /appointments/{id}/complete` | → `completed`, обновляет `clients.last_visit_at` |
| `POST /appointments/{id}/no-show` | → `no_show` |

`POST /appointments`:

```json
{"client_id": "…", "service_id": "…", "master_id": "…", "start_at": "2026-11-10T14:00:00+03:00", "comment": "", "notify_client": true}
```

- `201` → запись (цена и `end_at` рассчитаны backend'ом, клиент их не передаёт).
- `400` → вне графика, услугу не оказывает этот мастер, раньше минимального срока.
- `409 slot_taken` → время занято, в `details.alternatives` — ближайшие свободные слоты.

Разрешённые переходы статусов: `pending → confirmed | cancelled`,
`confirmed → completed | cancelled | no_show`. Остальные → `409 invalid_status_transition`.
Сотрудник может создать запись вне графика с флагом `force_outside_schedule: true`
(только owner/administrator; пересечение всё равно запрещено). Каждое изменение пишется
в `audit_logs`.

## 9. Диалоги

| Метод и путь | Назначение |
|---|---|
| `GET /conversations?status=&channel=&search=&assigned_to=` | Список, сначала ожидающие сотрудника |
| `GET /conversations/{id}` | Диалог, клиент, причина передачи, краткое содержание |
| `GET /conversations/{id}/messages?before=&limit=` (+) | Сообщения (постранично, от новых к старым) |
| `POST /conversations/{id}/assign` | `{user_id?}` (по умолчанию — себе) → статус `operator` |
| `POST /conversations/{id}/reply` | `{text}` → отправка клиенту от имени салона; `202` + сообщение со статусом доставки |
| `POST /conversations/{id}/close` | Завершить передачу: статус `bot`, ИИ снова отвечает |
| `POST /conversations/{id}/takeover` (+) | Сотрудник забирает диалог сам, без запроса клиента |

Обновление списка диалогов в панели — опрос раз в 5–10 секунд в MVP (без WebSocket).

## 10. Аналитика

Все методы принимают `date_from`, `date_to` (в часовом поясе салона).

| Метод и путь | Возвращает |
|---|---|
| `GET /analytics/overview` | Записи сегодня, новые обращения, клиенты, отмены, ближайшие записи, выручка по завершённым |
| `GET /analytics/appointments` | Записи по дням, статусам, мастерам, услугам, источникам |
| `GET /analytics/clients` | Новые/постоянные, сегменты 30/60/90 дней |
| `GET /analytics/conversion` | Диалоги → диалоги с намерением записаться → созданные записи; доля переданных сотруднику |

## 11. Рассылки (возврат клиентов)

| Метод и путь | Назначение |
|---|---|
| `GET /campaigns/segments` | Сегменты с количеством клиентов, у которых есть согласие и доступный канал |
| `POST /campaigns` | `{segment, template}` → черновик + предпросмотр |
| `POST /campaigns/{id}/approve` | Только owner: запуск отправки |
| `POST /campaigns/{id}/cancel` | Остановить |
| `GET /campaigns/{id}` | Статистика отправки |

## 12. Публичные эндпоинты

| Метод и путь | Защита |
|---|---|
| `POST /webhooks/telegram/{integration_id}` | Заголовок `X-Telegram-Bot-Api-Secret-Token` = секрет интеграции |
| `POST /webhooks/vk/{integration_id}` | Поле `secret` в теле = секретный ключ интеграции; `group_id` совпадает |
| `GET /health` | Процесс жив |
| `GET /ready` | Доступны PostgreSQL и Redis |

Вебхуки всегда отвечают быстро. Неверный секрет → `403` без подробностей, событие не
сохраняется. Подробности — в [integrations.md](integrations.md).
