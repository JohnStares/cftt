import socket
import time
import threading
import os

class DiscoveryChannel:
	"""
	A class that aids the two machines willing to exchange data discover themselves
	with the use of UDP
	"""
	def __init__(self, broadcast_ip: str | None = None, broadcast_port: int | None = None) -> None:
		"""
		An instance of the `DiscoveryChannel` class

		Args:
			broadcast_ip (str | None): T
			broadcast_port (str | None): T
		"""

		self.BROADCAST_IP = broadcast_ip or "255.255.255.255"
		self.BROADCAST_PORT = broadcast_port or 5001
		self.BROADCAST_ADDR = (self.BROADCAST_IP, self.BROADCAST_PORT)

		self.DISCOVERY_UNIQ_ID = "DCFTTX"
		self.PORT = 5000
		self._event = threading.Event()
		self._thread = None


	def broadcast(self):
		"""
		The receiving device needs a way to shoutout its IP and PORT
		over the network so whoever requires it can get it.

		It spins up its own thread for this operation. Remeber to call
		stop() method to clean the thread after broadcast is done
		"""
		self._thread = threading.Thread(target=self._broadcast, daemon=True)

		self._thread.start()

	def listen(self):
		"""
		The sending device needs a way to get the IP and PORT of the device it
		needs to send data to over the network. The receiving device needs to
		be broadcasting its signature for connection to be made.

		Returns:
			ADDR (tuple[str, int] | None): The IP and Port of the broadcasting device
				or None if it didn't find any.
		"""
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

					id, ip, port = data.split("|")

					if id != self.DISCOVERY_UNIQ_ID:
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

	def _broadcast(self):
		"""
		A helper method that handles the actual broadcasting.
		"""
		HOST = self._get_local_ip()

		with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
			sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

			msg = f"{self.DISCOVERY_UNIQ_ID}|{HOST}|{self.PORT}".encode("utf-8")

			for _ in range(10):
				if self._event.is_set():
					return

				sock.sendto(msg, self.BROADCAST_ADDR)

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

	def __init__(self, **kwargs) -> None:
		self.kwargs = kwargs
		self.channel = DiscoveryChannel()


	def send(self, **kwargs):
		"""
		Handles sending data over the network

		Args:
			kwargs(dict): Contains neccessary informations required to
				data such as, the addr, the file(s) 
		"""
		# Normally, it gets the file it wants to
		# Send from the UI. But now i will hardcode
		# A particular file

		addr = self.channel.listen()

		if addr is None:
			print("No receiver found")
			return None

		with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
			sock.connect(addr)

			f_size = os.path.getsize("data.csv")

			header = f"data1.csv\n{f_size}".encode("utf-8")

			sock.sendall(len(header).to_bytes(4, "big"))
			sock.sendall(header)

			with open("data.csv", "rb") as f:
				while chunk := f.read(self.CHUNK_SIZE):
					sock.sendall(chunk)


	def receive(self, *kwargs):
		"""
		Handles receiving data over the network

		Args:
			kwargs (dict): More information required to receive data such as
				the destination
		"""
		self.channel.broadcast()


		with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
			sock.bind(("", self.channel.PORT))

			sock.listen(1)

			conn, _ = sock.accept() # Blocking

			with conn:
				header_len = int.from_bytes(self._recv_exact(conn, 4), "big")

				header = self._recv_exact(conn, header_len).decode("utf-8")

				filename, size = self._parse_header(header)

				self._write_to_file(conn, size, filename)


	def stop(self):
		self.channel.stop()


	def _recv_exact(self, sock: socket.socket, n: int):
		buf = bytearray()

		while len(buf) < n:
			chunk = sock.recv(n - len(buf))

			if not chunk:
				raise ConnectionError(f"Closed")

			buf.extend(chunk)

		return bytes(buf)

	def _parse_header(self, header: str, delimiter: str = "\n") -> tuple[str, int]:
		_header = header.split(delimiter, 1)

		return _header[0], int(_header[1])

	def _write_to_file(self, conn: socket.socket, size: int, filename: str, dest: str = "."):
		with open(f"{dest}/{filename}", "wb") as f:
			remaining = size

			while remaining > 0:
				chunk = conn.recv(min(self.CHUNK_SIZE, remaining))

				if not chunk:
					break

				f.write(chunk)

				remaining -= len(chunk)



