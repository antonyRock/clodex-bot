#!/bin/bash
# Разовая проверка: открыла ли VDSina исходящий SMTP (заявка Z1K-1L3E4F94). Сообщает в Telegram и выключается.
set -a; . /root/.config/tg-bot.env; set +a
IP=$(getent ahostsv4 smtp.gmail.com | head -1 | cut -d" " -f1)
if timeout 8 bash -c "</dev/tcp/$IP/465" 2>/dev/null; then
  OK587=$(timeout 8 bash -c "</dev/tcp/$IP/587" 2>/dev/null && echo "и 587" || echo "(587 пока закрыт)")
  curl -s "https://api.telegram.org/bot$TG_TOKEN/sendMessage" --data-urlencode "chat_id=__TG_ID__" \
    --data-urlencode "text=✅ VDSina открыла почтовые порты (465 $OK587). Теперь бот отправляет письма сам после вашего «да». Хотите проверить — напишите боту: «отправь мне тестовое письмо»." >/dev/null
  echo "$(date) порт открыт" >> /root/tg-bot/bot.log
  systemctl disable --now smtp-port-watch.timer
fi
