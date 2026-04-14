from __future__ import annotations

import argparse
import shutil
import textwrap
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a Samsung/Tizen web-app project that opens the hosted "
            "Invoice Manager signage launcher."
        )
    )
    parser.add_argument(
        "--server-url",
        required=True,
        help="Base public URL for the Invoice Manager app, for example https://menus.example.com",
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts/tizen_signage_player",
        help="Directory to write the generated Tizen project into.",
    )
    parser.add_argument(
        "--app-name",
        default="Invoice Manager Signage",
        help="Display name for the Tizen app.",
    )
    parser.add_argument(
        "--package-id",
        default="IMSIGNAGE1",
        help="Tizen package ID placeholder. Replace if your signing profile requires a different package ID.",
    )
    parser.add_argument(
        "--app-key",
        default="player",
        help="App key suffix used to build the Tizen application ID.",
    )
    parser.add_argument(
        "--version",
        default="1.0.0",
        help="Version to write into config.xml.",
    )
    return parser.parse_args()


def normalize_server_url(raw_url: str) -> str:
    normalized = raw_url.rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SystemExit(
            "--server-url must be a full public URL such as https://menus.example.com"
        )
    return normalized


def render_config_xml(
    *,
    app_name: str,
    package_id: str,
    app_key: str,
    version: str,
    server_url: str,
) -> str:
    origin = urlparse(server_url)
    launcher_url = f"{server_url}/signage/tizen/launcher"
    application_id = f"{package_id}.{app_key}"
    widget_id = f"http://invoicemanager.local/{application_id}"

    return textwrap.dedent(
        f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <widget xmlns="http://www.w3.org/ns/widgets"
            xmlns:tizen="http://tizen.org/ns/widgets"
            id="{escape(widget_id)}"
            version="{escape(version)}"
            viewmodes="maximized">
            <name>{escape(app_name)}</name>
            <icon src="icon.png"/>
            <content src="index.html"/>
            <feature name="http://tizen.org/feature/screen.size.all"/>
            <access origin="{escape(f"{origin.scheme}://{origin.netloc}")}" subdomains="true"/>
            <tizen:application id="{escape(application_id)}" package="{escape(package_id)}" required_version="5.5"/>
            <tizen:content src="{escape(launcher_url)}"/>
            <tizen:setting
                screen-orientation="landscape"
                context-menu="disable"
                background-support="disable"
                encryption="disable"
                install-location="internal-only"
                hwkey-event="disable"/>
        </widget>
        """
    )


def render_index_html(server_url: str) -> str:
    launcher_url = f"{server_url}/signage/tizen/launcher"
    return textwrap.dedent(
        f"""\
        <!doctype html>
        <html lang="en">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Invoice Manager Signage</title>
            <style>
                body {{
                    margin: 0;
                    min-height: 100vh;
                    display: grid;
                    place-items: center;
                    background: #07111c;
                    color: #f7f4ed;
                    font-family: "Segoe UI", sans-serif;
                }}
                .card {{
                    max-width: 36rem;
                    padding: 2rem;
                    border-radius: 1rem;
                    background: rgba(255, 255, 255, 0.08);
                    text-align: center;
                }}
                a {{
                    color: #ffbf47;
                }}
            </style>
        </head>
        <body>
            <div class="card">
                <h1>Invoice Manager Signage Player</h1>
                <p>
                    This Tizen package is configured to load the hosted launcher at
                    <a href="{launcher_url}">{launcher_url}</a>.
                </p>
                <p>
                    If the hosted start page is unavailable, check the server URL and network access.
                </p>
            </div>
        </body>
        </html>
        """
    )


def render_readme(server_url: str, package_id: str, app_key: str) -> str:
    app_id = f"{package_id}.{app_key}"
    return textwrap.dedent(
        f"""\
        Invoice Manager Tizen Signage Player
        ====================================

        This folder is a generated Samsung/Tizen web-app scaffold.

        Hosted launcher URL:
        {server_url}/signage/tizen/launcher

        Tizen application ID:
        {app_id}

        Next steps:
        1. Open this project in Tizen Studio.
        2. Create or select a Samsung certificate profile that can sign apps for your target display.
        3. Build the project to produce a signed .wgt package.
        4. Host the signed .wgt file on a web server the TV can reach.
        5. Paste that hosted .wgt URL into the Samsung URL Launcher install field.
        6. After the app launches on the TV, generate an activation code for a display in Invoice Manager and enter it on the TV.

        Notes:
        - This scaffold points to the hosted Invoice Manager launcher and does not need to be rebuilt for menu/layout changes on the server.
        - If your Samsung model expects a packaging descriptor instead of a direct .wgt URL, adapt the hosted deployment format to your panel's SSSP requirements.
        """
    )


def main() -> None:
    args = parse_args()
    server_url = normalize_server_url(args.server_url)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    config_xml = render_config_xml(
        app_name=args.app_name,
        package_id=args.package_id,
        app_key=args.app_key,
        version=args.version,
        server_url=server_url,
    )
    index_html = render_index_html(server_url)
    readme_text = render_readme(server_url, args.package_id, args.app_key)

    (output_dir / "config.xml").write_text(config_xml, encoding="utf-8")
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")
    (output_dir / "README.txt").write_text(readme_text, encoding="utf-8")

    icon_source = Path(__file__).resolve().parents[1] / "app" / "static" / "live.png"
    if icon_source.exists():
        shutil.copyfile(icon_source, output_dir / "icon.png")

    print(f"Wrote Tizen signage scaffold to: {output_dir}")
    print("Next: open the generated project in Tizen Studio, sign it, and build a .wgt package.")


if __name__ == "__main__":
    main()
