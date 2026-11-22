// =====================================================================
//  Kompakt masaüstü robot — v0.2 "ideal" düzen (parametrik OpenSCAD)
//
//  Fikir: tilt servosu KAFANIN İÇİNDE. Boyun sadece iki kollu bir çatal.
//         Pan servosu yuvarlak tabanın kapağına asılı, 6808 rulman yükü taşır.
//         Kablolar pan ekseninin ortasından (horn'un çevresindeki halkadan) iner.
//
//  Parçalar (5 baskı): taban, kapak, boyun, kafa, kafa_arka
//  PARCA = "montaj" -> bütün robot. Basmak için parça adını yaz, F6, Export STL.
//  egim / pan ile montajda kafayı oynatıp çarpma kontrolü yapabilirsin.
//
//  "ÖLÇ" yazan değerler parçalar gelince kumpasla doğrulanacak.
//  Birim mm. Taban/kapak PETG, boyun/kafa PLA veya PETG. 0.2 mm, 3 duvar, %20.
// =====================================================================

PARCA = "montaj";
egim = 0;      // montaj önizlemesi: kafa eğimi (derece, + = yukarı bakar)
pan  = 0;      // montaj önizlemesi: pan açısı

$fn = 72;
bosluk = 0.3;
duvar  = 2.4;

// ---------------- Servo: Waveshare ST3215 ---------------------------
servo_L = 45.2;  servo_W = 24.7;  servo_H = 35.0;   // üretici ölçüsü
servo_mil_uctan   = 11;   // ÖLÇ: mil merkezi -> en yakın kısa kenar
servo_mil_cikinti = 6;    // ÖLÇ: gövde yüzü -> horn dış yüzü
horn_cap          = 20;   // ÖLÇ: horn diski çapı
horn_vida_cember  = 14;   // ÖLÇ: horn vida deliklerinin çember çapı
horn_vida         = 2.2;  // ÖLÇ: horn vida deliği (M2: 2.2, M3: 3.2)

// ---------------- Raspberry Pi 5 ------------------------------------
pi_L = 85; pi_W = 56; pi_dx = 58; pi_dy = 49; pi_kenar = 3.5;
pi_yigin = 38;            // ÖLÇ: Pi + Active Cooler + M.2 HAT+ toplam yükseklik

// ---------------- Ekran / kamera ------------------------------------
ekran_pencere = 33;  ekran_kart = 39;     // ÖLÇ: yuvarlak kart çapı
kam_delik = [21, 12.5];  kam_lens = 12;  kam_lens_ust = 9.5;

// ---------------- Rulmanlar / bağlantı ------------------------------
r6808 = [40, 52, 7];      // pan: 6808-2RS (iç 40, dış 52, 7 mm)
r608  = [8, 22, 7];       // tilt karşı taraf: 608ZZ + M8x25 cıvata

// ---------------- Ana ölçüler ---------------------------------------
taban_D = 112; taban_H = 80; kapak_t = 9;
horn_ust = kapak_t + 0.8;               // horn üst yüzü (kapak altından)
tabla_D = 66; tabla_t = 5;
eksen_z = 50;                           // tilt ekseni, boyun tabanından
kol_t = 5; kol_W = 26;

kafa_W = 64; kafa_H = 82; kafa_D = 54; kafa_r = 14;
kz0 = -26;  kz1 = kz0 + kafa_H;         // kafa alt/üst (eksene göre)
ky1 = 17;   ky0 = ky1 - kafa_D;         // kafa arka/ön (ön = -y)
ekran_z = 8;                            // ekran merkezi (eksene göre)
kam_z   = 42.5;                         // kamera lens merkezi (kart ekranın üstünde kalır)

// Kafa içindeki tilt servosu: mil +x, horn yüzü kafanın sağ dış yüzünün 0.5 mm dışında
horn_yuz_x = kafa_W/2 + 0.5;
sv_x0 = horn_yuz_x - servo_mil_cikinti - servo_H;   // gövdenin -x yüzü
sv_z0 = -servo_mil_uctan;                           // mil gövdenin alt ucuna yakın

// =====================================================================
module kafa_profil(ice, y, h) {
    hull() for (x = [-(kafa_W/2 - kafa_r), kafa_W/2 - kafa_r], z = [kz0 + kafa_r, kz1 - kafa_r])
        translate([x, y, z]) rotate([-90, 0, 0]) cylinder(r = kafa_r - ice, h = h);
}
module m_delik(d, h) cylinder(d = d, h = h);

// =====================================================================
//  1) TABAN — Pi 5 yatay altta, servo sürücü kart sol duvarda
// =====================================================================
kule_acilar = [30, 150, 270];
module taban() {
    difference() {
        hull() { cylinder(d = taban_D - 4, h = 1); translate([0, 0, 2]) cylinder(d = taban_D, h = taban_H - 2); }
        translate([0, 0, duvar]) cylinder(d = taban_D - 2*duvar, h = taban_H);
        // Pi USB + Ethernet (+x)
        translate([38, -27, duvar + 4]) cube([30, 54, 22]);
        // Pi USB-C güç (-y)
        translate([-pi_L/2 + 11.2 - 7, -70, duvar + 4]) cube([14, 30, 11]);
        // 12V servo güç kablosu (arka, +y)
        translate([-25, 40, 66]) rotate([-90, 0, 0]) cylinder(d = 11, h = 20);
        // havalandırma: alt ızgara + arka yarıklar
        for (i = [-3:3]) translate([i*8 - 2, -22, -1]) cube([4, 44, duvar + 2]);
        for (a = [54:12:126]) rotate(a) translate([taban_D/2 - 5, -2, 16]) cube([10, 4, 26]);
        // lastik ayak yuvaları (10 mm)
        for (a = [45:90:315]) rotate(a) translate([42, 0, -0.01]) cylinder(d = 10.5, h = 1.2);
    }
    // Pi ayakları: M2.5 heat-set (3.6 mm delik), 5 mm
    for (x = [-pi_L/2 + pi_kenar, -pi_L/2 + pi_kenar + pi_dx], y = [-pi_dy/2, pi_dy/2])
        translate([x, y, duvar - 0.01]) difference() {
            cylinder(d = 6.5, h = 5); translate([0, 0, 1]) cylinder(d = 3.6, h = 6); }
    // kapak kuleleri: M3 heat-set, Pi'nin üstünde başlar (45° konik destek)
    for (a = kule_acilar) rotate(a) translate([taban_D/2 - 6, 0, 0]) {
        translate([0, 0, 50]) difference() {
            cylinder(d = 9, h = taban_H - 50);
            translate([0, 0, taban_H - 50 - 6]) cylinder(d = 4, h = 7); }
        translate([0, 0, 45]) cylinder(d1 = 0.5, d2 = 9, h = 5);
    }
    // servo sürücü kart bloğu (sol duvar, düz yüz x = -43). Kart cırt kelepçe
    // ile iki tünelden bağlanır ya da çift taraflı bantla yapışır. ÖLÇ: kart boyu
    difference() {
        intersection() {
            cylinder(d = taban_D - 1, h = taban_H);
            hull() { translate([-60, -20, 50]) cube([17, 40, 26]); translate([-60, -20, 40]) cube([6, 40, 1]); }
        }
        for (z = [56, 70]) translate([-48, -25, z]) cube([2.5, 50, 4]);
    }
}

// =====================================================================
//  2) KAPAK — 6808 rulman yuvası + altında pan servo kafesi (mil yukarı)
// =====================================================================
module pan_servo_govde(b = 0) {   // kapak koordinatında, mil (0,0)
    translate([-servo_mil_uctan - b, -servo_W/2 - b, horn_ust - servo_mil_cikinti - servo_H])
        cube([servo_L + 2*b, servo_W + 2*b, servo_H]);
}
module kapak() {
    difference() {
        union() {
            hull() { cylinder(d = taban_D, h = kapak_t - 2); cylinder(d = taban_D - 4, h = kapak_t); }
            translate([-servo_mil_uctan - bosluk - duvar, -servo_W/2 - bosluk - duvar, -28])
                cube([servo_L + 2*(bosluk + duvar), servo_W + 2*(bosluk + duvar), 28]);
        }
        translate([0, 0, kapak_t - r6808[2]]) cylinder(d = r6808[1] + bosluk, h = r6808[2] + 1);  // rulman
        translate([0, 0, -1]) cylinder(d = 46, h = kapak_t + 2);  // iç bilezik + kablo halkası boşluğu
        // servo gövdesi (alttan itilir, üstü kapağa dayanır)
        translate([-servo_mil_uctan - bosluk, -servo_W/2 - bosluk, -40])
            cube([servo_L + 2*bosluk, servo_W + 2*bosluk, 40 + horn_ust - servo_mil_cikinti]);
        // kelepçe yarıkları (servoyu kafese bağlar)
        for (x = [-servo_mil_uctan + 4, servo_L - servo_mil_uctan - 8])
            translate([x, -30, -24]) cube([4, 60, 3.5]);
        // M3 vidalar (havşalı)
        for (a = kule_acilar) rotate(a) translate([taban_D/2 - 6, 0, -1]) {
            cylinder(d = 3.4, h = kapak_t + 2);
            translate([0, 0, kapak_t + 1 - 2]) cylinder(d1 = 3.4, d2 = 6.6, h = 2.01);
        }
    }
}

// =====================================================================
//  3) BOYUN — pan horn'una vidalı tabla + iki kol (sağ: tilt horn, sol: M8 mil)
// =====================================================================
xs_ic = horn_yuz_x;           xs_dis = xs_ic + kol_t;       // sağ kol
xl_ic = -kafa_W/2 - 1.5;      xl_dis = xl_ic - kol_t;       // sol kol (1.5 = M8 pul)
module kol(x0) hull() {
    translate([x0, -kol_W/2, 0]) cube([kol_t, kol_W, 1]);
    translate([x0, 0, eksen_z]) rotate([0, 90, 0]) cylinder(r = kol_W/2, h = kol_t);
}
module boyun() {
    difference() {
        union() {
            hull() { cylinder(d = tabla_D, h = tabla_t); translate([xl_dis, -kol_W/2, 0]) cube([xs_dis - xl_dis, kol_W, tabla_t]); }
            kol(xs_ic); kol(xl_dis);
            translate([0, 0, -0.8]) cylinder(d = 43, h = 0.8);                  // iç bileziğe basan pul
            translate([0, 0, -0.8 - r6808[2]]) cylinder(d = r6808[0] - 0.1, h = r6808[2]);  // rulman borusu
        }
        translate([0, 0, -10]) cylinder(d = 37, h = 10);                         // boru içi: horn + kablolar
        // pan horn vidaları (havşa üstten)
        for (a = [0:90:270]) rotate(a) translate([horn_vida_cember/2, 0, -1]) {
            cylinder(d = horn_vida, h = tabla_t + 2);
            translate([0, 0, 4]) cylinder(d = 4.5, h = tabla_t); }
        translate([0, 0, -1]) cylinder(d = 7, h = tabla_t + 2);
        // kablo penceresi: horn'un çevresinde 120° yay (kamera şerit kablosu 16 mm geçer)
        rotate(120) rotate_extrude(angle = 120) translate([12.5, -1]) square([5.5, tabla_t + 2]);
        // sağ kol: tilt horn vidaları
        translate([xs_ic - 1, 0, eksen_z]) rotate([0, 90, 0]) {
            for (a = [0:90:270]) rotate(a) translate([horn_vida_cember/2, 0, 0]) cylinder(d = horn_vida, h = kol_t + 2);
            cylinder(d = 7, h = kol_t + 2); }
        // sol kol: M8 mil deliği
        translate([xl_dis - 1, 0, eksen_z]) rotate([0, 90, 0]) cylinder(d = 8.4, h = kol_t + 2);
    }
}

// =====================================================================
//  4) KAFA — içinde tilt servosu; önde ekran, üstte kamera; arkası kapaklı
//     Koordinat: orijin = tilt ekseni
// =====================================================================
module kafa_dis() hull() { kafa_profil(2.5, ky0, 0.01); kafa_profil(0, ky0 + 3, kafa_D - 3); }
module kafa() {
    difference() {
        union() {
            difference() { kafa_dis(); kafa_profil(duvar, ky0 + duvar, kafa_D); }
            // servo kafesi (arkası açık, servo arkadan girer)
            translate([sv_x0 - bosluk - duvar, -servo_W/2 - bosluk - duvar, sv_z0 - bosluk - duvar])
                cube([kafa_W/2 - 1 - (sv_x0 - bosluk - duvar), servo_W/2 + bosluk + duvar + ky1 - 3,
                      servo_L + 2*(bosluk + duvar)]);
            // 608 göbeği (sol duvar)
            translate([-kafa_W/2 + 0.5, 0, 0]) rotate([0, 90, 0]) cylinder(d = 30, h = 10);
            // ekran tutucu kulaklar (M2)
            for (s = [-1, 1]) translate([s*(ekran_kart/2 + 3), ky0 + duvar, ekran_z])
                rotate([-90, 0, 0]) difference() { cylinder(d = 6, h = 7.5); cylinder(d = 1.8, h = 8); }
            // kamera ayakları (M2): üst sıra lensin 7.5 mm üstü, alt sıra 5 mm altı
            for (dx = [-kam_delik[0]/2, kam_delik[0]/2], dz = [kam_lens_ust - 2, kam_lens_ust - 2 - kam_delik[1]])
                translate([dx, ky0 + duvar, kam_z + dz]) rotate([-90, 0, 0])
                    difference() { cylinder(d = 5, h = 3); cylinder(d = 1.8, h = 4); }
            // arka kapak kuleleri (M2)
            for (x = [-1, 1], z = [kz0 + 6, kz1 - 8]) translate([x*(kafa_W/2 - 7), ky1 - 10, z])
                rotate([-90, 0, 0]) difference() { cylinder(d = 6, h = 8); cylinder(d = 1.8, h = 9); }
        }
        // servo cebi (arkaya açık)
        translate([sv_x0 - bosluk, -servo_W/2 - bosluk, sv_z0 - bosluk])
            cube([servo_H + 2*bosluk, servo_W/2 + bosluk + ky1 + 5, servo_L + 2*bosluk]);
        // horn deliği (sağ duvar)
        translate([sv_x0 + servo_H - 1, 0, 0]) rotate([0, 90, 0]) cylinder(d = horn_cap + 4, h = 20);
        // 608 yuvası dıştan + cıvata/somun boşluğu
        translate([-kafa_W/2 - 1, 0, 0]) rotate([0, 90, 0]) cylinder(d = r608[1] + bosluk, h = 1 + r608[2]);
        translate([-kafa_W/2 - 1, 0, 0]) rotate([0, 90, 0]) cylinder(d = 12, h = 20);
        // ekran penceresi + kart oturma yuvası (1 mm)
        translate([0, ky0 - 1, ekran_z]) rotate([-90, 0, 0]) cylinder(d = ekran_pencere, h = 10);
        translate([0, ky0 + duvar - 1, ekran_z]) rotate([-90, 0, 0]) cylinder(d = ekran_kart + 0.6, h = 2);
        // kamera lens deliği
        translate([0, ky0 - 1, kam_z]) rotate([-90, 0, 0]) cylinder(d = kam_lens, h = 10);
        // kablo çıkışı: alt yüz, eksene yakın (eğimde az oynar)
        translate([-17, 0, kz0 - 1]) cylinder(d = 12, h = duvar + 2);
    }
}
module kafa_arka() {
    difference() {
        kafa_profil(duvar + 0.3, ky1 - 2, 2);
        for (x = [-1, 1], z = [kz0 + 6, kz1 - 8]) translate([x*(kafa_W/2 - 7), ky1 - 3, z])
            rotate([-90, 0, 0]) { cylinder(d = 2.3, h = 4); translate([0, 0, 2.6]) cylinder(d = 4.2, h = 2); }
        for (i = [-2:2]) translate([i*7 - 1.5, ky1 - 3, 0]) cube([3, 4, 30]);
    }
}

// =====================================================================
module montaj() {
    color("WhiteSmoke") taban();
    translate([0, 0, taban_H]) {
        color("Gainsboro") kapak();
        color("DimGray", 0.5) pan_servo_govde();
        rotate(pan) translate([0, 0, horn_ust]) {
            color("LightSteelBlue") boyun();
            translate([0, 0, eksen_z]) rotate([egim, 0, 0]) {
                color("White") kafa();
                color("Silver") kafa_arka();
                color("DimGray", 0.5) translate([sv_x0, -servo_W/2, sv_z0]) cube([servo_H, servo_W, servo_L]);
                color("#1a1a2e") translate([0, ky0 + 2.4, ekran_z]) rotate([-90, 0, 0]) cylinder(d = ekran_kart, h = 4);
            }
        }
    }
}

if      (PARCA == "montaj")    montaj();
else if (PARCA == "taban")     taban();
else if (PARCA == "kapak")     rotate([180, 0, 0]) kapak();          // üst yüz tablada
else if (PARCA == "boyun")     translate([0, 0, 0.8 + r6808[2]]) boyun();  // boru tablada, tabla altı destekli
else if (PARCA == "kafa")      rotate([90, 0, 0]) kafa();            // ön yüz tablada
else if (PARCA == "kafa_arka") rotate([90, 0, 0]) kafa_arka();
