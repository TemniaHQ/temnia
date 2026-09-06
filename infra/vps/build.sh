#!/bin/bash
# Temnia staging VPS build: fresh Ubuntu 26.04 -> hardened host + Dokploy, no public ports but SSH.
#
# Run it detached, never through the SSH session itself: step 3 restarts sshd, and on Ubuntu's
# socket-activated sshd that ends every open session, including the one running the script.
#   ssh temnia-vps 'cat > /root/build.sh' < infra/vps/build.sh
#   ssh temnia-vps 'systemd-run --unit temnia-build --collect bash -c "bash /root/build.sh > /root/build.log 2>&1"'
#   ssh temnia-vps 'tail -5 /root/build.log'     # once a minute at most: SSH is rate-limited from step 6 on
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
step() { echo "STEP $*"; }

step 1 hostname
# cloud-init reapplies the hosting panel's hostname on every boot unless told not to;
# the box owns its name, the panel label is cosmetic.
printf "preserve_hostname: true\n" > /etc/cloud/cloud.cfg.d/99-temnia-hostname.cfg
hostnamectl set-hostname temnia-vps
grep -q "temnia-vps" /etc/hosts || echo "127.0.1.1 temnia-vps" >> /etc/hosts

step 2 packages
apt-get update -q
apt-get -y -q -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" dist-upgrade
echo iptables-persistent iptables-persistent/autosave_v4 boolean false | debconf-set-selections
echo iptables-persistent iptables-persistent/autosave_v6 boolean false | debconf-set-selections
apt-get -y -q remove ufw
apt-get -y -q install iptables-persistent unattended-upgrades curl ca-certificates
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF
apt-get -y -q autoremove

step 3 sshd
cat > /etc/ssh/sshd_config.d/10-temnia.conf <<'CONF'
# Key-only root. Sorted before cloud-init's 50-*.conf, so these values win.
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
X11Forwarding no
MaxAuthTries 3
CONF
sshd -t
sshd -T | grep -E "^(passwordauthentication|permitrootlogin|kbdinteractiveauthentication) "
systemctl restart ssh

step 4 docker-daemon-defaults
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'CONF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "50m", "max-file": "3" }
}
CONF

step 5a docker
# Dokploy's installer pins a Docker version through get.docker.com that the Ubuntu 26.04 channel
# ("resolute") does not carry, so it silently ends with no Docker and fails at swarm init. Docker's
# repository does support 26.04: install its current stable release first and the installer detects it.
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" > /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get -y -q install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker --version

step 5 dokploy
cd /root
curl -sSL https://dokploy.com/install.sh -o dokploy-install.sh
sh dokploy-install.sh 2>&1 | grep -v -E "^\s*$"
docker service ls

step 6 firewall
IFACE=$(ip route show default | awk '{print $5; exit}')
echo "public interface: $IFACE"
# v4 INPUT: SSH rate limit (6 new connections per 30 s per source), nothing else new to the host.
iptables -F INPUT
iptables -A INPUT -i lo -j ACCEPT
iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
iptables -A INPUT -p icmp -j ACCEPT
iptables -A INPUT -p tcp --dport 22 -m conntrack --ctstate NEW -m recent --set --name SSH --rsource
iptables -A INPUT -p tcp --dport 22 -m conntrack --ctstate NEW -m recent --update --seconds 30 --hitcount 6 --name SSH --rsource -j DROP
iptables -A INPUT -p tcp --dport 22 -m conntrack --ctstate NEW -j ACCEPT
iptables -A INPUT -p tcp --syn -j DROP
iptables -A INPUT -p udp -m conntrack --ctstate NEW -j DROP
# v4 DOCKER-USER: nothing new from the internet reaches a published container port.
# Traffic arriving through the Cloudflare tunnel comes from the cloudflared container over the
# overlay network, never from the public interface, so this is the whole origin policy.
iptables -F DOCKER-USER
iptables -A DOCKER-USER -i "$IFACE" -m conntrack --ctstate NEW -j DROP
iptables -A DOCKER-USER -j RETURN
# v6: same host policy; Docker publishes on v6 through its proxy, so INPUT covers it.
ip6tables -F INPUT
ip6tables -A INPUT -i lo -j ACCEPT
ip6tables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
ip6tables -A INPUT -p ipv6-icmp -j ACCEPT
ip6tables -A INPUT -p tcp --dport 22 -m conntrack --ctstate NEW -m recent --set --name SSH6 --rsource
ip6tables -A INPUT -p tcp --dport 22 -m conntrack --ctstate NEW -m recent --update --seconds 30 --hitcount 6 --name SSH6 --rsource -j DROP
ip6tables -A INPUT -p tcp --dport 22 -m conntrack --ctstate NEW -j ACCEPT
ip6tables -A INPUT -p tcp --syn -j DROP
ip6tables -A INPUT -p udp -m conntrack --ctstate NEW -j DROP
netfilter-persistent save

step 7 verify
echo "listening: $(ss -tlnp | awk 'NR>1{print $4}' | sort -u | tr '\n' ' ')"
docker service ls --format '{{.Name}} {{.Replicas}} {{.Image}}'
docker ps --format '{{.Names}} {{.Status}} {{.Ports}}'
printf "panel via loopback: "; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3000/
iptables -S INPUT | wc -l; iptables -S DOCKER-USER; ip6tables -S INPUT | wc -l
echo "STEP done"
