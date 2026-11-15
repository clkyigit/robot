#!/bin/bash
# Robot kurulumu (Raspberry Pi). Kullanım: bash ~/robot-repo/pi/kurulum.sh
# - Gerekli paketleri kurar
# - Eski ~/robot klasöründeki .env, hafıza ve kişilik dosyalarını taşır
# - Uygulama için API_TOKEN üretir
# - Eski Telegram servisini kapatıp yeni robot servisini kurar
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
OLD="$HOME/robot"
cd "$DIR"

echo "== 1/5 Sistem paketleri =="
sudo apt install -y python3-venv python3-picamera2

echo "== 2/5 Python ortamı =="
if [ ! -d venv ]; then
  python3 -m venv --system-site-packages venv   # kamera kütüphanesini görebilsin
fi
./venv/bin/pip install -q -r requirements.txt

echo "== 3/5 Ayar ve hafıza dosyaları =="
for f in .env memory.json owner.json persona.md; do
  if [ ! -f "$DIR/$f" ] && [ -f "$OLD/$f" ]; then
    cp "$OLD/$f" "$DIR/$f"
    echo "  $f taşındı"
  fi
done
[ -f .env ] || { echo "HATA: .env bulunamadı ($OLD/.env yok)"; exit 1; }
chmod 600 .env

if ! grep -q '^API_TOKEN=' .env; then
  echo "API_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')" >> .env
  echo "  Uygulama anahtarı (API_TOKEN) üretildi"
fi

echo "== 4/5 Servis =="
sudo systemctl disable --now robot-bot 2>/dev/null || true   # eski bot, çakışmasın
sudo tee /etc/systemd/system/robot.service > /dev/null << SERVICE
[Unit]
Description=Robot (uygulama sunucusu + Telegram)
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$DIR
EnvironmentFile=$DIR/.env
ExecStart=$DIR/venv/bin/python robot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE
sudo systemctl daemon-reload
sudo systemctl enable --now robot
sleep 4

echo "== 5/5 Durum =="
systemctl --no-pager --lines=5 status robot || true
IP=$(hostname -I | awk '{print $1}')
echo
echo "Tamam! Telefonun tarayıcısında aç:  http://$IP:8080"
echo "Anahtarı görmek için (ekran görüntüsü paylaşma):  grep API_TOKEN $DIR/.env"
