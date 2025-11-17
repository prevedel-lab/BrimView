from http.server import SimpleHTTPRequestHandler, HTTPServer
import mimetypes

class CORSRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # we don't need to set the CORS policies because we don't use anymore SharedArrayBuffer
        # self.send_header('Access-Control-Allow-Origin', '*')
        # self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        # self.send_header('Cross-Origin-Embedder-Policy', 'credentialless')
        # self.send_header('Cross-Origin-Embedder-Policy', 'require-corp')
        super().end_headers()

if __name__ == '__main__':
    port = 8000
    server_address = ('', port)
    mimetypes.add_type('application/javascript', '.js')
    httpd = HTTPServer(server_address, CORSRequestHandler)
    print(f"Serving on http://localhost:{port}")
    httpd.serve_forever()