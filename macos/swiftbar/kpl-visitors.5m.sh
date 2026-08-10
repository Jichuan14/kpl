#!/bin/bash
# <xbar.title>KPL visitors</xbar.title>
# <xbar.desc>Shows today's unique KPL Lab visitors in the macOS menu bar.</xbar.desc>
# <xbar.dependencies>curl,plutil,security</xbar.dependencies>

# SwiftBar runs this file every five minutes (the `.5m.sh` suffix). The API
# credential is intentionally not stored in this script; it stays in Keychain.

set -u

PATH="/usr/bin:/bin:/usr/sbin:/sbin"
API_URL="https://kpllab.xyz/api/analytics/widget"
KEYCHAIN_SERVICE="KPL Lab SwiftBar"
KEYCHAIN_ACCOUNT="analytics-widget-token"

show_error() {
  printf '%s\n' "KPL: — | color=#e5534b"
  printf '%s\n' "---"
  printf '%s\n' "Visitor count unavailable"
  printf '%s\n' "Refresh | refresh=true"
}

token=$(/usr/bin/security find-generic-password \
  -s "$KEYCHAIN_SERVICE" -a "$KEYCHAIN_ACCOUNT" -w 2>/dev/null) || {
  printf '%s\n' "KPL: setup | color=#d29922"
  printf '%s\n' "---"
  printf '%s\n' "Add the widget token to Keychain, then refresh."
  exit 0
}

# `--config -` keeps the Authorization header out of the process command line.
# The endpoint and curl both require HTTPS; curl still verifies the server
# certificate with macOS's trust store.
response=$(printf '%s\n' \
  "header = \"Authorization: Bearer $token\"" \
  'header = "Accept: application/json"' | \
  /usr/bin/curl --config - --fail --silent --show-error \
    --connect-timeout 5 --max-time 10 --proto '=https' --tlsv1.2 "$API_URL" \
    2>/dev/null) || {
  show_error
  exit 0
}

unique_visitors=$(printf '%s' "$response" | /usr/bin/plutil \
  -extract data.today.unique_visitors raw -o - - 2>/dev/null)
page_views=$(printf '%s' "$response" | /usr/bin/plutil \
  -extract data.today.page_views raw -o - - 2>/dev/null)

case "$unique_visitors:$page_views" in
  *[!0-9:]*|:*)
    show_error
    exit 0
    ;;
esac

printf '%s\n' "KPL: $unique_visitors | color=#bc8cff"
printf '%s\n' "---"
printf '%s\n' "Today: $unique_visitors unique visitors"
printf '%s\n' "Today: $page_views page views"
printf '%s\n' "Updated: $(/bin/date '+%H:%M')"
printf '%s\n' "Refresh | refresh=true"
printf '%s\n' "Open management | href=https://kpllab.xyz/management"
