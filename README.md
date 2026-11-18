# Ayla Funding Bot — Paper Research Build

Запуск: `python run.py` (PyCharm/Windows) або `python3 run.py` (Raspberry Pi 4).

## Встановлення

1. Python 3.11+. Встановити `pip install -r requirements.txt`.
2. Скопіювати `.env.example` в `.env`, установити довгий випадковий `WEB_TOKEN`.
3. Запустити `run.py`, відкрити `http://127.0.0.1:8096` (за замовчуванням доступ лише з локального ПК).
4. Ввести `WEB_TOKEN` у вебінтерфейсі. Збір даних автоматичний. Paper Trading вмикається вручну.
5. Не переносити `.env` в архіви або репозиторії. Для RPi можна задати `HOST=0.0.0.0`, але лише у довіреній локальній мережі або через VPN.

Зберігайте **існуючі** `.env` та `data/ayla.sqlite`: оновлення коду не вимагає видалення БД. Не копіюйте БД під час роботи програми без SQLite backup API або зупинки процесу.

## Що реалізовано

- Публічні котирування BingX, премія Mark/Index, ліквідність, SQLite, 24-годинний Z-score з мінімумом 80% спостережень.
- Консервативний відбір сигналів, перевірка bid/ask і часу наступного funding (якщо доступний).
- Paper-only LONG/SHORT, облік входів/виходів, taker-комісій, stop-loss, часу позиції, нормалізації премії, ліміту позицій, cooldown та добового реалізованого PnL.
- Вебсторінка, API з X-Token, Telegram-сповіщення (якщо задано `TELEGRAM_BOT_TOKEN` і `TELEGRAM_CHAT_ID`).
- Запуск через `run.py`, тести `python -m pytest -q`.

## Відомі обмеження — важливо

Це **не** завершена специфікація 1.0 і **не** перевірена прибуткова стратегія. Немає live-ордерів і немає коду для їх надсилання. Не реалізовано WebSocket order book / VWAP, реальні funding settlement, точний intraday historical recovery, повний Research Lab, Telegram-команди, повний захист equity від просадки. Через відсутність розрахунку funding бот блокує нові входи, якщо наступний funding ближче ніж 35 хвилин, або час funding невідомий. Реальна доступність поля `nextFundingTime` у відповіді BingX ще потребує інтеграційної перевірки; за його відсутності Paper Trading залишиться без угод. Після аварійного вимкнення відкриті віртуальні позиції закриваються за першою доступною перевіреною котировкою з позначкою `RECOVERY_GAP` — це не достовірний історичний fill.

Тестування на живому API BingX у цьому середовищі не проводилося. Угоди не відкриватимуться до накопичення 24-годинної історії, а наявність статусу `PAPER_ACTIVE` не означає наявність валідних сигналів.

## Безпека

Ставки, параметри ризику і механізм виконання — дослідницькі. Жодних реальних коштів. Після перенесення на RPi не відкривайте порт 8096 у публічний інтернет.

## Індикатор історії

У сканері показується тривалість спостережуваного вікна та частка збережених 5-секундних зразків за останні 24 години. Історія береться з SQLite, тому перезапуск не скидає прогрес. Позначка ✓ означає, що вікно охоплює 24 години та зібрано щонайменше 80% зразків. Пропуски під час відключення світла зменшують покриття; 24 години від першого запису самі по собі не гарантують готовності.

## Аналітичні вкладки (оновлення)

- **Огляд** — стан підключення, баланс, equity, режим Paper.
- **Сканер** — премія, Z-score, покриття 24-годинної історії.
- **Signal Intelligence** — поточні причини відхилення сигналів та діагностичні знімки не частіше одного на 5 хвилин для кожного символу. Це не кількість реальних сигналів або угод. Діагностика не змінює логіку торгового ядра.
- **Opportunity Radar** — монети за абсолютним Z-score, ліквідність, спред, причини відхилення.
- **Premium Chart** — графік агрегованої історії премії за 1–168 годин (без сторонніх JavaScript-бібліотек).
- **System Health** — останні котирування та записи SQLite, розмір БД, час роботи процесу й орієнтовне використання RAM.
- **Daily Intelligence** — локальний денний звіт Europe/Kyiv, PnL та діагностичні причини за добу. Автоматичне надсилання цього звіту в Telegram ще не реалізоване.
- **Угоди / Події** — як раніше.

### Установлення патча

Зупиніть сервіс (`sudo systemctl stop ayla-funding.service`) або процес `run.py`.
Зробіть резервну копію проєкту, зокрема `data/ayla.sqlite` (при зупиненому процесі).
Скопіюйте файли патча в корінь проєкту із заміною. **Не видаляйте `.env`, `data/` та `.venv`.**
Запустіть сервіс (`sudo systemctl start ayla-funding.service`) або `python3 run.py`, оновіть сторінку з очищенням кешу.
Нова таблиця `signal_diagnostics` створюється автоматично через `CREATE TABLE IF NOT EXISTS`, без очищення наявних таблиць.

Вебвкладки з аналітикою підвантажують дані під час відкриття і оновлюють їх приблизно кожні 10 секунд. Збереження діагностичних знімків відбувається лише під час активного циклу збору. Для інструментів без достатньої історії графік показує доступні точки, а сигнали залишаються в `WARMING_UP`.


## Collector Reliability patch (2026-11-16)
- Each successful REST cycle persists samples at most once per 30 seconds per symbol; no wall-clock modulo condition.
- History readiness uses distinct observed minute buckets in a rolling 24h window (>=1152/1440, 80%), a baseline spanning >=23h55m, and a recent sample (<=120s). Gaps are not filled artificially.
- Z-score uses one sample per observed minute instead of weighting irregular REST polling frequencies; 1152 observed minutes required.
- Existing SQLite, .env, trades and settings are preserved. Old history is reused.
- **Deployment:** stop systemd, back up data/ayla.sqlite* while stopped, overwrite only the patch files, run tests, restart.
- This is a reliability fix, not proof of profitable strategy.


## History Intelligence 2.0

На вкладці **System Health** доступні вибір контракту, погодинна гістограма
24-годинного покриття, кількість відсутніх хвилин, свіжість котирувань,
найбільший розрив і прогноз часу до готовності. API: `GET /api/history/{symbol}`
(заголовок `X-Token`). Дані читаються з наявної SQLite, без міграцій.

**ETA є умовним:** припускає одну заповнену хвилину на кожну майбутню
хвилину й враховує вихід старих хвилин із 24-годинного вікна. Якщо
збір зупиниться, прогноз перестане бути актуальним. Поріг готовності
залишається 80% (1152/1440), потрібні свіжі дані та достатній часовий
розмах. Готовність історії не означає наявність сигналу або відкриття угоди.
Пауза Paper Trading не зупиняє колектор.

## Adaptive Strategy — Research Lab (read-only)

Вкладка **Research Lab** порівнює статистичні вікна 1, 6 та 24 години за локальною SQLite-історією (максимум 72 години). Для кожного вікна вимагається 80% хвилинних спостережень. Розрахунок Z-score використовує лише попередні хвилини, а вхід у дослідницькій симуляції — Mark Price наступної хвилини. Виходи перевіряються через 5, 15 та 30 хвилин, без заповнення пропусків.

**Обмеження:** Mark Price не є виконуваною ціною. Історичні Bid/Ask, spread, funding, ліквідність і ризик-фільтри не відтворюються; комісія 0,05% та умовне прослизання 0,02% за сторону — припущення. Перекривні сигнали не є незалежними угодами, сума proxy-результатів не є портфельним PnL. Це лише первинне порівняння гіпотез, а не доказ прибутковості.

Новий маршрут `GET /api/research/{symbol}` вимагає `X-Token`. Жодних торгових параметрів, paper-станів, бази даних чи `.env` патч не змінює. **Автоматичне дозаповнення історії BingX не реалізоване**: спочатку треба підтвердити наявність API для історичних Mark/Index та коректність даних.


## v3.3 — Market Data Integrity & Paper Execution

- Всі валідні Bid/Ask з публічного загального BingX ticker оновлюються на кожному циклі збору; обсяг торгів теж оновлюється.
- Для відкритих позицій і кандидатів із |Z| ≥ 1.5 використовуються додаткові bookTicker; якщо запит не вдався, використовується лише ще свіжа ticker-котировка.
- Старіші за 10 секунд котирування видаляються з кешу; нові Paper-входи блокуються без свіжого Bid/Ask.
- `/api/status` містить `market_data` із часом останнього ticker, кількістю валідних котирувань, помилками та лічильниками причин відмови Paper.
- Signal Intelligence показує джерело та вік котирування.
- **Не змінено** пороги Z-score/премії, правила funding, ризик-менеджмент, 6-годинне вікно та PAPER ONLY.
- Зміни не торкаються `.env`, SQLite та сесій. Установлювати лише змінені файли з патча.


## v3.4 — Exit Intelligence & Paper Reset
- Exit audit in `exit_decisions`: entry, normalization observations, executed exits; includes Z, premium, bid/ask, net PnL, hold duration. Original STOP_LOSS, TIME_LIMIT and NORMALIZED rules unchanged.
- `/api/exit-research`: read-only observational mark-price proxy for 30/60/120 seconds; not an executable strategy backtest.
- Paper reset in Trades tab: balance, trades, all. Requires Paper paused and no open/recovering positions; creates a consistent SQLite backup in `data/backups/` before each reset. `balance_offset` in `paper_state` preserves balance semantics.
- Market samples, signal diagnostics, configuration, Telegram and `.env` are not deleted by reset.
- Reset requires X-Token authentication and confirmation. Never affects live orders (none supported).
