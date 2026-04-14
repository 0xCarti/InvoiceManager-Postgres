# Tizen URL Launcher Signage

This project now includes a Tizen signage activation flow for Samsung displays that are managed through `URL Launcher`.

## What Changed

- A display can now issue a short-lived activation code from the Displays page.
- The server exposes a hosted Tizen launcher at `/signage/tizen/launcher`.
- The Tizen launcher activates a screen and then redirects it into the normal `/player/<token>` signage player flow.
- A helper script can generate a Tizen web-app project scaffold that points at this server.

## Backend Flow

1. Create a `Display` in Invoice Manager.
2. Install the Samsung/Tizen player package on the TV using `URL Launcher`.
3. Open the Displays page and generate an activation code for that display.
4. Enter the activation code on the TV.
5. The TV stores the display token locally and then loads the standard player URL.

## Generate The Tizen Project

Run:

```bash
py -3.11 scripts/prepare_tizen_signage_player.py --server-url https://your-public-host
```

Default output:

```text
artifacts/tizen_signage_player/
```

Generated files:

- `config.xml`
- `index.html`
- `icon.png`
- `README.txt`

## Build And Install

1. Open the generated project in Tizen Studio.
2. Create/select a Samsung certificate profile that can sign apps for your target display.
3. Build the project to produce a signed `.wgt`.
4. Host the signed `.wgt` file on a URL the TV can reach.
5. Paste that hosted `.wgt` URL into the Samsung `URL Launcher` install field.
6. Launch the installed app on the TV and activate it with a display code from Invoice Manager.

## Operational Notes

- The generated Tizen project is only a thin wrapper around the hosted launcher URL.
- Menu and playlist updates continue to come from the Flask app, so you do not need to rebuild the Tizen package when menu content changes.
- The app host must be reachable from the TV. Do not use `localhost` or `127.0.0.1`.
- HTTPS is strongly recommended for production deployments.

## Known Limitations

- This repo does not generate Samsung signing certificates for you.
- Different Samsung signage models can vary in how `URL Launcher` expects packaged app URLs. The generated project assumes the panel can install a hosted signed `.wgt`.
