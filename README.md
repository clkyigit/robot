# 🤖 Robot

Raspberry Pi 5 üzerinde çalışan, kendi kendini tasarlayan küçük bir masa robotu.

- **Beyin:** Claude (sohbet, kişilik, araçlar) + Gemini (görme) + OpenAI (yedek)
- **Göz:** Raspberry Pi Camera Module 3
- **İletişim:** Telegram botu, tarayıcı arayüzü ve Android uygulaması (yolda)
- **Yakında:** yuvarlak/küçük DSI ekranda yüz, mikrofon + hoparlör, servolarla baş hareketi

## Klasörler

| Klasör | İçerik |
|---|---|
| `pi/` | Pi'de çalışan her şey: beyin, kamera, uygulama sunucusu, Telegram botu |
| `pi/web/` | Telefon tarayıcısından kullanılan test arayüzü |
| `android/` | Android uygulaması (sırada) |

## Pi'ye kurulum

```bash
sudo apt install -y git
git clone https://github.com/clkyigit/robot ~/robot-repo
bash ~/robot-repo/pi/kurulum.sh
```

Kurulum betiği:
1. Gerekli paketleri kurar,
2. Eski `~/robot` klasöründeki `.env`, hafıza ve kişilik dosyalarını taşır,
3. Uygulama için bir erişim anahtarı (`API_TOKEN`) üretir,
4. Robotu, Pi açılınca kendiliğinden başlayan bir servis olarak kurar.

Sonra telefonun tarayıcısında `http://PI_IP_ADRESI:8080` adresini aç ve anahtarı gir.

### Güncelleme

```bash
cd ~/robot-repo && git pull && sudo systemctl restart robot
```

### Kayıtları izleme

```bash
journalctl -u robot -f
```

## `.env` dosyası

`pi/.env` içinde (repoya **yüklenmez**):

```
TELEGRAM_TOKEN=...
ANTHROPIC_API_KEY=...
GEMINI_API_KEY=...
OPENAI_API_KEY=...
API_TOKEN=...          # kurulum betiği üretir
```

İsteğe bağlı: `CLAUDE_MODEL`, `CLAUDE_FAST_MODEL`, `GEMINI_MODELS` (virgülle ayrılmış, sırayla denenir), `OPENAI_MODEL`, `ROBOT_PORT` (varsayılan 8080).

## Kulaklar (mikrofon)

INMP441 I2S mikrofon: VDD→pin 1, GND→pin 6, L/R→GND, SCK→pin 12, WS→pin 35, SD→pin 38.
`/boot/firmware/config.txt` içinde `dtoverlay=adau7002-simple` olmalı.

Bir kez: `bash pi/kulak_kurulum.sh` (Vosk ve Türkçe ses modelini kurar).

Robot uyandırma kelimesini (varsayılan **robot**) internetsiz algılar, sonraki cümleyi kaydedip
yazıya çevirir (OpenAI, olmazsa Gemini), cevabı Telegram'a yazar. Telegram'da `/dinle` ve `/sus`.

İsteğe bağlı ayarlar: `WAKE_WORDS`, `EARS=0`, `MIC_DEVICE`, `MIC_GAIN`, `EARS_NOTIFY_WAKE=0`,
`OPENAI_STT_MODELS`. Not: robot dinlerken mikrofon meşguldür; `arecord` testi için önce `/sus`.

## Bilinç: kendini bilme ve günlük

- Robot her mesajda kendi durumunu bilir: sıcaklık, fan, bellek, Wi-Fi, açık kalma süresi, kamera/kulak durumu,
  çalışmayan modeller ve `pi/govde.md` dosyasındaki parça listesi (parça gelince bu dosyayı güncelle).
- Mesajın nereden geldiğini (Telegram, uygulama, ses) ve en son ne zaman konuştuğunuzu bilir.
- Her konuşma `pi/gunluk/` içine kaydedilir. Her gece `DIARY_TIME` (23:30) saatinde kendi günlüğünü yazar,
  ardından kendisi hakkındaki notlarını (`pi/kimlik.md`) günceller. Eski günler için `gecmise_bak` aracını kullanır.
- `TEMP_ALERT` (75 °C) aşılırsa Telegram'dan uyarır.
- Telegram: `/gunluk`, `/gunluk yaz`, `/gunluk 2026-11-15`, `/kimlik`. Uygulama: `GET /api/self`.
- `gunluk/` ve `kimlik.md` kişiseldir, repoya girmez.

## Uygulama API'si

Her istekte `Authorization: Bearer API_TOKEN` başlığı (ya da `?token=`) gerekir.

| Yöntem | Yol | Ne yapar |
|---|---|---|
| GET | `/api/status` | Modellerin durumu, Pi sıcaklığı, kamera durumu |
| POST | `/api/selftest` | Her modeli kısa bir istekle dener |
| POST | `/api/chat` | `{"text": "..."}` → `{"reply", "photos"}` |
| POST | `/api/look` | Fotoğraf çekip anlatır |
| GET | `/api/photo` | Anlık fotoğraf (JPEG) |
| GET | `/api/stream` | Canlı görüntü (MJPEG) |
| GET | `/api/memory` | Kalıcı hatıralar ve sohbet geçmişi |
| DELETE | `/api/history` | Sohbet geçmişini temizler |
| POST | `/api/privacy` | `{"camera": false}` → kamerayı, `{"mic": false}` → kulakları kapatır |
| GET | `/api/ears` | Kulakların durumu ve son sesli konuşmalar |

## Gizlilik

- Sadece ilk yazan Telegram hesabına cevap verir.
- API anahtarsız hiçbir şey döndürmez.
- Kamera kullanılmadığında 30 saniye sonra kendiliğinden kapanır; gizlilik anahtarıyla tamamen kapatılabilir.
- `.env`, `memory.json`, `owner.json` ve `persona.md` repoya hiçbir zaman yüklenmez.

## Android uygulaması

`android/` klasöründeki her değişiklikte GitHub Actions APK'yı otomatik derler ve
[son sürüm sayfasına](https://github.com/clkyigit/robot/releases/tag/son-surum) koyar.
Telefondan oradan `robot.apk`'yı indirip kur. Uygulamada **Ayarlar** sekmesine robotun
adresini (`http://PI_IP:8080`) ve `API_TOKEN` anahtarını gir.

API anahtarları (Claude, Gemini, OpenAI) uygulamada **yoktur**; hepsi Pi'de kalır.
Uygulama sadece Pi'deki sunucuyla konuşur.

> `android/app/debug.keystore` herkese açık standart bir test anahtarıdır; her APK'nın
> eskisinin üzerine kurulabilmesi için sabit tutulur. Gizli bir şey değildir.
