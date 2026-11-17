"""Fan kontrolü (robottan bağımsız servis: robot-fan.service).

Pi 5'in fan soketi hasarlı olduğu için Active Cooler GPIO pinlerine bağlı:
  kırmızı +5V -> pin 4, siyah GND -> pin 14, mavi PWM -> pin 32 (GPIO12), sarı devir -> pin 36 (GPIO16)

Sıcaklığa göre hız ayarlar, devri okur ve durumu /dev/shm/robot-fan.json dosyasına yazar
(robotun body.py'si buradan okur). Robotun kendini geliştirme sistemi bu dosyayı değiştiremez.

Güvenlik: sıcaklık okunamazsa ya da bir hata olursa fan tam hızda döner. Servis durursa PWM pini boşta kalır;
fan bu durumda da tam hızda döner.

Ayarlar (ortam değişkeni): FAN_PWM_GPIO=12, FAN_TACH_GPIO=16, FAN_PWM_HZ=1000
"""
import json
import os
import pathlib
import signal
import sys
import time

PWM_GPIO = int(os.environ.get("FAN_PWM_GPIO", "12"))
TACH_GPIO = int(os.environ.get("FAN_TACH_GPIO", "16"))
PWM_HZ = int(os.environ.get("FAN_PWM_HZ", "1000"))
STATUS = pathlib.Path("/dev/shm/robot-fan.json")
INTERVAL = 2.0
PULSES_PER_REV = 2

# (sıcaklık °C, hız %): Pi 5'in kendi fan eğrisine yakın
CURVE = [(45, 0), (50, 30), (60, 50), (67, 75), (75, 100)]
HYSTERESIS = 3.0


def cpu_temp() -> float | None:
    try:
        return int(pathlib.Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000
    except (OSError, ValueError):
        return None


def target_duty(temp: float | None, current: int) -> int:
    if temp is None:
        return 100
    duty = 0
    for t, d in CURVE:
        if temp >= t:
            duty = d
    # yavaşlarken biraz bekle (fan sürekli aç-kapa yapmasın)
    if duty < current:
        cooler = 0
        for t, d in CURVE:
            if temp >= t - HYSTERESIS:
                cooler = d
        duty = max(duty, min(current, cooler))
    return duty


def open_chip():
    import lgpio
    for n in range(0, 16):
        try:
            h = lgpio.gpiochip_open(n)
        except Exception:  # noqa: BLE001
            continue
        try:
            info = lgpio.gpio_get_chip_info(h)
            label = str(info[3] if len(info) > 3 else info)
        except Exception:  # noqa: BLE001
            label = ""
        if "rp1" in label.lower() or "pinctrl-bcm2835" in label.lower():
            return lgpio, h, n
        lgpio.gpiochip_close(h)
    raise RuntimeError("40 pinli GPIO yongası bulunamadı")


def write_status(**kw) -> None:
    try:
        tmp = STATUS.with_suffix(".tmp")
        tmp.write_text(json.dumps({"t": time.time(), **kw}))
        tmp.replace(STATUS)
    except OSError:
        pass


def main() -> None:
    lgpio, h, chip = open_chip()
    lgpio.gpio_claim_output(h, PWM_GPIO, 1)
    tach = None
    try:
        lgpio.gpio_claim_alert(h, TACH_GPIO, lgpio.FALLING_EDGE, lgpio.SET_PULL_UP)
        tach = lgpio.callback(h, TACH_GPIO, lgpio.FALLING_EDGE)   # fonksiyonsuz: sadece sayar (tally)
    except Exception as e:  # noqa: BLE001
        print(f"Devir okunamıyor: {e}", flush=True)

    def stop(*_):
        # çıkarken fanı tam hızda bırak (pin serbest kalınca da tam hız)
        try:
            lgpio.tx_pwm(h, PWM_GPIO, PWM_HZ, 100)
            lgpio.gpiochip_close(h)
        finally:
            sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"Fan servisi başladı (gpiochip{chip}, PWM GPIO{PWM_GPIO} {PWM_HZ} Hz, devir GPIO{TACH_GPIO})", flush=True)

    duty = -1
    last_tally, last_t = 0, time.time()
    while True:
        temp = cpu_temp()
        new = target_duty(temp, max(duty, 0))
        if new != duty:
            if duty <= 0 < new:   # dururken kalkış: bir an tam güç ver
                lgpio.tx_pwm(h, PWM_GPIO, PWM_HZ, 100)
                time.sleep(0.5)
            lgpio.tx_pwm(h, PWM_GPIO, PWM_HZ, new)
            duty = new
        time.sleep(INTERVAL)
        rpm = None
        if tach is not None:
            now, tally = time.time(), tach.tally()
            rpm = int((tally - last_tally) / PULSES_PER_REV / (now - last_t) * 60)
            last_tally, last_t = tally, now
        write_status(temp=temp, duty=duty, rpm=rpm)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"Fan servisi hatası: {e}", flush=True)
        write_status(error=str(e)[:200])
        raise
