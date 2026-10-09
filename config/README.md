# Site configuration

Copy `site.example.json` to `private-site.json` and replace the example server/client addresses and chat subnet with your deployment values. Every `__NAME__` value is a placeholder, not a connection target. Replace all host/subnet placeholders and use JSON integers for `*_port` values. Loopback placeholders must refer to this machine; retain loopback-only binding for internal APIs and the client bridge. `private-site.json` is local-only and ignored by Git.

Existing service configuration (`gateway.json`, `chat.json`, `secondbrain.json`, and `python-runtime.json`) retains precedence for its own settings. Site settings preserve deployment values formerly embedded in source defaults, including the exact trusted client address for human-approved operations.

Private original files use the `private-` prefix and `.original` suffix. They remain local restore assets, never publishable source. A generated encrypted client package receives the configured server address and numeric service ports; a public template uses `__SERVER_HOST__`, which Setup-Client asks for if it is not supplied.

Deployment-specific compatibility identifiers and installed firewall display names can also be retained in private site settings. Public defaults use ClientPC and ServerPC role names; existing deployment values are retained locally without changing approval contracts or installed firewall rules.

Service ports have no public numeric defaults. Configure all `*_port` entries before starting LocalBrain. Existing service configuration files and private site ports must agree for services that override their listen port.
