# Clodex Bot — сервер личного агента

Telegram-бот и серверные скрипты личного агента на Claude Code / Codex: бот (текст, голос, файлы, письма, подпись документов), сторож сервера, почтовый сторож, утренняя сводка, «Вопрос дня», напоминания о выписках, страница входа на сайт.

*Server side of a personal Claude Code / Codex agent: Telegram bot, watchdogs, morning brief. Russian only.*

Пара к расширению [Clodex](https://github.com/antonyRock/clodex). Ставится агентом по комплекту передачи — вручную не рассчитано.

## Как устроено

- В коде нет личных данных: имя, почта, пути, домен — метки `__…__`. Значения лежат на сервере в `/root/.config/agent-settings.json` (образец — `settings.example.json`).
- Пароли и токены — только в `/root/.config/*.env` (права 600): `TG_TOKEN`, `TG_ALLOWED`, `MAIL_USER`, `MAIL_PASS`, `YA_USER`, `YA_PASS`, `APPLE_USER`, `APPLE_PASS`.
- `deploy/render.py` подставляет значения; `deploy/deploy.py` раскладывает файлы по местам и перезапускает службы.

| Папка | Куда на сервере |
|---|---|
| `bot/` | `/root/tg-bot/` |
| `auth/` | `/root/brain-auth/` |
| `night/` | `/root/night/` |
| `bin/` | `/usr/local/bin/` |
| `nginx/brain.conf` | `/etc/nginx/sites-available/brain` |
| `systemd/` | `/etc/systemd/system/` |

## Установка (Ubuntu, root)

```bash
git clone https://github.com/antonyRock/clodex-bot /root/clodex-bot
cp /root/clodex-bot/settings.example.json /root/.config/agent-settings.json
# заполнить значения, chmod 600; секреты — в /root/.config/*.env
python3 /root/clodex-bot/deploy/deploy.py --no-pull
```

Зависимости бота (виртуальное окружение `/root/tg-bot/venv`, faster-whisper, LibreOffice для PDF), nginx, сертификат, Syncthing и Claude Code ставятся по этапам комплекта передачи.

## Обновление

```bash
python3 /root/clodex-bot/deploy/deploy.py          # git pull + выкладка
python3 /root/clodex-bot/deploy/deploy.py --check  # что изменится
```

Правки — только через репозиторий: если файл на сервере правили напрямую, выкладка остановится и покажет его. Старые версии — в `/root/agent-backup/<время>/`.

Откат: `git -C /root/clodex-bot checkout <коммит> && python3 /root/clodex-bot/deploy/deploy.py --no-pull`, затем `git -C /root/clodex-bot checkout main`.

## Под себя

Финансовые скрипты (`cc_statement.py`, `cashback_mail.py`, `statement_nudge.py`) разбирают письма и выписки Т-Банка — для другого банка нужен свой разбор. Список сервисов почтового сторожа — `RULES` и `KEEP_BULK` в `mail_watch.py`; службы, за которыми следит сторож, — `SERVICES` в `guard.py`.

Возможности бота и история — [docs/Возможности бота.md](docs/Возможности%20бота.md).

Лицензия MIT.
