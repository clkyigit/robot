// =====================================================================
//  Kompakt masaüstü robot gövdesi  —  v0.1 (parametrik, OpenSCAD)
//
//  Parçalar: 1) taban  2) taban_kapak  3) pan_tabla  4) tilt_braket
//            5) kafa   6) kafa_arka
//  Kullanım: aşağıda PARCA = "montaj" ile bütün robotu görürsün.
//            Basmak için PARCA'yı tek bir parçanın adına çevir, F6 (render),
//            sonra File > Export > STL.
//
//  "ÖLÇ" yazan değerler tahmini: parçalar gelince kumpasla ölçüp düzelt.
//  Ölçü birimi: mm.  Önerilen baskı: PETG (taban), PLA (diğerleri),
//  0.2 mm katman, 3 duvar, %20 doluluk.
// =====================================================================

PARCA = "montaj";   // "montaj", "taban", "taban_kapak", "pan_tabla",
                    // "tilt_braket", "kafa", "kafa_arka"

$fn = 64;
bosluk = 0.3;       // parçalar arası tolerans

// ---------------- Servo: Waveshare ST3215 ---------------------------
// Gövde: 45.22 x 24.72 x 35 mm (üretici). Mil çıkışı 35 mm'lik yüzde.
servo_L = 45.2;          // uzun kenar
servo_W = 24.7;          // ince kenar
servo_H = 35.0;          // mil yönündeki kalınlık
servo_mil_uctan = 11;    // ÖLÇ: milin merkezinden en yakın kısa kenara mesafe
servo_mil_cikinti = 6;   // ÖLÇ: horn yüzeyinin gövdeden yüksekliği (horn takılıyken)
horn_cap = 20;           // ÖLÇ: alüminyum horn diski çapı
horn_vida_cember = 14;   // ÖLÇ: horn üzerindeki vida deliklerinin bulunduğu çember çapı
horn_vida = 2.2;         // ÖLÇ: horn vida deliği (M2 ise 2.2, M3 ise 3.2)

// ---------------- Raspberry Pi 5 ------------------------------------
pi_L = 85; pi_W = 56;
pi_delik_x = 58; pi_delik_y = 49;      // M2.5 delik aralığı
pi_delik_kenar = 3.5;                  // deliklerin kart kenarına mesafesi
pi_yigin_H = 38;                       // Pi + M.2 HAT+ + fan toplam yükseklik (ÖLÇ)

// ---------------- Ekran: 1.28" GC9A01 -------------------------------
ekran_gorunur = 33;      // ön pencere çapı (aktif alan 32.4 mm)
ekran_kart_cap = 39;     // ÖLÇ: yuvarlak kartın çapı
ekran_kalinlik = 4.5;    // ÖLÇ: kart + cam kalınlığı

// ---------------- Kamera: Camera Module 3 ---------------------------
kam_kart = [25, 24];
kam_delik = [21, 12.5];  // M2 delik aralığı (resmi çizim)
kam_lens = 12;           // ön delik (kare lens gövdesinin köşegeni için pay)
kam_lens_ust = 9.5;      // lens merkezinin kartın üst kenarına mesafesi

// ---------------- Rulmanlar -----------------------------------------
pan_rulman = true;       // 6806-2RS (30x42x7) kullanılsın mı
r6806 = [30, 42, 7];
r608  = [8, 22, 7];

// ---------------- Genel ölçüler -------------------------------------
duvar = 2.4;
taban_L = 104; taban_W = 94; taban_H = 74;   // W: Pi'nin yanına dik servo sürücü kart sığsın
kose_r = 8;
kapak_t = 9;             // 6806 rulman (7 mm) + 2 mm taban

kafa_W = 64; kafa_H = 76; kafa_D = 36; kafa_r = 14;

// =====================================================================
//  Yardımcılar
// =====================================================================
module yuvarlak_kutu(L, W, H, r) {
    hull() for (x = [-L/2 + r, L/2 - r], y = [-W/2 + r, W/2 - r])
        translate([x, y, 0]) cylinder(r = r, h = H);
}
module insert_delik(h = 6, d = 4.0) {   // M3 heat-set insert yuvası
    cylinder(d = d, h = h);
}
module vida_delik(d = 3.3, h = 20) { cylinder(d = d, h = h); }

// =====================================================================
//  1) TABAN — Pi 5, servo sürücü kart, kablolar
// =====================================================================
module taban() {
    difference() {
        yuvarlak_kutu(taban_L, taban_W, taban_H, kose_r);
        // iç boşluk
        translate([0, 0, duvar])
            yuvarlak_kutu(taban_L - 2*duvar, taban_W - 2*duvar, taban_H, kose_r - duvar);
        // Pi USB/Ethernet tarafı (+x duvar)
        translate([taban_L/2 - duvar - 1, -26, duvar + 6]) cube([duvar + 2, 52, 20]);
        // Pi USB-C güç (-y duvar)
        translate([-pi_L/2 + 4 + 11.2 - 7, -taban_W/2 - 1, duvar + 6]) cube([14, duvar + 2, 9]);
        // 12V servo güç girişi (arka, -x duvar)
        translate([-taban_L/2 - 1, 18, 30]) rotate([0, 90, 0]) cylinder(d = 8.5, h = duvar + 2);
        // havalandırma: alt ızgara
        for (i = [-4:4]) translate([i*9 - 2, -20, -1]) cube([4, 40, duvar + 2]);
        // havalandırma: arka (-x) duvar yarıkları
        for (i = [-3:3]) translate([-taban_L/2 - 1, i*9 - 2 - 12, 14]) cube([duvar + 2, 4, 40]);
    }
    // Pi ayakları (M2.5 insert, 6 mm yükseklik — kartın altından hava geçsin)
    for (x = [0, pi_delik_x], y = [0, pi_delik_y])
        translate([-pi_L/2 + pi_delik_kenar + x + 4, -pi_W/2 + pi_delik_kenar + y, duvar])
            difference() { cylinder(d = 7, h = 6); translate([0,0,1]) cylinder(d = 3.6, h = 6); }
    // +y iç duvar: servo sürücü kartı dik durur (çift taraflı bant / cırt ile), alt rayı
    translate([-33, taban_W/2 - duvar - 16, duvar]) difference() {
        cube([66, 16, 5]); translate([-1, 10, 2]) cube([68, 2.2, 4]); }
    // kapak vida kuleleri (M3 insert)
    for (x = [-1, 1], y = [-1, 1])
        translate([x*(taban_L/2 - 7), y*(taban_W/2 - 7), duvar])
            difference() { cylinder(d = 9, h = taban_H - duvar);
                           translate([0, 0, taban_H - duvar - 6]) insert_delik(); }
}

// =====================================================================
//  2) TABAN KAPAK — pan servosunu (mil yukarı) ve 6806 rulmanı taşır
// =====================================================================
pan_mil = [0, 0];                       // pan ekseni kapak merkezinde
cep_D = servo_H - 4;                    // servo gövdesinin kapağın altına sarkan kısmı
module servo_cep_dikey(ekstra = 0) {    // mil +z, mil merkezi orijinde
    translate([-servo_mil_uctan - bosluk, -servo_W/2 - bosluk, -servo_H])
        cube([servo_L + 2*bosluk, servo_W + 2*bosluk, servo_H + ekstra]);
}
module taban_kapak() {
    difference() {
        union() {
            yuvarlak_kutu(taban_L, taban_W, kapak_t, kose_r);
            // servo yuvası (aşağı sarkan kafes)
            translate([0, 0, kapak_t]) mirror([0, 0, 1])
                translate([-servo_mil_uctan - bosluk - duvar, -servo_W/2 - bosluk - duvar, 0])
                    cube([servo_L + 2*(bosluk + duvar), servo_W + 2*(bosluk + duvar), cep_D]);
        }
        // servo gövdesi
        translate([0, 0, kapak_t - servo_mil_cikinti + 0.01]) servo_cep_dikey(ekstra = 2);
        // 6806 rulman yuvası ve horn boşluğu
        if (pan_rulman) translate([0, 0, kapak_t - r6806[2]]) cylinder(d = r6806[1] + bosluk, h = r6806[2] + 1);
        translate([0, 0, -1]) cylinder(d = pan_rulman ? 35 : horn_cap + 3, h = kapak_t + 2);  // iç bileziğe sürtmesin
        // köşe vidaları (M3, havşasız)
        for (x = [-1, 1], y = [-1, 1])
            translate([x*(taban_L/2 - 7), y*(taban_W/2 - 7), -1]) vida_delik();
        // servo kablosu ve ekran/kamera kabloları için arka yarık
        translate([-taban_L/2 + 10, -8, -1]) cube([10, 16, kapak_t + 2]);
    }
}

// =====================================================================
//  3) PAN TABLA — pan horn'una vidalanır, tilt servosunu yatık taşır
// =====================================================================
tabla_cap = 72; tabla_t = 5;
tilt_eksen_z = tabla_t + servo_L - servo_mil_uctan;   // tilt mili yüksekliği (tabla üstünden)
c0 = -servo_H/2 - duvar;                              // beşiğin arka (-x) yüzü
module pan_tabla() {
    difference() {
        union() {
            cylinder(d = tabla_cap, h = tabla_t);
            if (pan_rulman) translate([0, 0, -r6806[2]]) difference() {     // rulman iç bileziğine giren boru
                cylinder(d = r6806[0] - 0.1, h = r6806[2]);
                translate([0, 0, -1]) cylinder(d = horn_cap + 1.5, h = r6806[2] + 2); }
            // tilt servo beşiği: servo dik duruyor, mil +x yönünde
            translate([-servo_H/2 - duvar, -servo_W/2 - bosluk - duvar, tabla_t])
                cube([servo_H + duvar, servo_W + 2*(bosluk + duvar), servo_L * 0.55]);
            // karşı taraftaki 608 mil saplaması (beşiğin -x yüzünde, tilt ekseninde)
            // yaka 4 mm (rulmanın iç bileziğine dayanır) + rulman boyu kadar mil
            translate([c0, 0, tilt_eksen_z]) rotate([0, -90, 0]) {
                cylinder(d = 12, h = 4);
                cylinder(d = r608[0] - 0.15, h = 4 + r608[2]);
            }
            // saplamayı tablaya bağlayan ayak
            translate([c0 - 4, -6, tabla_t]) cube([4, 12, tilt_eksen_z - tabla_t - 6]);
        }
        // horn vida delikleri (alttan)
        for (a = [0:90:270]) rotate(a) translate([horn_vida_cember/2, 0, -r6806[2] - 1])
            cylinder(d = horn_vida, h = 40);
        translate([0, 0, -r6806[2] - 1]) cylinder(d = 7, h = r6806[2] + tabla_t + 2);  // horn orta vidası
        // servo gövdesi (dik, mil +x)
        translate([-servo_H/2, -servo_W/2 - bosluk, tabla_t + 0.01])
            cube([servo_H + 1, servo_W + 2*bosluk, servo_L]);
        // kablo deliği
        translate([12, -servo_W/2 - 12, -10]) cylinder(d = 9, h = 30);
        // servoyu beşiğe bağlayan kablo bağı (cırt kelepçe) yarıkları
        for (x = [-servo_H/2 + 3, servo_H/2 - 6])
            translate([x, -servo_W/2 - bosluk - duvar - 1, tabla_t + 14]) cube([3, servo_W + 2*(bosluk + duvar) + 2, 4]);
    }
}

// =====================================================================
//  4) TILT BRAKET — U şeklinde: +x kolu horn'a, -x kolu 608'e, üstü kafaya
// =====================================================================
kol_t = 4;
braket_ust = 18;                          // eksenden braket tablasına yükseklik
xa_ic  = c0 - 4.5;                        // -x kolunun iç yüzü (yakaya 0.5 mm boşluk)
xa_dis = xa_ic - (r608[2] + 3);           // -x kolunun dış yüzü
xb_ic  = servo_H/2 + servo_mil_cikinti;   // +x kolunun iç yüzü (horn yüzeyi)
xb_dis = xb_ic + kol_t;
module tilt_braket() {
    difference() {
        union() {
            translate([xb_ic, -16, -14]) cube([kol_t, 32, braket_ust + 14]);        // horn kolu
            translate([xa_dis, -16, -14]) cube([xa_ic - xa_dis, 32, braket_ust + 14]); // rulman kolu
            translate([xa_dis, -16, braket_ust]) cube([xb_dis - xa_dis, 32, 4]);     // üst köprü
        }
        // horn vida delikleri
        translate([xb_ic - 1, 0, 0]) rotate([0, 90, 0]) {
            for (a = [0:90:270]) rotate(a) translate([horn_vida_cember/2, 0, 0]) cylinder(d = horn_vida, h = kol_t + 2);
            cylinder(d = 7, h = kol_t + 2);
        }
        // 608 yuvası: iç yüzden açık, 7 mm derin; arkasında 3 mm duvar
        translate([xa_ic + 1, 0, 0]) rotate([0, -90, 0]) cylinder(d = r608[1] + bosluk, h = r608[2] + 1.5);
        translate([xa_ic, 0, 0]) rotate([0, -90, 0]) cylinder(d = 10, h = r608[2] + 5);  // mil ucu boşluğu
        // kafa vidaları (M3)
        for (x = [-10, 10]) translate([x, 0, braket_ust - 1]) vida_delik(h = 8);
        // kablo geçişi
        translate([0, 10, braket_ust - 1]) cylinder(d = 8, h = 8);
    }
}

// =====================================================================
//  5) KAFA — önde yuvarlak ekran, üstünde kamera
// =====================================================================
ekran_merkez_z = kafa_H * 0.42;
kam_merkez_z = kafa_H - 15;        // lens merkezi (üst ayaklar kafanın içinde kalsın)
module kafa_kabuk() { // dik duran, ön yüz -y
    rotate([90, 0, 0]) translate([0, kafa_H/2, -kafa_D/2]) yuvarlak_kutu(kafa_W, kafa_H, kafa_D, kafa_r);
}
module kafa() {
    difference() {
        kafa_kabuk();
        // iç boşluk: ön duvar ve alt/üst/yan duvarlar 'duvar' kalınlığında, arkası açık
        rotate([90, 0, 0]) translate([0, kafa_H/2, -kafa_D/2 - 1])
            yuvarlak_kutu(kafa_W - 2*duvar, kafa_H - 2*duvar, kafa_D - duvar + 1, kafa_r - duvar);
        // ekran penceresi
        translate([0, -kafa_D/2 - 1, ekran_merkez_z]) rotate([-90, 0, 0]) cylinder(d = ekran_gorunur, h = duvar + 2);
        // kamera lens deliği
        translate([0, -kafa_D/2 - 1, kam_merkez_z]) rotate([-90, 0, 0]) cylinder(d = kam_lens, h = duvar + 2);
        // braket vidaları (alt yüz, M3 insert — içteki kulelere)
        for (x = [-10, 10]) translate([x, 0, -1]) insert_delik(h = duvar + 7);
        translate([0, 10, -1]) cylinder(d = 8, h = duvar + 2);   // kablo geçişi
    }
    // alt kuleler: braket vidaları için M3 insert
    for (x = [-10, 10]) translate([x, 0, duvar - 0.01]) difference() {
        cylinder(d = 8, h = 7); translate([0, 0, -1]) insert_delik(h = 9); }
    // kamera kartı ayakları (M2). Üst delik sırası lensin 'kam_lens_ust - 2' mm üstünde
    for (dx = [-kam_delik[0]/2, kam_delik[0]/2], dz = [0, -kam_delik[1]])
        translate([dx, -kafa_D/2 + duvar, kam_merkez_z + kam_lens_ust - 2 + dz])
            rotate([-90, 0, 0]) difference() { cylinder(d = 5, h = 3); cylinder(d = 1.8, h = 4); }
    // ekran tutucu: iki küçük kulak (kartı arkadan sıkıştırır, M2 vida)
    for (s = [-1, 1]) translate([s*(ekran_kart_cap/2 + 3), -kafa_D/2 + duvar, ekran_merkez_z])
        rotate([-90, 0, 0]) difference() { cylinder(d = 6, h = ekran_kalinlik + 3); cylinder(d = 1.8, h = 9); }
    // arka kapak vida kuleleri (M2)
    for (x = [-1, 1], z = [12, kafa_H - 12])
        translate([x*(kafa_W/2 - 6), kafa_D/2 - 8, z]) rotate([-90, 0, 0])
            difference() { cylinder(d = 6, h = 6); cylinder(d = 1.8, h = 7); }
}
module kafa_arka() {
    difference() {
        rotate([90, 0, 0]) translate([0, kafa_H/2, -2])
            yuvarlak_kutu(kafa_W - 2*duvar - 0.6, kafa_H - 2*duvar - 0.6, 2, kafa_r - duvar);
        for (x = [-1, 1], z = [12, kafa_H - 12])
            translate([x*(kafa_W/2 - 6), -5, z]) rotate([-90, 0, 0]) cylinder(d = 2.3, h = 10);
        // havalandırma
        for (i = [-2:2]) translate([i*8 - 1.5, -5, kafa_H*0.3]) cube([3, 10, kafa_H*0.4]);
    }
}

// =====================================================================
//  Montaj görünümü
// =====================================================================
module montaj() {
    color("WhiteSmoke") taban();
    translate([0, 0, taban_H]) {
        color("Gainsboro") taban_kapak();
        // pan servosu (temsili)
        color("DimGray", 0.6) translate([0, 0, kapak_t - servo_mil_cikinti]) servo_cep_dikey();
        translate([0, 0, kapak_t]) {
            color("LightSteelBlue") pan_tabla();
            // tilt servosu (temsili)
            color("DimGray", 0.6) translate([-servo_H/2, -servo_W/2, tabla_t]) cube([servo_H, servo_W, servo_L]);
            translate([0, 0, tilt_eksen_z]) {
                color("SkyBlue") tilt_braket();
                translate([0, 0, braket_ust + 4]) { color("White") kafa(); color("Silver") translate([0, kafa_D/2 - 2, 0]) kafa_arka(); }
            }
        }
    }
}

if      (PARCA == "montaj")      montaj();
else if (PARCA == "taban")       taban();
else if (PARCA == "taban_kapak") taban_kapak();
else if (PARCA == "pan_tabla")   pan_tabla();
else if (PARCA == "tilt_braket") tilt_braket();
else if (PARCA == "kafa")        rotate([90, 0, 0]) kafa();        // ön yüz tablada
else if (PARCA == "kafa_arka")   rotate([90, 0, 0]) kafa_arka();
