# Robot gövdesi v0.4 — küre kafa + silindir gövde

`python3 robot_v4.py` → `stl/` içine 7 parça. `python3 kontrol.py` → çakışma raporu. `python3 onizleme.py` → `onizleme.png`.
Boy ≈ 232 mm, gövde Ø110 × 114, kafa Ø106.

## Parçalar ve baskı (STL'ler baskı yönünde gelir, döndürme gerekmez)
| Parça | Ne | Baskı | Destek |
|---|---|---|---|
| govde | Pi + HAT + servo kartı + pan servo bölmesi | baş aşağı (üst kenar tablada) | pencere köprüleri için ağaç (sadece tabladan) |
| alt_plaka | taban + dik Pi taşıyıcı + dik servo kartı taşıyıcı | düz | yok |
| kapak | pan servo kafesi | üst yüz tablada | yok |
| kelepce | pan servoyu alttan tutar | düz | yok |
| boyun | pan kornasına oturan çatal, solda 2 × 608ZZ göbeği | tabla aşağıda | yok |
| kafa_on | yüz: ekran + kamera | yüz tablada | yok/az |
| kafa_arka | tilt servo yuvası | ayrım yüzü tablada | ağaç (iç) |

PLA veya PETG, 0.2 mm, 3 duvar, %20 dolgu.

## Gerekenler
- 2 × ST3215 (pan + tilt), 1 × Bus Servo Adapter (A), Pi 5 + Active Cooler + M.2 HAT+
- Camera Module 3 (uzun FFC ≥ 300 mm, Pi 5 için 22→15 pin), GC9A01 1.28" yuvarlak ekran
- 2 × 608ZZ, 1 × M8×30 cıvata + M8 somun (kafanın sol mili)
- **90° açılı USB-C kablo/adaptör** (Pi dik durunca USB-C aşağı bakar; fiş için 12 mm yer var — ÖLÇ)
- 12 V panel DC jak (Ø11 delik, ≤3 mm panel) — ÖLÇ
- M3 heat-set insert × 13 (gövde 4+3, kapak 2, kafa_on 4); M3 vida: 4×8 (alt plaka), 3×8 (kapak), 2×10 (kelepçe), 4×16 (kafa)
- M2.5 × 10–12 × 4 (Pi, önden HAT direklerine)
- M2 × 10 vida + M2 somun × 4 (kamera: vida yüzden, somun kartın arkasında)
- Korna vidaları (servo ile gelir) × 8

## Yerleşim
- Pi 5 **dik**: uzun kenar yatay, kartın alt yüzü öne, HAT arkaya. GPIO üstte, arkaya bakar.
- Arka (+y): GPIO penceresi (başlığın karşısı)
- Sağ (+x): USB + Ethernet
- Sol-arka, altta: USB-C kablo çıkışı. Sol (-x): microSD yarığı (cımbız)
- Sol-ön: 12 V DC jak. Önde dik duran servo kartı (cırt/zip ile)
- Pan servo Pi'nin üstünde, kapağa asılı. Kapakta kablo yarığı (kamera FFC + ekran + tilt servo)

## Montaj sırası
1. Insert'leri havyayla göm (gövde 7, kapak 2, kafa_on 4). M8 somunu kafa_arka'daki altıgen yuvaya içeriden yerleştir.
2. Pi'yi alt plakadaki dik taşıyıcıya: M2.5 vidalar önden, HAT direklerine. Servo kartını öndeki taşıyıcıya bağla.
3. Plakayı gövdeye alttan M3 ile.
4. Pan servoyu kapak kafesine alttan sok, kelepçeyle tuttur. Kapağı gövdeye (3 × M3).
5. Boyunu pan kornasına (4 vida), korna merkez vidası boyundaki delikten. 2 × 608ZZ'yi sol göbeğe dışarıdan bastır.
6. Tilt servoyu kafa_arka'daki yuvaya önden sok; ekranı ve kamerayı kafa_on'a tak. Kabukları 4 × M3×16 ile arkadan birleştir.
7. Kafayı çatala: sağda korna (4 vida, merkez vidası koldaki delikten); solda M8×30 cıvata dışarıdan rulmanlardan geçip içteki somuna. Kafa serbest dönecek kadar sık.

## ÖLÇ (doğrula)
Servo mil konumu (uçtan 11 mm), korna çapı/vida çemberi (Ø20 / Ø14), ekran kartı çapı (Ø40) ve penceresi (Ø33),
kamera lens yüksekliği, Pi+HAT yığın yüksekliği (38 mm), SD yuvasının yeri, açılı USB-C fişinin boyu, DC jak deliği, kamera delik aralığı (21 × 12.5).
