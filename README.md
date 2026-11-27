# CFTT

**CFTT** stands for **Custom File Transfer Tool**. The name is a little
unusual; it was a practical choice for a personal project, not a polished
product name.

CFTT is a small app for sending files directly between devices on the same
local network. It is being built for my own use and as a project to work on.
It is still under development and has not been packaged for general use.

## What it can do

- Select and send one or more files to another device running CFTT.
- Find devices on the local network by their device name, or connect using a
  PIN.
- Receive files and choose where they are saved.
- Show transfer progress and retry files that did not send successfully.
- Run as a Flet app, with desktop and mobile platforms as development targets.

Both devices need to be on a network that allows them to communicate with each
other. Network or firewall restrictions may prevent discovery or transfers.

## Development setup

The project requires Python 3.10 or later. Using [uv](https://docs.astral.sh/uv/)
is the easiest way to install the project and its development dependencies:

```bash
uv sync --group dev
```

Run the app in desktop development mode:

```bash
uv run flet run
```

The Flet app source is in [`src/`](src/) in [`src/main.py`](src/main.py) file, with its entry point at
[`main.py`](main.py). The file-transfer and device-discovery code is in
[`src/sock.py`](src/sock.py).

## Project status

This is a work in progress for personal use. It is **not currently bundled as
an installer or app package**, and it is not being presented as a production
file-transfer product. Platform packaging, device compatibility, and network
behavior still need testing on the systems where it may eventually be used.

## License

No license has been specified yet. Until one is added, assume that all rights
are reserved.
