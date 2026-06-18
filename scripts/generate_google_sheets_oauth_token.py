import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google_auth_oauthlib.flow import Flow


SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive.file',
]


def parse_args():
    parser = argparse.ArgumentParser(description='Generate a Google Sheets OAuth token.')
    parser.add_argument('--client', required=True, help='OAuth desktop client JSON path')
    parser.add_argument('--token', required=True, help='Output authorized user token JSON path')
    parser.add_argument('--url-file', required=True, help='Where to write the consent URL')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open-browser', action='store_true')
    return parser.parse_args()


def main():
    args = parse_args()
    redirect_uri = f'http://localhost:{args.port}/'
    flow = Flow.from_client_secrets_file(args.client, scopes=SCOPES, redirect_uri=redirect_uri)
    auth_url, _ = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent',
    )

    result = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            if 'error' in params:
                result['error'] = params['error'][0]
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Authorization failed. You can close this tab.')
                return

            code = params.get('code', [None])[0]
            if not code:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'Missing OAuth code. You can close this tab.')
                return

            try:
                flow.fetch_token(code=code)
                token_path = Path(args.token)
                token_path.parent.mkdir(parents=True, exist_ok=True)
                token_path.write_text(flow.credentials.to_json(), encoding='utf-8')
                result['ok'] = True
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'Google Sheets authorization complete. You can close this tab.')
            except Exception as exc:
                result['error'] = str(exc)
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'Token exchange failed. Check the script output.')

        def log_message(self, *_args):
            return

    server = HTTPServer(('localhost', args.port), CallbackHandler)
    Path(args.url_file).write_text(auth_url, encoding='utf-8')
    if args.open_browser:
        threading.Thread(target=webbrowser.open, args=(auth_url,), daemon=True).start()

    while not result:
        server.handle_request()

    if result.get('error'):
        raise RuntimeError(result['error'])

    print(json.dumps({'status': 'ok', 'token': args.token}))


if __name__ == '__main__':
    main()
