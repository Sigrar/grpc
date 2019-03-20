""" Proxies a TCP connection between a single client-server pair.

This proxy is not suitable for production, but should work well for cases in
which a test needs to spy on the bytes put on the wire between a server and
a client.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import datetime
import select
import socket
import threading

_TCP_PROXY_BUFFER_SIZE = 1024
_TCP_PROXY_TIMEOUT = datetime.timedelta(milliseconds=500)


def _init_listen_socket(bind_address):
    listen_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen_socket.bind((bind_address, 0))
    listen_socket.listen(1)
    return listen_socket, listen_socket.getsockname()[1]


def _init_proxy_socket(gateway_address, gateway_port):
    proxy_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    proxy_socket.connect((gateway_address, gateway_port))
    return proxy_socket


# TODO(rbellevi): Docstrings
class TcpProxy(object):
    """Proxies a TCP connection between one client and one server."""

    def __init__(self, bind_address, gateway_address, gateway_port):
        self._bind_address = bind_address
        self._gateway_address = gateway_address
        self._gateway_port = gateway_port

        self._byte_count_lock = threading.RLock()
        self._sent_byte_count = 0
        self._received_byte_count = 0

        self._stop_event = threading.Event()

        self._listen_socket, self._port = _init_listen_socket(
            self._bind_address)
        self._proxy_socket = _init_proxy_socket(self._gateway_address,
                                                self._gateway_port)

        # The following three attributes are owned by the serving thread.
        self._northbound_data = ""
        self._southbound_data = ""
        self._client_sockets = []

        self._thread = threading.Thread(target=self._run_proxy)
        self._thread.start()

    def get_port(self):
        return self._port

    def _handle_reads(self, sockets_to_read):
        for socket_to_read in sockets_to_read:
            if socket_to_read is self._listen_socket:
                client_socket, client_address = socket_to_read.accept()
                self._client_sockets.append(client_socket)
            elif socket_to_read is self._proxy_socket:
                data = socket_to_read.recv(_TCP_PROXY_BUFFER_SIZE)
                with self._byte_count_lock:
                    self._received_byte_count += len(data)
                self._northbound_data += data
            else:
                # Otherwise, read from a connected client.
                data = socket_to_read.recv(_TCP_PROXY_BUFFER_SIZE)
                if data:
                    with self._byte_count_lock:
                        self._sent_byte_count += len(data)
                    self._southbound_data += data
                else:
                    self._client_sockets.remove(socket_to_read)

    def _handle_writes(self, sockets_to_write):
        for socket_to_write in sockets_to_write:
            if socket_to_write is self._proxy_socket:
                if self._southbound_data:
                    self._proxy_socket.sendall(self._southbound_data)
                    self._southbound_data = ""
            else:
                # Otherwise, write to a connected client.
                if self._northbound_data:
                    socket_to_write.sendall(self._northbound_data)
                    self._northbound_data = ""

    def _run_proxy(self):
        while not self._stop_event.is_set():
            expected_reads = (self._listen_socket, self._proxy_socket) + tuple(
                self._client_sockets)
            expected_writes = expected_reads
            sockets_to_read, sockets_to_write, _ = select.select(
                expected_reads, expected_writes, (),
                _TCP_PROXY_TIMEOUT.total_seconds())
            self._handle_reads(sockets_to_read)
            self._handle_writes(sockets_to_write)
        for client_socket in self._client_sockets:
            client_socket.close()

    def stop(self):
        self._stop_event.set()
        self._thread.join()
        self._listen_socket.close()
        self._proxy_socket.close()

    def get_byte_count(self):
        with self._byte_count_lock:
            return self._sent_byte_count, self._received_byte_count,

    def reset_byte_count(self):
        with self._byte_count_lock:
            self._byte_count = 0
            self._received_byte_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()