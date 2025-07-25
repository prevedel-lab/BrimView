from http.server import SimpleHTTPRequestHandler, HTTPServer
import mimetypes

class CORSRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        #self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        # self.send_header('Cross-Origin-Embedder-Policy', 'credentialless')
        self.send_header('Cross-Origin-Embedder-Policy', 'require-corp')
        # The Cross-Origin-Resource-Policy is set by the server
        # self.send_header('Cross-Origin-Resource-Policy', 'cross-origin')
        super().end_headers()

if __name__ == '__main__':
    port = 8000
    server_address = ('', port)
    mimetypes.add_type('application/javascript', '.js')
    httpd = HTTPServer(server_address, CORSRequestHandler)
    print(f"Serving on http://localhost:{port}")
    httpd.serve_forever()