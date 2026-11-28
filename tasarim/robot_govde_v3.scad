// =====================================================================
//  Masaüstü robot — v0.3  (Meshy modelinin görünümü + ölçülü iç düzen)
//
//  Görünüm: küre kafa, büyük yuvarlak yüz, üstte kamera siperliği,
//           yanlarda "kulak" diskleri, eğik ağızlı silindir gövde,
//           kalın yuvarlak taban (Pi 5 burada yatay durur).
//  İç düzen: Pi 5 tabanda (alt kapaktan erişilir), bus servo kartı gövde
//           arka duvarında, pan servo gövde kapağına asılı (6808 rulman),
//           tilt servo kafanın içinde (karşı tarafta 608 + M8).
//
//  Parçalar (6 baskı): govde, alt_plaka, kapak, boyun, kafa_on, kafa_arka
//  PARCA = "montaj" -> bütün robot; egim / pan ile kafayı oynat.
//  "ÖLÇ" yazan değerler parçalar gelince kumpasla doğrulanacak.
//  Birim mm. govde/alt_plaka/kapak PETG; boyun/kafa PLA veya PETG.
// =====================================================================

PARCA = "montaj"; // [montaj, govde, alt_plaka, kapak, boyun, kafa_on, kafa_arka]
egim = 0;   // montaj: kafa eğimi (derece, + = yukarı)
pan  = 0;   // montaj: pan açısı

$fn = 72;
bosluk = 0.3;
duvar  = 2.4;

// ---------------- Servo: Waveshare ST3215 ---------------------------
servo_L = 45.2;  servo_W = 24.7;  servo_H = 35.0;
servo_mil_uctan   = 11;   // ÖLÇ
servo_mil_cikinti = 6;    // ÖLÇ: gövde yüzü -> horn dış yüzü
horn_cap          = 20;   // ÖLÇ
horn_vida_cember  = 14;   // ÖLÇ
horn_vida         = 2.2;  // ÖLÇ

// ---------------- Pi 5 (portlar arkaya, +y) -------------------------
pi_yigin = 38;            // ÖLÇ: Pi + Active Cooler + M.2 HAT+
plaka_t = 3;  pi_ayak = 5;
pi_kart_z = plaka_t + pi_ayak;         // kartın alt yüzü
// GPIO başlığı -x uzun kenarda (USB-C +x, USB/Ethernet +y). Pin 1 portların karşı ucunda.
gpio_y0 = -42.5 + 7;  gpio_L = 51;     // ÖLÇ: başlığın y aralığı

// ---------------- Ekran / kamera ------------------------------------
ekran_pencere = 33;  ekran_kart = 39;  // ÖLÇ: kart çapı
kam_delik = [21, 12.5];  kam_lens = 12;  kam_lens_ust = 9.5;

// ---------------- Rulmanlar -----------------------------------------
r6808 = [40, 52, 7];
r608  = [8, 22, 7];

// ---------------- Taban + gövde -------------------------------------
taban_R = 54;   taban_H = 44.4;        // kalın taban (Pi burada)
govde_R = 44;   govde_ic = govde_R - duvar;
kapak_ust = 100;                       // pan kapağının üst yüzü (yerden)
kapak_t = 9;
agiz_on = kapak_ust + 1;               // gövde ağzı önde
agiz_arka = 120;                       // gövde ağzı arkada (eğik ağız)

// ---------------- Boyun ---------------------------------------------
horn_ust = 0.8;                        // horn üstü, kapak üstünden
tabla_D = 66; tabla_t = 5;
eksen_z = tabla_t + 44;                // tilt ekseni, boyun tabanından
kol_t = 4; kol_W = 20; kulak_D = 40;

// ---------------- Kafa (orijin = tilt ekseni) -----------------------
kafa_R = 45;                           // küre yarıçapı
C = [0, -6, 12];                       // küre merkezi (eksene göre)
kafa_yan = 34;                         // yan düzlükler ±x
kafa_ust = 37;                         // üst/alt düzlükler, merkezden
kafa_on_y = C[1] - 33.5;               // yüz düzlüğü (yüz halkası Ø60)
bolme_y = -13;                         // ön/arka kabuk ayrım düzlemi
ekran_z = 9;                           // ekran merkezi
kam_x = 10;  kam_z = 43;               // kamera lensi
led_x = -10;                           // siperlikteki ikinci halka (LED yuvası)
siper = [40, 16, 31, 55.5];            // genişlik, derinlik, alt z, üst z

horn_yuz_x = kafa_yan + 0.5;
sv_x0 = horn_yuz_x - servo_mil_cikinti - servo_H;
sv_z0 = -servo_mil_uctan;

// =====================================================================
//  1) GÖVDE — kalın taban + eğik ağızlı silindir (tek parça, dik basılır)
// =====================================================================
plaka_aci   = [20, 160, 200, 340];
kapak_kule  = [210, 320, 90];
module govde() {
    difference() {
        union() {
            // taban kabuğu (iç tavan 45° eğimli: desteksiz basılır)
            rotate_extrude() polygon([[taban_R - duvar, 0], [taban_R, 0], [taban_R, taban_H - 6],
                [taban_R - 4, taban_H], [govde_R, taban_H], [govde_R, taban_H + 2],
                [govde_ic, taban_H + 2], [govde_ic, taban_H - 2.4], [taban_R - duvar, taban_H - 12.4]]);
            // gövde tüpü
            translate([0, 0, taban_H]) difference() {
                cylinder(r = govde_R, h = agiz_arka - taban_H);
                translate([0, 0, -1]) cylinder(r = govde_ic, h = agiz_arka);
            }
        }
        // eğik ağız
        translate([0, -govde_R, agiz_on]) rotate([atan((agiz_arka - agiz_on) / (2*govde_R)), 0, 0])
            translate([-100, -50, 0]) cube([200, 300, 100]);
        // Pi portları (arka, +y): USB + Ethernet
        translate([-27, 38, pi_kart_z - 1]) cube([54, 30, 20]);
        // Pi USB-C (+x)
        translate([38, -31.3 - 6, pi_kart_z]) cube([30, 12, 9]);
        // GPIO penceresi (-x): 40 pin başlığın hizası, dışarıdan jumper takılır
        translate([-taban_R - 5, gpio_y0 - 4, pi_kart_z + 1]) cube([12, gpio_L + 8, 21]);
        // 12V kablo girişi (gövde arka)
        translate([30, 20, 62]) rotate([-90, 0, 0]) cylinder(d = 11, h = 40);
        // yan "hoparlör" ovalleri = havalandırma yarıkları (±x)
        for (s = [-1, 1]) for (z = [58:5:92]) hull() {
            translate([s*govde_R, -9, z]) rotate([0, 90, 0]) cylinder(d = 2.6, h = 10, center = true);
            translate([s*govde_R,  9, z]) rotate([0, 90, 0]) cylinder(d = 2.6, h = 10, center = true);
        }
        // taban arka-üst ızgara (modeldeki ön yaylı yarıklar yerine tabanda)
        for (a = [-50:8:50]) rotate(a - 90) translate([taban_R - 3, 0, 20]) cube([8, 2.5, 12], center = true);
    }
    // alt plaka vida kuleleri (M3 insert, alttan vidalanır)
    for (a = plaka_aci) rotate(a) translate([taban_R - 6, 0, plaka_t]) difference() {
        union() { cylinder(d = 8, h = 9); translate([0, 0, 9]) cylinder(d1 = 8, d2 = 0.5, h = 5); }
        translate([0, 0, -1]) cylinder(d = 4, h = 7); }
    // kapak kuleleri (M3 insert)
    for (a = kapak_kule) rotate(a) translate([govde_ic - 2.5, 0, 0]) {
        translate([0, 0, kapak_ust - kapak_t - 6]) difference() {
            cylinder(d = 8, h = 6); translate([0, 0, 0.5]) cylinder(d = 4, h = 7); }
        translate([0, 0, kapak_ust - kapak_t - 11]) cylinder(d1 = 0.5, d2 = 8, h = 5);
    }
    // bus servo kartı bloğu: arka duvar, düz yüz y = 33 (ÖLÇ: kart boyu; bant veya kelepçe)
    difference() {
        intersection() {
            cylinder(r = govde_ic + 0.5, h = 200);
            hull() { translate([-20, 33, 52]) cube([40, 10, 24]); translate([-20, 38, 44]) cube([40, 5, 1]); }
        }
        for (z = [57, 69]) translate([-25, 35.5, z]) cube([50, 2.5, 4]);
    }
}

// =====================================================================
//  2) ALT PLAKA — Pi 5 ayakları, lastik ayaklar, havalandırma
// =====================================================================
module alt_plaka() {
    difference() {
        cylinder(r = taban_R - duvar - bosluk, h = plaka_t);
        for (a = plaka_aci) rotate(a) translate([taban_R - 6, 0, -1]) {
            cylinder(d = 3.4, h = 5); cylinder(d1 = 6.6, d2 = 3.4, h = 1 + 1.8); }
        // Pi vidaları: alttan M2.5, baş gömülü
        for (x = [-24.5, 24.5], y = [-39, 19]) translate([x, y, -1]) {
            cylinder(d = 2.9, h = plaka_t + 2); cylinder(d = 5.2, h = 1 + 1.6); }
        // dekor çentikler (modeldeki dört kertik)
        for (a = [45:90:315]) rotate(a) translate([taban_R - duvar - 4, -3, -1]) cube([6, 6, 1.6]);
        for (i = [-3:3]) translate([-20, i*8 - 2, -1]) cube([40, 4, plaka_t + 2]);
        for (a = [0:90:270]) rotate(a) translate([36, 0, -0.01]) cylinder(d = 10.5, h = 1.2);
    }
    // Pi 5 ayakları (portlar +y): vida ALTTAN (dışarıdan) girer, HAT'ın ara direklerine vidalanır
    for (x = [-24.5, 24.5], y = [-39, 19]) translate([x, y, plaka_t - 0.01]) difference() {
        cylinder(d = 6.5, h = pi_ayak); translate([0, 0, -5]) cylinder(d = 2.9, h = pi_ayak + 6); }
}

// =====================================================================
//  3) KAPAK — 6808 yuvası + pan servo kafesi (mil yukarı). Ters basılır.
// =====================================================================
module kapak() {   // z = 0 kapağın üst yüzü
    difference() {
        union() {
            translate([0, 0, -kapak_t]) cylinder(r = govde_ic - bosluk, h = kapak_t);
            translate([-servo_mil_uctan - bosluk - duvar, -servo_W/2 - bosluk - duvar, -kapak_t - 28])
                cube([servo_L + 2*(bosluk + duvar), servo_W + 2*(bosluk + duvar), 28]);
        }
        translate([0, 0, -r6808[2]]) cylinder(d = r6808[1] + bosluk, h = r6808[2] + 1);
        translate([0, 0, -kapak_t - 1]) cylinder(d = 46, h = kapak_t + 2);
        translate([-servo_mil_uctan - bosluk, -servo_W/2 - bosluk, -60])
            cube([servo_L + 2*bosluk, servo_W + 2*bosluk, 60 + horn_ust - servo_mil_cikinti]);
        for (x = [-servo_mil_uctan + 4, servo_L - servo_mil_uctan - 8])
            translate([x, -30, -kapak_t - 24]) cube([4, 60, 3.5]);
        for (a = kapak_kule) rotate(a) translate([govde_ic - 2.5, 0, -kapak_t - 1]) {
            cylinder(d = 3.4, h = kapak_t + 2); translate([0, 0, kapak_t - 1]) cylinder(d1 = 3.4, d2 = 6.6, h = 2.01); }
        // dekor halka (modeldeki çukur halka)
        translate([0, 0, -0.8]) difference() { cylinder(r = 36, h = 1); cylinder(r = 34, h = 1); }
    }
}
module pan_servo_govde() translate([-servo_mil_uctan, -servo_W/2, horn_ust - servo_mil_cikinti - servo_H])
    cube([servo_L, servo_W, servo_H]);

// =====================================================================
//  4) BOYUN — tabla + iki kol; kol uçları "kulak" diskleri
// =====================================================================
xs_ic = horn_yuz_x;          xs_dis = xs_ic + kol_t;    // sağ: tilt horn
xl_ic = -kafa_yan - 1.6;     xl_dis = xl_ic - kol_t;    // sol: M8 (+ pul)
module kol(x0) {
    hull() { translate([x0, -kol_W/2, 0]) cube([kol_t, kol_W, 1]);
             translate([x0, 0, eksen_z]) rotate([0, 90, 0]) cylinder(d = kol_W, h = kol_t); }
    translate([x0, 0, eksen_z]) rotate([0, 90, 0]) cylinder(d = kulak_D, h = kol_t);
}
module boyun() {
    difference() {
        union() {
            hull() { cylinder(d = tabla_D, h = tabla_t); translate([xl_dis, -kol_W/2, 0]) cube([xs_dis - xl_dis, kol_W, tabla_t]); }
            kol(xs_ic); kol(xl_dis);
            translate([0, 0, -0.8]) cylinder(d = 43, h = 0.8);
            translate([0, 0, -0.8 - r6808[2]]) cylinder(d = r6808[0] - 0.1, h = r6808[2]);
        }
        translate([0, 0, -10]) cylinder(d = 37, h = 10);
        for (a = [0:90:270]) rotate(a) translate([horn_vida_cember/2, 0, -1]) {
            cylinder(d = horn_vida, h = tabla_t + 2); translate([0, 0, 4]) cylinder(d = 4.5, h = tabla_t); }
        translate([0, 0, -1]) cylinder(d = 7, h = tabla_t + 2);
        rotate(120) rotate_extrude(angle = 120) translate([12.5, -1]) square([5.5, tabla_t + 2]);
        translate([xs_ic - 1, 0, eksen_z]) rotate([0, 90, 0]) {
            for (a = [0:90:270]) rotate(a) translate([horn_vida_cember/2, 0, 0]) cylinder(d = horn_vida, h = kol_t + 2);
            cylinder(d = 7, h = kol_t + 2); }
        translate([xl_dis - 1, 0, eksen_z]) rotate([0, 90, 0]) cylinder(d = 8.4, h = kol_t + 2);
        // kulak dekor halkaları (dış yüz)
        for (x = [xs_dis - 0.8, xl_dis - 0.2]) translate([x, 0, eksen_z]) rotate([0, 90, 0])
            difference() { cylinder(d = kulak_D - 6, h = 1); cylinder(d = kulak_D - 9, h = 1); }
    }
}

// =====================================================================
//  5) KAFA — küre, yüz düzlüğü, siperlik; ön ve arka kabuk olarak basılır
// =====================================================================
module siper_sekil(ice) {
    r = 5 - ice;
    hull() for (x = [-(siper[0]/2 - 5), siper[0]/2 - 5], z = [siper[2] + 5, siper[3] - 5])
        translate([x, kafa_on_y + ice, z]) rotate([-90, 0, 0]) cylinder(r = r, h = siper[1] - ice);
}
module kafa_dis() {
    intersection() {
        translate(C) sphere(r = kafa_R, $fn = 96);
        translate(C) cube([2*kafa_yan, 200, 2*kafa_ust], center = true);
        translate([-100, kafa_on_y, -100]) cube([200, 200, 200]);
    }
    siper_sekil(0);
}
module kafa_ic() {
    intersection() {
        translate(C) sphere(r = kafa_R - duvar, $fn = 96);
        translate(C) cube([2*(kafa_yan - duvar), 200, 2*(kafa_ust - duvar)], center = true);
        translate([-100, kafa_on_y + duvar, -100]) cube([200, 200, 200]);
    }
    siper_sekil(duvar);
    translate([-siper[0]/2 + duvar, kafa_on_y + duvar, 20]) cube([siper[0] - 2*duvar, siper[1] - duvar, 15]);
}
kafa_vidalar = [[-24, 34], [-24, -18], [26, 40], [20, -19]];   // [x, z]
module kafa() {
    difference() {
        union() {
            difference() { kafa_dis(); kafa_ic(); }
            intersection() {
                kafa_dis();
                union() {
                    // servo kafesi (önü açık: servo önden girer, ön kabuk kapatır)
                    translate([sv_x0 - bosluk - duvar, bolme_y, sv_z0 - bosluk - duvar])
                        cube([kafa_yan - (sv_x0 - bosluk - duvar), servo_W/2 + bosluk + duvar - bolme_y,
                              servo_L + 2*(bosluk + duvar)]);
                    // 608 göbeği
                    translate([-kafa_yan + 0.5, 0, 0]) rotate([0, 90, 0]) cylinder(d = 30, h = 10);
                    // ekran kulakları (M2)
                    for (s = [-1, 1]) translate([s*(ekran_kart/2 + 3), kafa_on_y + duvar, ekran_z])
                        rotate([-90, 0, 0]) difference() { cylinder(d = 6, h = 7.5); cylinder(d = 1.8, h = 8); }
                    // kamera ayakları (M2)
                    for (dx = [-kam_delik[0]/2, kam_delik[0]/2], dz = [kam_lens_ust - 2, kam_lens_ust - 2 - kam_delik[1]])
                        translate([kam_x + dx, kafa_on_y + duvar, kam_z + dz]) rotate([-90, 0, 0])
                            difference() { cylinder(d = 5, h = 3); cylinder(d = 1.8, h = 4); }
                    // kabuk birleştirme kuleleri: arka (uzun) + ön (kısa)
                    for (p = kafa_vidalar) translate([p[0], bolme_y, p[1]]) rotate([-90, 0, 0]) cylinder(d = 6, h = 60);
                    for (p = kafa_vidalar) translate([p[0], bolme_y - 7, p[1]]) rotate([-90, 0, 0]) cylinder(d = 6, h = 7);
                }
            }
        }
        // servo cebi (önü açık)
        translate([sv_x0 - bosluk, bolme_y - 1, sv_z0 - bosluk])
            cube([servo_H + 2*bosluk, servo_W/2 + bosluk - bolme_y + 1, servo_L + 2*bosluk]);
        // horn deliği
        translate([sv_x0 + servo_H - 1, 0, 0]) rotate([0, 90, 0]) cylinder(d = horn_cap + 4, h = 20);
        // 608 yuvası (dıştan) + M8 / somun boşluğu
        translate([-kafa_yan - 1, 0, 0]) rotate([0, 90, 0]) cylinder(d = r608[1] + bosluk, h = 1 + r608[2]);
        translate([-kafa_yan - 1, 0, 0]) rotate([0, 90, 0]) cylinder(d = 12, h = 20);
        // ekran penceresi (pahlı) + kart yuvası
        translate([0, kafa_on_y - 0.01, ekran_z]) rotate([-90, 0, 0]) cylinder(d1 = ekran_pencere + 3, d2 = ekran_pencere, h = 1.5);
        translate([0, kafa_on_y - 1, ekran_z]) rotate([-90, 0, 0]) cylinder(d = ekran_pencere, h = 10);
        translate([0, kafa_on_y + duvar - 1, ekran_z]) rotate([-90, 0, 0]) cylinder(d = ekran_kart + 0.6, h = 2);
        // yüz dekor halkası (Ø52-56)
        translate([0, C[1] - 0.01, C[2]]) translate([0, kafa_on_y - C[1], 0]) rotate([-90, 0, 0])
            difference() { cylinder(d = 56, h = 0.8); cylinder(d = 52, h = 0.8); }
        // kamera ve LED halkası
        translate([kam_x, kafa_on_y - 1, kam_z]) rotate([-90, 0, 0]) cylinder(d = kam_lens, h = 10);
        translate([led_x, kafa_on_y - 1, kam_z]) rotate([-90, 0, 0]) cylinder(d = 5.2, h = 10);
        for (x = [kam_x, led_x]) translate([x, kafa_on_y - 0.01, kam_z]) rotate([-90, 0, 0])
            difference() { cylinder(d = 16, h = 0.8); cylinder(d = 14, h = 0.8); }
        // kablo çıkışı (alt, eksene yakın)
        translate([-17, 0, C[2] - kafa_ust - 1]) cylinder(d = 12, h = duvar + 2);
        // yan kulak dekor halkaları (küre merkezli)
        for (s = [-1, 1]) translate([s*kafa_yan, C[1], C[2]]) rotate([0, 90, 0])
            difference() { cylinder(d = 50, h = 1.6, center = true); cylinder(d = 46, h = 1.6, center = true); }
        // birleştirme vidaları: M2x(uzun), arkadan
        for (p = kafa_vidalar) translate([p[0], bolme_y - 8, p[1]]) rotate([-90, 0, 0]) {
            cylinder(d = 1.8, h = 8);                       // ön kabukta pilot
            translate([0, 0, 8]) cylinder(d = 2.3, h = 80);  // arka kabukta geçiş
        }
        for (p = kafa_vidalar) translate([p[0], bolme_y + 6, p[1]]) rotate([-90, 0, 0])
            cylinder(d = 4.4, h = 80);   // vida başı kanalı: M2x14 vida arkadan girer
    }
}
module kafa_on()   intersection() { kafa(); translate([-100, -200 + bolme_y, -100]) cube([200, 200, 200]); }
module kafa_arka() intersection() { kafa(); translate([-100, bolme_y, -100]) cube([200, 200, 200]); }

// =====================================================================
module montaj() {
    color("WhiteSmoke") govde();
    color("Gainsboro") alt_plaka();
    translate([0, 0, kapak_ust]) {
        color("LightGray") kapak();
        color("DimGray", 0.5) pan_servo_govde();
        rotate(pan) translate([0, 0, horn_ust]) {
            color("LightSteelBlue") boyun();
            translate([0, 0, eksen_z]) rotate([egim, 0, 0]) {
                color("White") kafa_on();
                color("Silver") kafa_arka();
                color("DimGray", 0.5) translate([sv_x0, -servo_W/2, sv_z0]) cube([servo_H, servo_W, servo_L]);
            }
        }
    }
    color("SeaGreen", 0.5) translate([-28, -42.5, pi_kart_z]) cube([56, 85, pi_yigin]);
}

if      (PARCA == "montaj")    montaj();
else if (PARCA == "govde")     govde();
else if (PARCA == "alt_plaka") alt_plaka();
else if (PARCA == "kapak")     rotate([180, 0, 0]) kapak();
else if (PARCA == "boyun")     translate([0, 0, 0.8 + r6808[2]]) boyun();        // tabla altı destekli
else if (PARCA == "kafa_on")   translate([0, 0, -kafa_on_y]) rotate([90, 0, 0]) kafa_on();   // yüz tablada
else if (PARCA == "kafa_arka") translate([0, 0, -bolme_y]) rotate([90, 0, 0]) kafa_arka();   // ayrım yüzü tablada
