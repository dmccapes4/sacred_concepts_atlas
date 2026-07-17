#!/usr/bin/env bash
# Deploy sacred.dylanmccapes.systems — nginx vhost + /var/www/sacred/index.html.
#
# Run:  sudo bash deploy/deploy.sh
#
# Does NOT start the FastAPI backend (that's a user systemd unit; see the
# notes printed at the end). Nginx only proxies /api to 127.0.0.1:8877.
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run with sudo:  sudo bash $0"
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WWW=/var/www/sacred
SITE=sacred.dylanmccapes.systems
OWNER=dylanmccapes
OWNER_HOME="$(getent passwd "$OWNER" | cut -d: -f6)"
INDEX="$ROOT/portal/static/index.html"
NGINX_SRC="$ROOT/deploy/nginx-sacred.conf"

[[ -f "$INDEX" ]]     || { echo "Missing $INDEX" >&2; exit 1; }
[[ -f "$NGINX_SRC" ]] || { echo "Missing $NGINX_SRC" >&2; exit 1; }
[[ -f /etc/ssl/cloudflare/origin.pem ]] || {
  echo "Missing Cloudflare Origin CA cert at /etc/ssl/cloudflare/origin.pem" >&2
  exit 1
}

echo "▸ Publishing site → $WWW"
install -d -m 755 -o www-data -g www-data "$WWW"
install -m 644 -o www-data -g www-data "$INDEX" "$WWW/index.html"

echo "▸ Installing nginx vhost $SITE"
install -m 644 "$NGINX_SRC" "/etc/nginx/sites-available/$SITE"
ln -sf "/etc/nginx/sites-available/$SITE" "/etc/nginx/sites-enabled/$SITE"

# Keep a filled-in copy next to the other dylanmccapes.systems secrets, same
# convention as voicenotes / nakatomi.
if [[ -n "$OWNER_HOME" ]]; then
  install -d -m 755 -o "$OWNER" -g "$OWNER" "$OWNER_HOME/.nakatomi-secrets/nginx"
  install -m 644 -o "$OWNER" -g "$OWNER" "$NGINX_SRC" \
    "$OWNER_HOME/.nakatomi-secrets/nginx/$SITE"
fi

echo "▸ nginx -t && reload"
nginx -t
systemctl reload nginx

echo ""
echo "✓ nginx live for https://$SITE/"
echo "  Static:  $WWW/index.html"
echo "  Config:  /etc/nginx/sites-available/$SITE"
echo "  Proxy:   /api/* → 127.0.0.1:8877"
echo ""
echo "Backend (run as $OWNER, not root):"
echo "  cp $ROOT/deploy/sacred-portal.service ~/.config/systemd/user/"
echo "  systemctl --user daemon-reload"
echo "  systemctl --user enable --now sacred-portal"
echo "  # once: sudo loginctl enable-linger $OWNER"
echo ""
echo "DNS: Cloudflare A record 'sacred' → this box, Proxied (orange cloud)."
echo "Verify:  curl -sI https://$SITE/ | head -5"
echo "         curl -s -X POST https://$SITE/api/session | head -c 200; echo"
