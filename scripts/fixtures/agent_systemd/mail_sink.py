"""Tiny debug SMTP receiver: accepts every message and writes it to <dir>/<n>.eml.

Stdlib only (smtpd is gone in newer Pythons). No auth, no TLS; fixture use only.
"""
import argparse
import os
import socketserver
import threading

_lock = threading.Lock()


def _next_path(directory):
    with _lock:
        n = 1 + max([int(f[:-4]) for f in os.listdir(directory)
                     if f.endswith(".eml") and f[:-4].isdigit()] or [0])
        path = os.path.join(directory, "%d.eml" % n)
        open(path, "xb").close()
        return path


class Handler(socketserver.StreamRequestHandler):
    def reply(self, line):
        self.wfile.write((line + "\r\n").encode("ascii"))
        self.wfile.flush()

    def handle(self):
        self.reply("220 mail-sink ready")
        envelope = {"from": None, "to": []}
        while True:
            raw = self.rfile.readline(65536)
            if not raw:
                return
            cmd = raw.decode("utf-8", "replace").strip()
            verb = cmd.split(" ", 1)[0].upper()
            if verb == "EHLO":
                self.wfile.write(b"250-mail-sink\r\n250-8BITMIME\r\n250 SMTPUTF8\r\n")
            elif verb == "HELO":
                self.reply("250 mail-sink")
            elif verb == "MAIL":
                envelope = {"from": cmd[10:], "to": []}
                self.reply("250 OK")
            elif verb == "RCPT":
                envelope["to"].append(cmd[8:])
                self.reply("250 OK")
            elif verb == "DATA":
                self.reply("354 End data with <CR><LF>.<CR><LF>")
                lines = []
                while True:
                    line = self.rfile.readline(1 << 20)
                    if not line or line in (b".\r\n", b".\n"):
                        break
                    if line.startswith(b".."):
                        line = line[1:]
                    lines.append(line)
                path = _next_path(self.server.directory)
                with open(path, "wb") as f:
                    f.write(b"".join(lines))
                self.reply("250 OK stored")
            elif verb in ("RSET", "NOOP"):
                self.reply("250 OK")
            elif verb == "QUIT":
                self.reply("221 bye")
                return
            else:
                self.reply("502 not implemented")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=2525)
    p.add_argument("--dir", default="/var/mail-sink")
    a = p.parse_args()
    os.makedirs(a.dir, exist_ok=True)
    server = Server((a.host, a.port), Handler)
    server.directory = a.dir
    server.serve_forever()


if __name__ == "__main__":
    main()
