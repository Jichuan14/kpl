# KPL visitor menu-bar widget

This SwiftBar plugin shows today's **unique visitors** and page views. It uses
the dedicated read-only `/api/analytics/widget` endpoint, not the management
username/password.

## One-time server setup

On the production server, generate a high-entropy token and put it in the
deployment environment file:

```bash
openssl rand -hex 32
# Add the output to .env.production as:
# ANALYTICS_WIDGET_TOKEN=<generated value>
docker compose -f docker-compose.production.yml up -d --build
```

Keep the value out of source control and do not add it to `backend/.env` on a
shared development machine. The endpoint returns HTTP 503 until this value is
configured, rather than falling back to an unsecured response.

## macOS setup

1. Install [SwiftBar](https://swiftbar.app/) and choose its plugin folder.
2. Store the *same* token in your login Keychain. This command does not echo
   what you paste:

   ```zsh
   read -r -s 'KPL_WIDGET_TOKEN?Paste the widget token: '; echo
   security add-generic-password -U \
     -s 'KPL Lab SwiftBar' \
     -a 'analytics-widget-token' \
     -w "$KPL_WIDGET_TOKEN"
   unset KPL_WIDGET_TOKEN
   ```

3. Copy `kpl-visitors.5m.sh` to SwiftBar's plugin folder, then make it
   executable. For SwiftBar's usual location:

   ```zsh
   cp macos/swiftbar/kpl-visitors.5m.sh "$HOME/Library/Application Support/SwiftBar/Plugins/"
   chmod 700 "$HOME/Library/Application Support/SwiftBar/Plugins/kpl-visitors.5m.sh"
   ```

SwiftBar refreshes it every five minutes. Use the **Refresh** menu item for an
immediate update. The default endpoint is `https://kpllab.xyz`; change
`API_URL` at the top of the plugin only if the production domain changes.

## Security and token rotation

- The token is kept only in the server environment and the macOS Keychain, not
  in the script, Git repository, query string, or SwiftBar output.
- Requests use HTTPS only, certificate verification, and a Bearer header. The
  header is supplied to curl through standard input so it is not part of curl's
  command line.
- The API compares credentials in constant time, returns only today's
  aggregate counts, and marks responses `no-store`.
- To rotate a token, generate a new one, replace `ANALYTICS_WIDGET_TOKEN` on
  the server and redeploy, then repeat the Keychain command on your Mac.

This widget shows daily traffic, not currently active browsers. Live presence
would require a separate browser heartbeat feature.
