#!/bin/bash
# Fan servisi kurulumu (bir kez). Kullanım: bash ~/robot-repo/pi/fan_kurulum.sh
# Active Cooler GPIO'ya bağlı olmalı: kırmızı pin 4, siyah pin 14, mavi pin 32, sarı pin 36.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "== 1/3 lgpio =="
python3 -c "import lgpio" 2>/dev/null || sudo apt install -y python3-lgpio

echo "== 2/3 Servis =="
sudo tee /etc/systemd/system/robot-fan.service > /dev/null << SERVICE
[Unit]
Description=Robot fanı (GPIO, sıcaklığa göre hız)
After=multi-user.target

[Service]
User=$USER
ExecStart=/usr/bin/python3 $DIR/fan.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE
sudo systemctl daemon-reload
sudo systemctl enable --now robot-fan
sleep 6

echo "== 3/3 Durum =="
sudo journalctl -u robot-fan -n 5 --no-pager
cat /dev/shm/robot-fan.json 2>/dev/null && echo
echo
echo "Fanı tam hızda denemek için: sudo systemctl stop robot-fan  (servis durunca fan tam hız döner)"
echo "Tekrar başlatmak için:      sudo systemctl start robot-fan"
