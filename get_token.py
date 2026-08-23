#!/usr/bin/env python3
"""
Haalt je YT_REFRESH_TOKEN op. Draai dit ÉÉN KEER op je eigen computer.

    python get_token.py

Je hebt nodig: een OAuth client van het type "Desktop app" uit de Google
Cloud Console. Zie stap 6 van de handleiding.

Het script print aan het eind drie waarden. Die zet je als GitHub Secrets.
Deel ze met niemand en commit ze nooit.
"""
import json
import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

POORT = 8080
REDIRECT = f"http://localhost:{POORT}"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"

code_holder = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code_holder["code"] = params.get("code", [None])[0]
        code_holder["error"] = params.get("error", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            "<h2>Klaar. Je kunt dit tabblad sluiten en terug naar je "
            "terminal.</h2>".encode("utf-8"))

    def log_message(self, *a):
        pass          # geen serverlogs in je terminal


def main():
    print("\nPlak je gegevens uit de Google Cloud Console.\n")
    client_id = input("Client ID:     ").strip()
    client_secret = input("Client secret: ").strip()

    if not client_id or not client_secret:
        sys.exit("Beide velden zijn verplicht.")

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",        # forceert een refresh_token, ook bij herhaling
    })

    print(f"\nJe browser opent nu. Log in met het Google-account waar je\n"
          f"YouTube-kanaal onder valt.\n\nLukt het openen niet, plak dan zelf:\n{auth_url}\n")
    webbrowser.open(auth_url)

    server = HTTPServer(("localhost", POORT), Handler)
    server.handle_request()          # wacht op precies één callback

    if code_holder.get("error"):
        sys.exit(f"Google gaf een fout terug: {code_holder['error']}")
    code = code_holder.get("code")
    if not code:
        sys.exit("Geen autorisatiecode ontvangen.")

    data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT,
        "grant_type": "authorization_code",
    }).encode()

    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    with urllib.request.urlopen(req) as r:
        tokens = json.loads(r.read().decode())

    refresh = tokens.get("refresh_token")
    if not refresh:
        sys.exit(
            "Geen refresh_token gekregen. Meestal omdat je deze app al eerder\n"
            "had goedgekeurd. Ga naar https://myaccount.google.com/permissions,\n"
            "verwijder de app, en draai dit script opnieuw."
        )

    print("\n" + "=" * 60)
    print("  Zet deze drie waarden als GitHub Secrets:")
    print("=" * 60)
    print(f"\nYT_CLIENT_ID\n{client_id}")
    print(f"\nYT_CLIENT_SECRET\n{client_secret}")
    print(f"\nYT_REFRESH_TOKEN\n{refresh}")
    print("\n" + "=" * 60)
    print("  Deel deze niet en zet ze nooit in je repo.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
