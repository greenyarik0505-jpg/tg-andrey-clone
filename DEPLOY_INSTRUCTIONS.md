# 🤖 Инструкция для ИИ-агента: Хостинг и развертывание Telegram AI-Clone Bot + Mini App

Данный документ содержит исчерпывающее техническое руководство для любого ИИ-ассистента (Claude, GPT, Cursor, Windsurf и др.) или DevOps-инженера по развертыванию, настройке и поддержке проекта цифрового клона Telegram.

---

## 1. Архитектура и компоненты системы

Проект представляет собой единый гибридный сервис на Python 3.11+:

- **Aiogram 3.31**: Обработка Telegram Bot API в режиме Polling. Перехватывает как стандартные команды админа (`/start`, `/model`, `/setkey`, `/custom`), так и события Telegram Business:
  - `business_connection`: регистрация подключения аккаунта пользователя через системное меню «Автоматизация чатов».
  - `business_message`: чтение входящих/исходящих сообщений в личных переписках человека и отправка ответов от его имени (`business_connection_id`).
- **FastAPI + Uvicorn**:
  - Раздает статику Telegram Mini App (TMA) из папки `webapp/static/index.html`.
  - Предоставляет REST API (`/api/settings`, `/api/chats`, `/api/test`, `/api/stats`).
- **SQLite (`aiosqlite`)**: Локальная асинхронная БД (`bot_data.db`) для хранения вайтлистов/блеклистов чатов, истории переписки, зашифрованных ключей и системных настроек.
- **LLM Engine (`core/llm_client.py`)**: Универсальный коннектор с поддержкой Google Gemini, OpenAI, DeepSeek, Anthropic, Groq, OpenRouter и любых кастомных OpenAI-совместимых эндпоинтов (vLLM, Ollama, Together).
- **Движок очеловечивания (`core/persona.py`)**:
  - Разбивка ответа на 1–3 коротких сообщения (Message Splitter).
  - Рандомизированная задержка ответа + отправка статуса `typing...`.
  - **Human Takeover**: если реальный владелец аккаунта сам написал в чат, бот блокирует автоответы в этом диалоге на $N$ минут.
  - **Safety Trigger**: экстренная остановка и тревога админу при обнаружении слов: деньги, карты, пароли, полиция.

---

## 2. Критические правила безопасности и частые ошибки (Gotchas)

> [!CAUTION]
> ### 1. Утечка токена в публичный Git
> - **НИКОГДА** не коммитить токен бота (`BOT_TOKEN`) в файлы репозитория (включая `render.yaml`, `config.py`, `README.md`).
> - Боты-парсеры сканируют публичные коммиты GitHub за 2–5 секунд. При обнаружении токена они мгновенно перехватывают бота, вызывая `setMyName` и подменяя имя на спам-рекламу.
> - **Правило**: Токен передается **СТРОГО** через переменные окружения хостинга (Environment Variables) или локальный `.env`, который обязательно включен в `.gitignore`.

> [!WARNING]
> ### 2. Telegram Web App требует HTTPS
> - Метод `InlineKeyboardButton(text=..., web_app=WebAppInfo(url=...))` в Telegram Bot API **жестко требует `https://`**.
> - Если передать `http://localhost:8080` или любой незащищенный `http://` адрес, Telegram выдаст фатальную ошибку: `TelegramBadRequest: inline keyboard button Web App URL is invalid: Only HTTPS links are allowed`.
> - Бот упадет при обработке `/start`. В коде `bot/admin.py` кнопка должна добавляться только если `webapp_url.startswith("https://")`.

> [!IMPORTANT]
> ### 3. Активация Business Mode в @BotFather
> - По умолчанию у новых ботов параметр `can_connect_to_business` равен `false`.
> - Чтобы друг мог выбрать бота в меню **«Автоматизация чатов»**, владелец бота обязан выполнить в [@BotFather](https://t.me/BotFather):
>   `/mybots` ➔ Выбрать бота ➔ **Bot Settings** ➔ **Business Mode** ➔ **Turn On**.

---

## 3. Деплой на Render (Пошаговый протокол для ИИ)

Render — рекомендуемый бесплатный облачный хостинг для запуска 24/7.

### Шаг 1. Подготовка репозитория
Убедись, что в репозитории:
1. В `.gitignore` добавлены:
   ```gitignore
   __pycache__/
   *.pyc
   bot_data.db
   .env
   *.log
   *.pdf
   *.html
   !webapp/static/*.html
   ```
2. В `config.py` токен читается из окружения без жестко зашитого фоллбека:
   ```python
   BOT_TOKEN = os.getenv("BOT_TOKEN", "")
   WEBAPP_PORT = int(os.getenv("PORT", os.getenv("WEBAPP_PORT", "8080")))
   ```
3. Репозиторий запушен на GitHub (например, `https://github.com/<username>/<repo>`).

### Шаг 2. Создание сервиса через Render API / CLI

Используй Render API (`POST https://api.render.com/v1/services`) с заголовком `Authorization: Bearer <RENDER_API_KEY>`:

```json
{
  "type": "web_service",
  "name": "andrey-ai-clone",
  "ownerId": "<RENDER_OWNER_ID>",
  "repo": "https://github.com/<username>/<repo>",
  "autoDeploy": "yes",
  "branch": "main",
  "serviceDetails": {
    "env": "python",
    "plan": "free",
    "region": "frankfurt",
    "envSpecificDetails": {
      "buildCommand": "pip install -r requirements.txt",
      "startCommand": "python run.py"
    },
    "envVars": [
      {"key": "BOT_TOKEN", "value": "<TELEGRAM_BOT_TOKEN>"},
      {"key": "ADMIN_ID", "value": "7150170715"},
      {"key": "PYTHON_VERSION", "value": "3.11.9"},
      {"key": "WEBAPP_URL", "value": "https://<SERVICE_NAME>.onrender.com"}
    ]
  }
}
```

### Шаг 3. Проверка статуса запуска
После создания сервиса проверить логи:
```bash
render logs --resources <SERVICE_ID> --limit 50
```
Успешный запуск выглядит так:
```text
[INFO] AndreyClone: 📦 Database initialized successfully.
[INFO] AndreyClone: 🌐 Mini App Dashboard running on http://0.0.0.0:10000
[INFO] AndreyClone: 🤖 Telegram Bot starting polling for Admin ID 7150170715...
[INFO] aiogram.dispatcher: Run polling for bot @Andrey_clon_bot
==> Your service is live 🎉
```

---

## 4. Конфигурация кастомных провайдеров и моделей

Система поддерживает любые OpenAI-совместимые сторонние эндпоинты (Ollama, vLLM, OpenRouter, Together, Groq, Mistral, DeepSeek):

1. **Через Mini App** (`https://<service-name>.onrender.com`):
   - Вкладка **«🤖 Нейросеть»** ➔ Блок **«⚙️ Кастомный провайдер / Модель»**.
   - Поля:
     - `Base URL`: `https://openrouter.ai/api/v1` (или адрес локального vLLM).
     - `Model ID`: `deepseek/deepseek-r1`, `mistralai/mistral-large` и т.д.
     - `API Key`: ключ авторизации провайдера.
2. **Через чат с ботом** (команда админа):
   ```text
   /custom <BASE_URL> <MODEL_ID> <API_KEY>
   ```
   Пример:
   ```text
   /custom https://openrouter.ai/api/v1 deepseek/deepseek-chat sk-or-v1-...
   ```
   Для стандартных провайдеров:
   ```text
   /setkey gemini AIzaSy...
   /setkey deepseek sk-...
   /setkey openai sk-proj-...
   ```

---

## 5. Чек-лист верификации работоспособности (Healthcheck)

ИИ-агент обязан выполнить следующие шаги проверки после деплоя:

- [ ] **1. Проверка Telegram Bot API `getMe`**:
  ```python
  import urllib.request, json
  res = json.loads(urllib.request.urlopen("https://api.telegram.org/bot<TOKEN>/getMe").read())
  assert res["ok"] == True
  ```
- [ ] **2. Проверка вебхука/очереди**:
  ```python
  res = json.loads(urllib.request.urlopen("https://api.telegram.org/bot<TOKEN>/getWebhookInfo").read())
  assert res["result"]["pending_update_count"] == 0
  ```
- [ ] **3. Проверка HTTPS эндпоинта Mini App**:
  ```python
  res = urllib.request.urlopen("https://<SERVICE_NAME>.onrender.com/api/settings")
  assert res.status == 200
  ```
- [ ] **4. Проверка обработки `/start`**:
  Отправить `/start` от имени `ADMIN_ID` (`7150170715`) ➔ бот должен вернуть инлайн-панель со статусом и кнопкой Mini App.
- [ ] **5. Проверка Telegram Business**:
  Друг включает бота в *«Настройки ➔ Автоматизация чатов»* ➔ админу приходит сообщение: `🔗 Telegram Business обновление! Статус: ПОДКЛЮЧЕН`.
