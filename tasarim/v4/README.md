# Robot gövdesi v0.4 — küre kafa + silindir gövde

`python3 robot_v4.py` → `stl/` içine 7 parça. `python3 kontrol.py` → çakışma raporu. `python3 onizleme.py` → `onizleme.png`.
Boy ≈ 207 mm, gövde Ø110, kafa Ø106.

## Parçalar ve baskı (STL'ler baskı yönünde gelir, döndürme gerekmez)
| Parça | Ne | Baskı | Destek |
|---|---|---|---|
| govde | Pi + HAT + servo kartı + pan servo bölmesi | baş aşağı (üst kenar tablada) | pencere köprüleri için ağaç (sadece tabladan) |
| alt_plaka | Pi 5 ayakları (vida alttan) | düz | yok |
| kapak | pan servo kafesi + servo kartı askısı | üst yüz tablada | yok |
| kelepce | pan servoyu alttan tutar | düz | yok |
| boyun | pan kornasına oturan çatal | tabla aşağıda | yok |
| kafa_on | yüz: ekran + kamera | yüz tablada | yok/az |
| kafa_arka | tilt servo yuvası | ayrım yüzü tablada | ağaç (iç) |

PLA veya PETG, 0.2 mm, 3 duvar, %20 dolgu.

## Gerekenler
- 2 × ST3215 (pan + tilt), 1 × Bus Servo Adapter (A), Pi 5 + Active Cooler + M.2 HAT+
- Camera Module 3 (uzun FFC ≥ 300 mm, Pi 5 için 22→15 pin), GC9A01 1.28" yuvarlak ekran
- 12 V panel DC jak (Ø11 delik, ≤3 mm panel) — ÖLÇ
- M3 heat-set insert × 14 (gövde 4+3, kapak 2, kafa_on 4, kafa_arka 1), M3 vida: 4×8 (alt plaka), 3×8 (kapak), 2×10 (kelepçe), 4×16 (kafa), 1×10 (kafa mili)
- M2.5 × 10–12 vida × 4 (Pi, alttan HAT direklerine), M2 × 6 kendinden kılavuzlu × 4 (kamera)
- Korna vidaları (servo ile gelir) × 8

## Yerleşim
- Ön (-y): yüz; gövdede alt-önde microSD yarığı (cımbız)
- Arka (+y): USB/Ethernet, 12 V DC jak
- Sol (-x): Pi USB-C güç
- Sağ (+x): GPIO penceresi (başlık hizası, HAT üstündeki pinlere kadar)
- Kapakta kablo yarığı: kamera FFC + ekran + tilt servo kabloları

## Montaj sırası
1. Insert'leri havyayla göm (gövde 7, kapak 2, kafa_on 4, kafa_arka mil 1).
2. Pi'yi alt plakaya: M2.5 vidalar alttan, HAT direklerine. Plakayı gövdeye M3 ile.
3. Pan servoyu kapak kafesine alttan sok, kelepçeyle tuttur; servo kartını askıya cırt/zip ile.
4. Kapağı gövdeye (3 × M3). Boyunu pan kornasına (4 vida), korna merkez vidası boyundaki delikten.
5. Tilt servoyu kafa_arka'daki yuvaya önden sok; ekranı ve kamerayı kafa_on'a tak.
6. Kafa kabuklarını 4 × M3×16 ile arkadan birleştir.
7. Kafayı çatalın arasına: sağda korna (4 vida, merkez vidası koldaki delikten), solda M3 mil vidası.

## ÖLÇ (doğrula)
Servo mil konumu (uçtan 11 mm), korna çapı/vida çemberi (Ø20 / Ø14), ekran kartı çapı (Ø40) ve penceresi (Ø33),
kamera lens yüksekliği, Pi+HAT yığın yüksekliği (38 mm), SD yuvasının yeri, DC jak deliği.
