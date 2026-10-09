"""TCP snapshot transport; all socket I/O runs outside Qt's GUI thread."""
import json
import socket
import socketserver
import struct
import threading
import time
import zlib

MAX_FRAME = 8 * 1024 * 1024


def receive_exact(sock, size):
    result = bytearray()
    while len(result) < size:
        chunk = sock.recv(size - len(result))
        if not chunk:
            raise ConnectionError('connection closed')
        result.extend(chunk)
    return bytes(result)


def receive_frame(sock):
    size, = struct.unpack('!I', receive_exact(sock, 4))
    if size > MAX_FRAME:
        raise ValueError('frame too large')
    return receive_exact(sock, size)


def send_frame(sock, data):
    if len(data) > MAX_FRAME:
        raise ValueError('frame too large')
    sock.sendall(struct.pack('!I', len(data)) + data)


def endpoint(value):
    host, port = value.rsplit(':', 1)
    port = int(port)
    if not host or not 1 <= port <= 65535:
        raise ValueError('expected HOST:PORT, port 1–65535')
    return host, port


class SyncServer:
    def __init__(self, address, token):
        self.lock = threading.Lock()
        self.snapshot = None
        self.closed = threading.Event()
        self.slots = threading.BoundedSemaphore(16)
        owner = self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                if not owner.slots.acquire(blocking=False):
                    return
                try:
                    self.request.settimeout(5)
                    self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    if receive_frame(self.request).decode('utf-8') != token:
                        send_frame(self.request, b'ERROR: connection token does not match')
                        return
                    send_frame(self.request, b'SOURCE_SCROLL_2')
                    while not owner.closed.is_set() and self.request.recv(1) == b'?':
                        with owner.lock:
                            snapshot = owner.snapshot
                        packet = {'version': 2, 'server_time': time.monotonic(), 'state': snapshot}
                        send_frame(self.request, zlib.compress(json.dumps(packet, separators=(',', ':')).encode(), 1))
                except (OSError, ValueError, UnicodeError):
                    pass
                finally:
                    owner.slots.release()
        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True
        self.server = Server(address, Handler)
        print(f'Source Scroll leader listening on {address[0]}:{address[1]} (TCP, protocol 2)', flush=True)
        threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .1}, daemon=True).start()

    def publish(self, state):
        with self.lock:
            self.snapshot = state

    def close(self):
        self.closed.set()
        # Avoid a blocking shutdown in Qt's event handler.
        threading.Thread(target=self._close, daemon=True).start()

    def _close(self):
        self.server.shutdown()
        self.server.server_close()


class SyncClient:
    def __init__(self, address, token):
        self.address, self.token = address, token
        self.lock = threading.Lock()
        self.latest = None
        self.status = 'Connecting to leader…'
        self.stop = threading.Event()
        self.socket = None
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while not self.stop.is_set():
            try:
                with socket.create_connection(self.address, timeout=3) as sock:
                    self.socket = sock
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    send_frame(sock, self.token.encode())
                    hello = receive_frame(sock)
                    if hello != b'SOURCE_SCROLL_2':
                        raise ValueError(hello.decode('utf-8', errors='replace'))
                    while not self.stop.is_set():
                        begin = time.monotonic()
                        sock.sendall(b'?')
                        encoded = receive_frame(sock)
                        received = time.monotonic()
                        decoder = zlib.decompressobj()
                        raw = decoder.decompress(encoded, MAX_FRAME + 1)
                        if len(raw) > MAX_FRAME or not decoder.eof:
                            raise ValueError('invalid or oversized snapshot')
                        packet = json.loads(raw)
                        if packet.get('version') != 2:
                            raise ValueError('unsupported protocol')
                        state = packet['state']
                        if state is not None:
                            # Monotonic clocks need not match. Estimate snapshot age
                            # using server-local elapsed time plus half the round trip.
                            age = max(0, packet['server_time'] - state['time']) + (received - begin) / 2
                            with self.lock:
                                self.latest = (state, received, age)
                                self.status = 'Following leader'
                        self.stop.wait(max(0, .05 - (time.monotonic() - begin)))
            except (OSError, ValueError, KeyError, TypeError, zlib.error) as exc:
                with self.lock:
                    self.status = f'{self.address[0]}:{self.address[1]} — {exc}; retrying…'
                print(self.status, flush=True)
                self.stop.wait(1)
            finally:
                self.socket = None

    def get(self):
        with self.lock:
            return self.latest, self.status

    def close(self):
        self.stop.set()
        sock = self.socket
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
