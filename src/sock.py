import socket
import time
import threading
import os
from collections.abc import Callable
from typing import Any

class DiscoveryChannel:
	"""
	A class that aids the two machines willing to exchange data discover themselves
	with the use of UDP
	"""
	def __init__(self, broadcast_ip: str | None = None, broadcast_port: int | None = None) -> None:
		"""
		An instance of the `DiscoveryChannel` class

		Args:
			broadcast_ip (str | None): The IPv4 broadcast address used to send
				discovery packets. Defaults to "255.255.255.255" (limited broadcast,
				reaches all hosts on the local subnet).
			broadcast_port (str | None): The UDP port used for broadcast discovery messages.
				Defaults to 5001
		"""

		self.BROADCAST_IP = broadcast_ip or "255.255.255.255"
		self.BROADCAST_PORT = broadcast_port or 5001
		self.BROADCAST_ADDR = (self.BROADCAST_IP, self.BROADCAST_PORT)

		self.DISCOVERY_UNIQ_ID = "DCFTTX"
		self.PORT = 5000
		self._event = threading.Event()
		self._thread = None


	def broadcast(self, pin: str | None = None):
		"""
		The receiving device needs a way to shoutout its IP and PORT
		over the network so whoever requires it can get it.

		It spins up its own thread for this operation. Remeber to call
		stop() method to clean the thread after broadcast is done
		"""
		self._thread = threading.Thread(target=self._broadcast, args=(pin,), daemon=True)

		self._thread.start()

	def listen(self, pin: str | None = None):
		"""
		The sending device needs a way to get the IP and PORT of the device it
		needs to send data to over the network. The receiving device needs to
		be broadcasting its signature for connection to be made.

		Returns:
			ADDR (tuple[str, int] | None): The IP and Port of the broadcasting device
				or None if it didn't find any.
		"""
		_pin = None
		with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
			sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
			sock.bind(("", self.BROADCAST_PORT))

			deadline = time.monotonic() + 20.0

			while True:
				remaining = deadline - time.monotonic()

				if remaining <= 0:
					return None

				sock.settimeout(remaining)

				try:
					data, addr = sock.recvfrom(1024)
				except socket.timeout:
					continue

				try:
					data = data.decode("utf-8")

					if self.DISCOVERY_UNIQ_ID not in data:
						continue

					if pin is not None:
						id, _pin, ip, port = data.split("|")
					else:
						id, ip, port = data.split("|")

					if id != self.DISCOVERY_UNIQ_ID:
						continue

					if pin is not None and _pin is not None and pin != _pin:
						continue

					ack_msg = f"{self.DISCOVERY_UNIQ_ID}|Ack".encode()

					sock.sendto(ack_msg, addr)
					return (ip, int(port))

				except (UnicodeDecodeError, ValueError):
					continue

	def stop(self):
		"""
		This cleans the thread spawned by the broacasting device.
		"""
		self._stop_broadcasting()

	def _broadcast(self, pin: str | None):
		"""
		A helper method that handles the actual broadcasting.
		"""
		HOST = self._get_local_ip()

		with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
			sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

			msg = f"{self.DISCOVERY_UNIQ_ID}|{pin}|{HOST}|{self.PORT}" if pin is not None else \
				f"{self.DISCOVERY_UNIQ_ID}|{HOST}|{self.PORT}"

			for _ in range(10):
				if self._event.is_set():
					return

				sock.sendto(msg.encode("utf-8"), self.BROADCAST_ADDR)

				ack_deadline = time.monotonic() + 2.0

				while time.monotonic() < ack_deadline:
					remaining = ack_deadline - time.monotonic()

					if remaining <= 0:
						break

					sock.settimeout(remaining)

					try:
						data, _ = sock.recvfrom(1024)

						data = data.decode()

						if self.DISCOVERY_UNIQ_ID in data:
							parts = data.split("|")

							if self.DISCOVERY_UNIQ_ID != parts[0] or "Ack" != parts[1]:
								raise RuntimeError("Wrong Ack")

							self._event.set()
							return

					except socket.timeout:
						break

	def _stop_broadcasting(self):
		"""
		The helper method that stops the broadcasting and cleans its
		thread
		"""
		self._event.set()
		if self._thread is not None and self._thread.is_alive():
			self._thread.join(timeout=5)

	def _get_local_ip(self):
		"""
		It returns the IP of the receiving device by asking the OS. If the
		device is connected to a WIFI, it returns IP that can be used to
		access the internet else it returns the localhost IP
		"""
		with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
			try:
				sock.connect(("8.8.8.8", 80))

				return sock.getsockname()[0]
			except OSError:
				return "127.0.0.1"





class CFTT:
	"""
	Stands for Custom File Transfer Tool.

	This class creates a TCP connection used for
	transferring data over the network.
	"""
	CHUNK_SIZE = 128 * 1024
	OK_STATUS = b"\x00"
	ERROR_STATUS = b"\x01"

	def __init__(self, **kwargs) -> None:
		self.kwargs = kwargs
		self.channel = DiscoveryChannel()


	def send(self, **kwargs):
		"""
		Handles sending data over the network

		Args:
			kwargs(dict): Contains neccessary informations required to
				data such as, the addr, the file(s) and more
		"""

		addr = (kwargs["ip"], kwargs["port"])

		files_path: list[str] = kwargs["files"]
		# Updates the UI on the progress of the sent files
		on_progress: Callable[[int, int], Any] | None = kwargs.get("on_progress")
		# Returns failed files to the UI so retry can only send files that failed
		on_failed_files: Callable[[list[str]], Any] | None = kwargs.get("on_failed_files")

		failed_files: list[str] = []

		with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
			sock.connect(addr)

			total_files = len(files_path)
			sent = 0

			sock.sendall(total_files.to_bytes(4, "big"))

			for idx, file_path in enumerate(files_path):

				f_size = os.path.getsize(file_path)
				filename =  self._safe_filename(file_path)

				header = self._prep_header(filename, f_size)

				sock.sendall(len(header).to_bytes(4, "big"))
				sock.sendall(header)

				try:
					with open(file_path, "rb") as f:
						while chunk := f.read(self.CHUNK_SIZE):
							sock.sendall(chunk)

					data = self._recv_exact(sock, 1)

					if data != self.OK_STATUS:
						failed_files.append(file_path)

					sent += 1

					if on_progress:
						on_progress(sent, total_files)

				except Exception:
					failed_files.extend(files_path[idx:])
					return

		if on_failed_files is not None and len(failed_files) > 0:
			on_failed_files(failed_files)


	def receive(self, **kwargs):
		"""
		Handles receiving data over the network

		Args:
			kwargs (dict): More information required to receive data such as
				the destination
		"""

		dest: str = kwargs["dest"]
		# Updates the UI on the progress of files that are received
		on_progress: Callable[[int, int], Any] | None = kwargs.get("on_progress")

		self.channel.broadcast()

		with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
			sock.bind(("", self.channel.PORT))
			sock.listen(1)
			sock.settimeout(20.0)

			received = 0

			try:
				conn, _ = sock.accept() # Blocking

				with conn:
					files_len = int.from_bytes(self._recv_exact(conn, 4), "big")

					for _ in range(files_len):
						header_len = int.from_bytes(self._recv_exact(conn, 4), "big")

						header = self._recv_exact(conn, header_len).decode("utf-8")

						filename, size = self._parse_header(header)

						try:
							self._recv_file(conn, size, filename, dest)
							conn.sendall(self.OK_STATUS)

							received += 1

							if on_progress:
								on_progress(received, files_len)

						except (OSError, PermissionError, ConnectionError):
							# Notify the sender that a file failed so they keep
							# record of it and maybe try resending the failed file(s) again
							conn.sendall(self.ERROR_STATUS)
							return

			except socket.timeout:
				# This closes the socket if sock.accept() stays longer than 20s
				# Notice it does not affect the new socket (conn) returned by sock.accept()
				return
			except OSError:
				# This will be logged
				return


	def stop(self):
		"""A shortcut on DiscoveryChannel().stop() method"""
		self.channel.stop()

	def _prep_header(self, filename: str, size: int) -> bytes:
		"""
		Prepares the header by combining the filename and
		size of the data to be sent. It also replaces \n
		characters fromt the filename with an empty string

		Args:
			filename (str): The name of the data or file being sent
			size (int): The size of the file in bytes

		Returns:
			bytes: A header byte comprising of the filename and the
				size of the file separated by a newline character.
		"""
		if "\n" in filename:
			filename = filename.replace("\n", "")

		header = f"{filename}\n{size}".encode("utf-8")

		return header


	def _recv_exact(self, sock: socket.socket, n: int):
		"""Receives the exact amount of bytes from a socket"""
		buf = bytearray()

		while len(buf) < n:
			chunk = sock.recv(n - len(buf))

			if not chunk:
				raise ConnectionError(f"Connection Lost")

			buf.extend(chunk)

		return bytes(buf)

	def _parse_header(self, header: str, delimiter: str = "\n") -> tuple[str, int]:
		"""
		Returns a tuple of filename and size from a header by delimeter sepratation
		"""
		_header = header.split(delimiter, 1)

		return _header[0], int(_header[1])

	def _recv_file(self, conn: socket.socket, size: int, filename: str, dest):
		"""
		Similar to _recv_exact method but this time writes bytes to disk directly.

		Args:
			conn (socket.socket): The socket obj
			size (int): The total size of the of bytes to read from the socket
			filename (str): The name of the file. Data writing to disk will be saved
				against this name
			dest (_type_): The destination path to where the data will be saved.

		"""

		# The filename is rechecked to ensure that the sender didn't tamper with
		# it from its side.
		dest = os.path.join(dest, self._safe_filename(filename))

		with open(dest, "wb", buffering=0) as f:
			remaining = size

			try:
				while remaining > 0:
					chunk = conn.recv(min(self.CHUNK_SIZE, remaining))

					if not chunk:
						raise ConnectionError("Connection Lost")

					f.write(chunk)

					remaining -= len(chunk)
			
			except (OSError, PermissionError):
				raise

	def _safe_filename(self, filename: str):
		"""
		This validates the filename provided by the
		sender to ensure no malicious name is provided

		Args:
			filename (str): The name of the file

		Returns:
			filename (str): The safe destination path
		"""

		name = filename.replace("\\", "/")
		name = name.rstrip("/")
		name = os.path.basename(name)

		if name in ("", ".", "..", ","):
			raise ValueError(f"Invalid filename: {name!r}")

		return name



