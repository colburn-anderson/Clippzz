#!/usr/bin/env bash
# Sets up the USB SSD on the Pi:
#   partition 1 (250 GB by default)  ->  /data           Clippzz, plus Docker's storage
#   partition 2 (the rest)           ->  /srv/storage    shared to the Mac as "SSD"
#
# It ERASES the SSD you name, moves /data, /var/lib/docker and /var/lib/containerd
# onto it (the old copies stay on the SD card as *.sd-old until you remove them),
# and shares partition 2 over the network for Finder and Time Machine.
#
#   sudo bash ~/clippzz/scripts/pi-ssd.sh /dev/sda [clippzz size, default 250GiB]
#   sudo bash ~/clippzz/scripts/pi-ssd.sh --cleanup     (later: delete the old SD copies)
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run it with sudo:  sudo bash $0 /dev/sda" >&2
  exit 1
fi

USER_NAME="${SUDO_USER:-tallergiraffe}"
OLD_DIRS=(/data.sd-old /var/lib/docker.sd-old /var/lib/containerd.sd-old)

if [ "${1:-}" = "--cleanup" ]; then
  for m in /data /var/lib/docker /var/lib/containerd; do
    if ! findmnt -n "$m" >/dev/null; then
      echo "Stop: $m is not on the SSD right now, so the old copies are still needed." >&2
      exit 1
    fi
  done
  for d in "${OLD_DIRS[@]}"; do
    [ -e "$d" ] && { echo "Removing $d"; rm -rf --one-file-system "$d"; }
  done
  df -h / | tail -1
  exit 0
fi

DEV="${1:?Give the SSD device, for example: sudo bash $0 /dev/sda}"
SIZE="${2:-250GiB}"

# ---------- checks: only ever touch a USB disk that is not the system disk ----------
[ -b "$DEV" ] || { echo "No such disk: $DEV" >&2; exit 1; }
[ "$(lsblk -dno TYPE "$DEV")" = "disk" ] || { echo "$DEV is not a whole disk" >&2; exit 1; }
[ "$(lsblk -dno TRAN "$DEV")" = "usb" ] || { echo "$DEV is not a USB disk; refusing" >&2; exit 1; }
ROOT_DISK="/dev/$(lsblk -no PKNAME "$(findmnt -no SOURCE /)")"
[ "$DEV" != "$ROOT_DISK" ] || { echo "$DEV holds the running system; refusing" >&2; exit 1; }
if lsblk -no MOUNTPOINTS "$DEV" | grep -q .; then
  echo "Something on $DEV is mounted. Unmount it first." >&2
  exit 1
fi
if grep -q "clippzz-ssd" /etc/fstab; then
  echo "The SSD is already set up (see /etc/fstab)." >&2
  exit 1
fi
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^clippzz-clip$'; then
  echo "A test clip is still being made (container clippzz-clip). Wait for it to finish." >&2
  exit 1
fi
id "$USER_NAME" >/dev/null

echo
lsblk -o NAME,SIZE,VENDOR,MODEL,FSTYPE,LABEL "$DEV"
echo
echo "This ERASES everything on $DEV ($(lsblk -dno VENDOR,MODEL,SIZE "$DEV" | xargs))."
echo "Partition 1: $SIZE for Clippzz at /data. Partition 2: the rest, shared to your Mac."
read -r -p "Type ERASE to continue: " answer
[ "$answer" = "ERASE" ] || { echo "Stopped. Nothing was changed."; exit 1; }

P1="${DEV}1"; P2="${DEV}2"
case "$DEV" in *[0-9]) P1="${DEV}p1"; P2="${DEV}p2";; esac

# ---------- undo if anything fails after the old folders are moved ----------
MOVED=0
undo() {
  echo
  echo "Something failed (line $1). Putting things back the way they were..." >&2
  if [ "$MOVED" = 1 ]; then
    for m in /var/lib/containerd /var/lib/docker /srv/storage /data; do umount "$m" 2>/dev/null || true; done
    [ -f /etc/fstab.before-ssd ] && cp /etc/fstab.before-ssd /etc/fstab
    rm -f /etc/systemd/system/docker.service.d/clippzz-ssd.conf /etc/systemd/system/containerd.service.d/clippzz-ssd.conf
    for pair in /data:/data.sd-old /var/lib/docker:/var/lib/docker.sd-old /var/lib/containerd:/var/lib/containerd.sd-old; do
      live="${pair%%:*}"; old="${pair##*:}"
      [ -d "$old" ] || continue
      rmdir "$live" 2>/dev/null || true
      if [ -e "$live" ]; then
        echo "Could not move $old back to $live automatically; do that by hand." >&2
      else
        mv "$old" "$live"
      fi
    done
    systemctl daemon-reload
  fi
  umount /mnt/clippzz-new 2>/dev/null || true
  systemctl start containerd.service docker.service || true
  echo "Restored. Nothing on the SD card was lost. Send the error above to Claude." >&2
}
trap 'undo $LINENO' ERR

# ---------- 1. partition and format ----------
echo "Partitioning $DEV..."
wipefs -a "$DEV" >/dev/null
sfdisk --quiet "$DEV" <<EOF
label: gpt
name=clippzz, size=$SIZE, type=L
name=storage, type=L
EOF
udevadm settle
mkfs.ext4 -q -F -L clippzz -m 1 "$P1"
mkfs.ext4 -q -F -L storage -m 0 "$P2"
UUID1="$(blkid -s UUID -o value "$P1")"
UUID2="$(blkid -s UUID -o value "$P2")"

# ---------- 2. copy Clippzz data and Docker's storage onto partition 1 ----------
echo "Stopping Docker (Clippzz pauses for a few minutes)..."
systemctl stop docker.socket docker.service containerd.service
if findmnt -rno TARGET | grep -qE '^/var/lib/(docker|containerd)(/|$)'; then
  echo "Docker still has storage mounted; aborting before anything moves." >&2
  false
fi
mkdir -p /mnt/clippzz-new
mount "$P1" /mnt/clippzz-new
echo "Copying /data ($(du -sh /data | cut -f1))..."
rsync -aHAX --numeric-ids /data/ /mnt/clippzz-new/
mkdir -p /mnt/clippzz-new/system/docker /mnt/clippzz-new/system/containerd
echo "Copying Docker's storage ($(du -sh /var/lib/docker /var/lib/containerd | awk '{print $1}' | xargs))..."
rsync -aHAX --numeric-ids /var/lib/docker/ /mnt/clippzz-new/system/docker/
rsync -aHAX --numeric-ids /var/lib/containerd/ /mnt/clippzz-new/system/containerd/
umount /mnt/clippzz-new

# ---------- 3. switch over: old copies stay on the SD card as *.sd-old ----------
cp /etc/fstab /etc/fstab.before-ssd
MOVED=1
mv /data /data.sd-old && mkdir /data
mv /var/lib/docker /var/lib/docker.sd-old && mkdir -m 710 /var/lib/docker
mv /var/lib/containerd /var/lib/containerd.sd-old && mkdir -m 711 /var/lib/containerd
mkdir -p /srv/storage
cat >> /etc/fstab <<EOF
# clippzz-ssd: added by clippzz/scripts/pi-ssd.sh. nofail keeps the Pi booting without the SSD.
UUID=$UUID1  /data  ext4  defaults,noatime,nofail,x-systemd.device-timeout=15s  0  2
UUID=$UUID2  /srv/storage  ext4  defaults,noatime,nofail,x-systemd.device-timeout=15s  0  2
/data/system/docker  /var/lib/docker  none  bind,nofail,x-systemd.requires-mounts-for=/data  0  0
/data/system/containerd  /var/lib/containerd  none  bind,nofail,x-systemd.requires-mounts-for=/data  0  0
EOF
# Docker must never start on the SD card's empty folders if the SSD is missing.
mkdir -p /etc/systemd/system/docker.service.d /etc/systemd/system/containerd.service.d
printf '[Unit]\nRequiresMountsFor=/var/lib/docker /var/lib/containerd\n' > /etc/systemd/system/docker.service.d/clippzz-ssd.conf
printf '[Unit]\nRequiresMountsFor=/var/lib/containerd\n' > /etc/systemd/system/containerd.service.d/clippzz-ssd.conf
systemctl daemon-reload
mount /data
mount /srv/storage
mount /var/lib/docker
mount /var/lib/containerd
for m in /data /srv/storage /var/lib/docker /var/lib/containerd; do
  findmnt -n "$m" >/dev/null || { echo "$m did not mount" >&2; false; }
done
chown "$USER_NAME:$USER_NAME" /srv/storage
systemctl start containerd.service docker.service
sleep 5
MOVED=0
trap - ERR
echo "Docker is back on the SSD:"
docker ps --format '  {{.Names}}  {{.Status}}'

# Weekly TRIM keeps the SSD fast.
systemctl enable --now fstrim.timer >/dev/null 2>&1 || true

# ---------- 4. share partition 2 with the Mac (Finder and Time Machine) ----------
setup_share() {
echo "Installing the network share (Samba)..."
apt-get update -qq || true
if apt-get install -y -qq samba >/dev/null; then
  [ -f /etc/samba/smb.conf.before-ssd ] || cp /etc/samba/smb.conf /etc/samba/smb.conf.before-ssd
  cat > /etc/samba/smb.conf <<EOF
# Written by clippzz/scripts/pi-ssd.sh. The original is smb.conf.before-ssd.
[global]
   workgroup = WORKGROUP
   server string = %h
   server role = standalone server
   security = user
   map to guest = never
   server min protocol = SMB3
   load printers = no
   printing = bsd
   printcap name = /dev/null
   disable spoolss = yes
   multicast dns register = no
   logging = file
   max log size = 1000
   # macOS: Finder metadata, Time Machine and fast folder listings
   vfs objects = catia fruit streams_xattr
   fruit:metadata = stream
   fruit:model = MacSamba
   fruit:posix_rename = yes
   fruit:veto_appledouble = no
   fruit:nfs_aces = no
   fruit:wipe_intentionally_left_blank_rfork = yes
   fruit:delete_empty_adfiles = yes

[SSD]
   path = /srv/storage
   valid users = $USER_NAME
   force user = $USER_NAME
   read only = no
   create mask = 0644
   directory mask = 0755
   # Time Machine may use up to 1 TB, leaving the rest for your files.
   fruit:time machine = yes
   fruit:time machine max size = 1T
EOF
  # Show the share in Finder's sidebar and as a Time Machine disk.
  if [ -d /etc/avahi/services ]; then
    cat > /etc/avahi/services/clippzz-ssd.service <<'EOF'
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">%h</name>
  <service><type>_smb._tcp</type><port>445</port></service>
  <service><type>_device-info._tcp</type><port>0</port><txt-record>model=TimeCapsule8,119</txt-record></service>
  <service><type>_adisk._tcp</type><port>0</port>
    <txt-record>sys=waMa=0,adVF=0x100</txt-record>
    <txt-record>dk0=adVN=SSD,adVF=0x82</txt-record>
  </service>
</service-group>
EOF
  fi
  systemctl disable --now nmbd >/dev/null 2>&1 || true
  systemctl enable smbd >/dev/null 2>&1
  systemctl restart smbd
  systemctl reload avahi-daemon 2>/dev/null || true
  echo
  echo "Choose the password your Mac will use for the share (it can differ from your Pi password)."
  if ! smbpasswd -a "$USER_NAME"; then
    echo "No password set. Run later:  sudo smbpasswd -a $USER_NAME"
  fi
else
  echo "Samba did not install (no internet?)." >&2
  return 1
fi
systemctl is-active --quiet smbd
}
# The storage move is finished by now, so a problem here only affects the share.
setup_share || echo "The share is not working yet (see above). The SSD storage itself is set up." >&2

echo
echo "Done."
df -h /data /srv/storage | sed 's/^/  /'
echo
echo "On your Mac: Finder > Go > Connect to Server >  smb://$(hostname).local/SSD"
echo "  (away from home over Tailscale:  smb://$(tailscale ip -4 2>/dev/null | head -1)/SSD)"
echo "  Sign in as $USER_NAME with the share password you just chose."
echo "For backups: System Settings > General > Time Machine > Add Backup Disk > SSD."
echo
echo "The old copies are still on the SD card. Once Clippzz looks fine, free that space with:"
echo "  sudo bash $0 --cleanup"
