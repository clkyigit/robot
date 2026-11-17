#!/bin/bash
# Yüz ekranı (1.28" GC9A01) kurulumu. Kullanım: bash ~/robot-repo/pi/yuz_kurulum.sh
# Bağlantı: VCC 17, GND 20, SCL 23, SDA 19, CS 24, DC 22, RST 13, BLK 18
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "== 1/3 SPI ve paketler =="
sudo apt install -y python3-spidev python3-lgpio python3-numpy python3-pil > /dev/null
if [ ! -e /dev/spidev0.0 ]; then
  sudo raspi-config nonint do_spi 0
  echo "  SPI açıldı."
fi
if [ ! -e /dev/spidev0.0 ]; then
  echo "  SPI'nin etkinleşmesi için yeniden başlatma gerekiyor: sudo reboot"
  echo "  Açılınca bu betiği tekrar çalıştır."
  exit 0
fi

echo "== 2/3 Servis =="
sudo tee /etc/systemd/system/robot-face.service > /dev/null << SERVICE
[Unit]
Description=Robotun yüzü (GC9A01 ekran)
After=multi-user.target

[Service]
User=$USER
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/face.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE
sudo systemctl daemon-reload
sudo systemctl enable --now robot-face
sleep 4

echo "== 3/3 Durum =="
sudo journalctl -u robot-face -n 6 --no-pager
echo
echo "Ekranda yüz görünmeli. Tüm ifadeleri sırayla görmek için:"
echo "  sudo systemctl stop robot-face && python3 $DIR/face.py --demo   (bitirmek: ctrl+c, sonra sudo systemctl start robot-face)"
