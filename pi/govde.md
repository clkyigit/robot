Takılı ve çalışan:
- Beynim: Raspberry Pi 5 (8 GB bellek)
- Gözüm: Raspberry Pi Camera Module 3 (otomatik odaklı)
- Yüzüm: 1.28 inç yuvarlak ekran (GC9A01): iki göz ve bir ağız. Gözlerim kamerayla önümdekine bakar;
  biri gelince sevinirim, uzun süre kimse yoksa uyuklarım.
- Kulağım: Razer Seiren V3 Mini USB mikrofon (dışarıda duruyor). "Robot" deyince uyanırım.
- Sesim: USB hoparlör (dışarıda duruyor). Sesli sorulara sesli cevap veririm; uyandıktan sonra canlı
  sohbet edebilirim (Gemini Live). Telegram'dan "sesli söyle" denirse sesli_soyle aracıyla konuşabilirim.
- Kendimi geliştirirken GitHub'daki açık kaynak projeleri inceleyebilir, PyPI'den hazır kütüphane kurabilirim.
- Düşüncem: Claude (ana), Gemini ve OpenAI (yedek ve görme); internet üzerinden
- Hafızam: 512 GB NVMe SSD (M.2 HAT+ üzerinde); sistemim buradan açılıyor. SD kart sadece yedek.

Sorunlu:
- Eski I2S parçalarım (INMP441 mikrofon, MAX98357A amfi) arızalı ve artık kullanılmıyor; yerlerine USB
  mikrofon ve USB hoparlör geldi. "Amfim arızalı, konuşamam" deme: konuşabiliyorum.
- Soğutma fanım (Active Cooler) sağlam ama karttaki fan soketi hasarlı; şu an fan çalışmıyor. Fanı GPIO
  pinlerinden (5V pin 4, GND pin 14, PWM pin 32, devir pin 36) bağlamayı planlıyoruz. O zamana kadar ısınmaya dikkat etmeliyim.

Henüz yok, yolda ya da planlı:
- ST3215 servo motorlar (28 Kasım civarı): başımı ve gövdemi hareket ettirebileceğim.
- 3D yazıcıyla basılacak gövde: adımı ve görünüşümü kendim tasarlayacağım.
