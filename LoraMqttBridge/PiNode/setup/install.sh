#!/usr/bin/env bash
# install.sh — Pi Zero 2W Setup für die LoRa-MQTT-Bridge
# Muss als root laufen: sudo ./install.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Bitte mit sudo ausführen." >&2
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

echo "==> System aktualisieren"
apt-get update
apt-get install -y python3 python3-pip python3-venv \
                   hostapd dnsmasq mosquitto mosquitto-clients \
                   git i2c-tools

echo "==> SPI + I²C in /boot/firmware/config.txt aktivieren"
CONFIG_TXT=/boot/firmware/config.txt
[[ -f $CONFIG_TXT ]] || CONFIG_TXT=/boot/config.txt
grep -q '^dtparam=spi=on'       $CONFIG_TXT || echo 'dtparam=spi=on'       >> $CONFIG_TXT
grep -q '^dtparam=i2c_arm=on'   $CONFIG_TXT || echo 'dtparam=i2c_arm=on'   >> $CONFIG_TXT

echo "==> User 'pi' in Gruppen gpio, spi, i2c"
usermod -aG gpio,spi,i2c pi || true

echo "==> Python venv unter /opt/lora-bridge"
python3 -m venv /opt/lora-bridge
/opt/lora-bridge/bin/pip install --upgrade pip
/opt/lora-bridge/bin/pip install -r "$HERE/../requirements.txt"
# rpi-lgpio als Drop-in
/opt/lora-bridge/bin/pip uninstall -y RPi.GPIO || true
/opt/lora-bridge/bin/pip install rpi-lgpio

echo "==> Code deployen nach /opt/lora-bridge/app"
rm -rf /opt/lora-bridge/app
mkdir -p /opt/lora-bridge/app
cp -r "$REPO/lora_mqtt_bridge" /opt/lora-bridge/app/

echo "==> Config nach /etc/lora-bridge/config.yaml"
mkdir -p /etc/lora-bridge
if [[ ! -f /etc/lora-bridge/config.yaml ]]; then
  cp "$HERE/../config.yaml" /etc/lora-bridge/config.yaml
  echo "    (Bitte anpassen: sudo nano /etc/lora-bridge/config.yaml)"
fi

echo "==> hostapd / dnsmasq / mosquitto Konfig"
install -m 0644 "$HERE/hostapd.conf"    /etc/hostapd/hostapd.conf
sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
install -m 0644 "$HERE/dnsmasq.conf"    /etc/dnsmasq.d/lora-bridge.conf
install -m 0644 "$HERE/mosquitto.conf"  /etc/mosquitto/conf.d/lora-bridge.conf

echo "==> statische IP für wlan0 via dhcpcd"
DHCPCD=/etc/dhcpcd.conf
if ! grep -q '# lora-bridge hotspot' "$DHCPCD" 2>/dev/null; then
  cat >> "$DHCPCD" <<'EOF'

# lora-bridge hotspot
interface wlan0
    static ip_address=192.168.50.1/24
    nohook wpa_supplicant
EOF
fi

echo "==> systemd-Services"
install -m 0644 "$HERE/systemd/lora-bridge.service" /etc/systemd/system/
systemctl daemon-reload
systemctl unmask hostapd
systemctl enable hostapd dnsmasq mosquitto lora-bridge.service
systemctl restart hostapd dnsmasq mosquitto

echo "==> Fertig. Reboot empfohlen: sudo reboot"
echo "    Danach:  sudo systemctl status lora-bridge"
