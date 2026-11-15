#!/bin/bash
# Robotun kulakları için kurulum (bir kez çalıştırılır). Kullanım: bash ~/robot-repo/pi/kulak_kurulum.sh
# - Vosk (internetsiz uyandırma kelimesi algılama) kütüphanesini kurar
# - Türkçe küçük ses modelini (~35 MB) indirir
# - Robot servisini yeniden başlatır
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
MODEL=vosk-model-small-tr-0.3

echo "== 1/3 Vosk kütüphanesi =="
./venv/bin/pip install -q vosk

echo "== 2/3 Türkçe ses modeli =="
mkdir -p models
if [ ! -d "models/$MODEL" ]; then
  wget -q --show-progress -O /tmp/$MODEL.zip "https://alphacephei.com/vosk/models/$MODEL.zip"
  python3 -m zipfile -e /tmp/$MODEL.zip models/
  rm -f /tmp/$MODEL.zip
  echo "  Model indirildi"
else
  echo "  Model zaten var"
fi

echo "== 3/3 Robot yeniden başlıyor =="
sudo systemctl restart robot
sleep 8
sudo journalctl -u robot -n 30 --no-pager | grep -iE "kulak|mikrofon|uyandırma|sözlük" || true
echo
echo "Tamam! Mikrofona \"robot\" de, sonra sorunu sor. Cevap Telegram'a gelecek."
