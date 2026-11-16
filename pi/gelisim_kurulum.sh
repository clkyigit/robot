#!/bin/bash
# Robotun kendini geliştirmesi için kurulum (bir kez). Kullanım: bash ~/robot-repo/pi/gelisim_kurulum.sh
# - Robot servisine açılış korumasını ekler (yeni kodla açılamazsa eski sürüme döner)
# - İsteğe bağlı: GitHub anahtarı (robot kendi değişikliklerini GitHub'a da göndersin diye)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
UNIT=/etc/systemd/system/robot.service

echo "== 1/3 Açılış koruması =="
if ! grep -q selfdev_guard "$UNIT"; then
  sudo sed -i "/^ExecStart=/i ExecStartPre=-$DIR/venv/bin/python $DIR/selfdev_guard.py" "$UNIT"
  sudo systemctl daemon-reload
  echo "  eklendi"
else
  echo "  zaten var"
fi
mkdir -p "$DIR/.selfdev"

echo "== 2/3 GitHub anahtarı (isteğe bağlı) =="
if grep -q '^GITHUB_PUSH_TOKEN=' "$DIR/.env"; then
  echo "  zaten var"
else
  echo "  Robot kendi değişikliklerini GitHub'a da göndersin istiyorsan anahtarı yapıştır."
  echo "  İstemiyorsan sadece Enter'a bas (değişiklikler sadece Pi'de kalır)."
  read -rsp "  GitHub anahtarı (ekranda görünmez): " GH
  echo
  if [ -n "$GH" ]; then
    echo "GITHUB_PUSH_TOKEN=$GH" >> "$DIR/.env"
    echo "  kaydedildi"
  else
    echo "  atlandı"
  fi
fi

echo "== 3/3 Robot yeniden başlıyor =="
sudo systemctl restart robot
sleep 3
systemctl --no-pager --lines=0 status robot | head -3 || true
echo
echo "Tamam! Telegram'da /gelisim yazarak durumu görebilirsin."
