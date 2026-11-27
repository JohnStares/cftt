import asyncio
import math
import os
import random
import threading
from dataclasses import dataclass, field

import flet as ft

from sock import DiscoveryChannel, CFTT


channel = DiscoveryChannel()
sock = CFTT()

BLUE = ft.Colors.BLUE_600
RECEIVE_TIMEOUT = 20  # seconds
DESKTOP = {ft.PagePlatform.WINDOWS, ft.PagePlatform.MACOS, ft.PagePlatform.LINUX}


# ----------------------------------------------------------------- backend glue
def stop_channel(target: DiscoveryChannel | None = None):
    try:
        (target or channel).stop()
    except Exception:
        pass


def cancel_receive():
    """TODO: abort an in-flight sock.receive() here (close its listening
    socket, set its stop flag, etc). Until you do, a timed-out receive thread
    keeps running in the background."""
    pass


# ----------------------------------------------------------------- state
@dataclass
class ReceiveSession:
    receive_done: threading.Event = field(default_factory=threading.Event)
    broadcast_error: Exception | None = None
    receive_error: Exception | None = None
    discovery: DiscoveryChannel | None = None
    received_files: int = 0
    total_files: int = 0


class ReceiveDiscoveryChannel(DiscoveryChannel):
    def __init__(self, session: ReceiveSession):
        super().__init__()
        self.session = session

    def _broadcast(self, pin: str | None):
        try:
            super()._broadcast(pin)
        except OSError as ex:
            self.session.broadcast_error = ex


@dataclass
class AppState:
    files: list[str] = field(default_factory=list)  # kept in memory for retries
    failed_files: list[str] = field(default_factory=list)
    send_pin: str | None = None
    recv_pin: str | None = None
    dest: str = ""
    default_dest: str = ""
    route: str = "/"
    task: asyncio.Task | None = None
    on_enter: object = None
    receive: ReceiveSession | None = None


# ----------------------------------------------------------------- UI helpers
def big_button(text, on_click, *, icon=None, width=150, height=70, disabled=False):
    return ft.Button(
        text,
        icon=icon,
        on_click=on_click,
        width=width,
        height=height,
        disabled=disabled,
        style=ft.ButtonStyle(
            bgcolor={
                ft.ControlState.DISABLED: ft.Colors.GREY_400,
                ft.ControlState.DEFAULT: BLUE,
            },
            color=ft.Colors.WHITE,
            shape=ft.RoundedRectangleBorder(radius=14),
            text_style=ft.TextStyle(size=20, weight=ft.FontWeight.BOLD),
        ),
    )


async def main(page: ft.Page):
    page.title = "CFTT"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.window.width = 480  # ignored on mobile
    page.window.height = 780

    state = AppState()

    # ---- persistent settings + default destination ----
    try:
        prefs = ft.SharedPreferences()
    except Exception:
        prefs = None
    try:
        downloads = await ft.StoragePaths().get_downloads_directory()

        if downloads is None:
            downloads = os.getcwd()
            
    except Exception:
        downloads = os.getcwd()

    state.default_dest = os.path.join(downloads, "cftt")
    saved = None
    if prefs:
        try:
            saved = await prefs.get("dest")
        except Exception:
            pass
    state.dest = saved or state.default_dest

    async def save_dest():
        if prefs:
            try:
                await prefs.set("dest", state.dest)
            except Exception:
                pass

    # ---- navigation ----
    PARENT = {
        "/send": "/",
        "/send/pin": "/send",
        "/send/search": "/send",
        "/receive": "/",
        "/receive/session": "/",
        "/settings": "/",
    }

    def nav(route):
        async def handler(e):
            await page.push_route(route)
        return handler

    def appbar(title):
        return ft.AppBar(
            leading=ft.IconButton(ft.Icons.ARROW_BACK, on_click=nav(PARENT[state.route])),
            title=ft.Text(title),
            bgcolor=BLUE,
            color=ft.Colors.WHITE,
        )

    C = ft.CrossAxisAlignment.CENTER
    M = ft.MainAxisAlignment.CENTER

    # ================================================================= HOME
    def home_view():
        return ft.View(
            route="/",
            appbar=ft.AppBar(
                bgcolor=ft.Colors.TRANSPARENT,
                actions=[ft.IconButton(ft.Icons.SETTINGS, tooltip="Settings", on_click=nav("/settings"))],
            ),
            vertical_alignment=M,
            horizontal_alignment=C,
            controls=[
                ft.Text("CFTT", size=72, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_700),
                ft.Container(height=40),
                ft.Row(
                    alignment=M,
                    spacing=20,
                    wrap=True,
                    controls=[
                        big_button("Send", nav("/send")),
                        big_button("Receive", nav("/receive")),
                    ],
                ),
            ],
        )

    # ================================================================= SEND: pick files
    def files_view():
        file_list = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=0)
        send_btn = big_button("Send", nav("/send/pin"), icon=ft.Icons.SEND, width=300, height=60)

        def remover(path):
            def _remove(e):
                state.files.remove(path)
                refresh()
                page.update()
            return _remove

        def refresh():
            if state.files:
                file_list.controls = [
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.INSERT_DRIVE_FILE),
                        title=ft.Text(os.path.basename(p), max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        subtitle=ft.Text(p, size=11, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        trailing=ft.IconButton(ft.Icons.DELETE, on_click=remover(p)),
                    )
                    for p in state.files
                ]
            else:
                file_list.controls = [
                    ft.Container(ft.Text("No files selected yet", italic=True), padding=30, alignment=ft.Alignment.CENTER)
                ]
            send_btn.disabled = not state.files

        async def add_files(e):
            if state.failed_files:
                state.files = list(state.failed_files)
                state.failed_files.clear()
            picked = await ft.FilePicker().pick_files(allow_multiple=True)
            for f in picked or []:
                if f.path and f.path not in state.files:
                    state.files.append(f.path)
            refresh()
            page.update()

        refresh()
        return ft.View(
            route="/send",
            appbar=appbar("Select files"),
            horizontal_alignment=C,
            controls=[
                ft.Button("Add files", icon=ft.Icons.ADD, on_click=add_files),
                file_list,
                send_btn,
            ],
        )

    # ================================================================= PIN page (shared)
    def pin_view(route, title, on_pin, on_skip):
        pin_field = ft.TextField(
            label="PIN", password=True, can_reveal_password=True,
            width=280, text_align=ft.TextAlign.CENTER,
        )
        use_btn = big_button("Use PIN", None, width=280, height=56, disabled=True)

        def changed(e):
            use_btn.disabled = not pin_field.value.strip()
            use_btn.update()

        async def use_pin(e):
            value = pin_field.value.strip()
            if value:
                await on_pin(value)

        async def skip(e):
            await on_skip()

        pin_field.on_change = changed
        use_btn.on_click = use_pin
        return ft.View(
            route=route,
            appbar=appbar(title),
            vertical_alignment=M,
            horizontal_alignment=C,
            controls=[
                ft.Icon(ft.Icons.LOCK_OUTLINE, size=56, color=BLUE),
                ft.Text("Use a PIN to connect directly?", size=20, text_align=ft.TextAlign.CENTER),
                ft.Container(height=10),
                pin_field,
                use_btn,
                ft.TextButton("Skip", on_click=skip),
            ],
        )

    def send_pin_view():
        async def on_pin(v):
            state.send_pin = v
            await page.push_route("/send/search")

        async def on_skip():
            state.send_pin = None
            await page.push_route("/send/search")

        return pin_view("/send/pin", "Send", on_pin, on_skip)

    def receive_pin_view():
        async def on_pin(v):
            state.recv_pin = v
            await page.push_route("/receive/session")

        async def on_skip():
            state.recv_pin = None
            await page.push_route("/receive/session")

        return pin_view("/receive", "Receive", on_pin, on_skip)

    # ================================================================= SEND: radar / search
    def search_view():
        size = 260
        status = ft.Text(size=20, weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.CENTER)
        body = ft.Column(horizontal_alignment=C, alignment=M, spacing=20, expand=True)

        # --- radar widget ---
        sweep = ft.Container(
            width=size, height=size,
            alignment=ft.Alignment.TOP_CENTER,
            rotate=ft.Rotate(angle=0, alignment=ft.Alignment.CENTER),
            animate_rotation=ft.Animation(1600, ft.AnimationCurve.LINEAR),
            content=ft.Container(width=3, height=size / 2, bgcolor=ft.Colors.GREEN_ACCENT_400),
        )

        def ring(d):
            return ft.Container(
                width=size, height=size, alignment=ft.Alignment.CENTER,
                content=ft.Container(
                    width=d, height=d, border_radius=d / 2,
                    border=ft.Border.all(1, ft.Colors.GREEN_700),
                ),
            )

        radar = ft.Stack(
            width=size, height=size,
            controls=[
                ft.Container(width=size, height=size, bgcolor="#0b1f14", border_radius=size / 2),
                ring(size * 0.33), ring(size * 0.66), ring(size - 2),
                sweep,
            ],
        )

        def add_blip():
            a = random.uniform(0, math.tau)
            r = random.uniform(0.25, 0.8) * size / 2
            radar.controls.append(
                ft.Container(
                    width=12, height=12, border_radius=6, bgcolor=ft.Colors.GREEN_ACCENT_400,
                    left=size / 2 + r * math.cos(a) - 6,
                    top=size / 2 + r * math.sin(a) - 6,
                )
            )

        async def spin():
            angle = 0.0
            while True:
                angle += math.tau
                sweep.rotate = ft.Rotate(angle=angle, alignment=ft.Alignment.CENTER)
                sweep.update()
                await asyncio.sleep(1.6)

        def render(*controls, text=""):
            status.value = text
            body.controls = list(controls)
            page.update()

        scan = {"token": None, "done": None, "devices": {}}

        async def retry(e=None):
            done = scan["done"]
            if done and not done.is_set():
                render(ft.ProgressRing(), text="Finishing device scan...")
                await asyncio.to_thread(done.wait)
            state.task = asyncio.create_task(run())

        retry_btn = ft.Button("Retry", icon=ft.Icons.REFRESH, on_click=retry)
        home_btn = ft.Button("Done", icon=ft.Icons.HOME, on_click=nav("/"))

        async def send_to(ip, port, files, device_name=None):
            sent_count = 0
            reported_failed_files = []
            progress_active = True

            async def update_progress(sent, total):
                if not progress_active:
                    return
                status.value = f"Sending files... ({sent}/{total})"
                body.controls = [ft.ProgressBar(value=sent / total if total else 0)]
                page.update()

            async def update_started():
                if not progress_active:
                    return
                status.value = "Sending files..."
                body.controls = [ft.ProgressRing()]
                page.update()

            def on_start():
                page.run_task(update_started)

            def on_progress(sent, total):
                nonlocal sent_count
                sent_count = sent
                page.run_task(update_progress, sent, total)

            def on_failed_files(failed_files):
                reported_failed_files[:] = failed_files

            target = device_name or ip
            render(ft.ProgressRing(), text=f"Connecting to {target}...")
            try:
                await asyncio.to_thread(
                    lambda: sock.send(
                        ip=ip,
                        port=port,
                        files=files,
                        on_start=on_start,
                        on_progress=on_progress,
                        on_failed_files=on_failed_files,
                    )
                )
            except Exception as ex:
                progress_active = False
                state.failed_files = (
                    reported_failed_files or files[sent_count:] or list(files)
                )
                render(ft.Text(str(ex), color=ft.Colors.RED_600), retry_btn, text="Transfer failed")
                return
            progress_active = False
            state.failed_files = list(reported_failed_files)
            if state.failed_files:
                render(
                    ft.Text(f"{len(state.failed_files)} file(s) could not be sent."),
                    retry_btn,
                    text="Transfer incomplete",
                )
                return
            state.files.clear()
            render(ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN_600, size=72), home_btn, text="Sent!")

        async def run():
            pin = state.send_pin
            files = list(state.failed_files or state.files)
            token = object()
            scan["token"] = token
            scan["devices"] = {}
            scan_done = threading.Event()
            scan["done"] = scan_done
            spinner = None
            if pin:
                render(ft.ProgressRing(), text="Looking for the device with that PIN...")
            else:
                render(radar, text="Scanning for nearby devices...")
                spinner = asyncio.create_task(spin())

            def choose_device(device):
                async def chosen(e):
                    if scan["token"] is not token:
                        return
                    scan["token"] = None
                    if spinner:
                        spinner.cancel()
                    ip, port, name = device
                    await send_to(ip, port, files, name)

                return chosen

            async def render_devices(include_retry):
                devices = list(scan["devices"].values())
                controls: list[ft.Control] = [radar]
                for device in devices:
                    _, _, name = device
                    controls.append(
                        ft.Card(
                            content=ft.ListTile(
                                leading=ft.Icon(ft.Icons.LAPTOP),
                                title=ft.Text(name),
                                subtitle=ft.Text("Tap to send"),
                                on_click=choose_device(device),
                            ),
                        )
                    )
                if include_retry:
                    controls.append(ft.TextButton("Scan again", on_click=retry))
                render(
                    *controls,
                    text="Select a device" if devices else "Scanning for nearby devices...",
                )

            async def show_device(device):
                if scan["token"] is not token or state.route != "/send/search":
                    return
                _, _, name = device
                if name in scan["devices"]:
                    return
                scan["devices"][name] = device
                add_blip()
                await render_devices(include_retry=scan_done.is_set())

            def on_device_found(device):
                page.run_task(show_device, device)

            error = None
            result = None

            def listen():
                try:
                    if pin:
                        return channel.listen(pin=pin)
                    return channel.listen(ldevices=on_device_found)
                finally:
                    scan_done.set()

            try:
                result = await asyncio.to_thread(listen)
            except Exception as ex:
                error = ex
            finally:
                if spinner:
                    spinner.cancel()

            if scan["token"] is not token:
                return
            if error:
                render(ft.Text(str(error), color=ft.Colors.RED_600), retry_btn, text="Search failed")
                return
            if pin:
                if not isinstance(result, tuple):
                    render(ft.Icon(ft.Icons.SEARCH_OFF, size=64), retry_btn, text="No device found")
                    return
                ip, port, name = result
                scan["token"] = None
                await send_to(ip, port, files, name)
                return

            if not scan["devices"]:
                render(ft.Icon(ft.Icons.SEARCH_OFF, size=64), retry_btn, text="No device found")
                return
            await render_devices(include_retry=True)

        state.on_enter = run
        return ft.View(
            route="/send/search",
            appbar=appbar("Find device"),
            horizontal_alignment=C,
            controls=[ft.Container(status, padding=ft.Padding.only(top=20)), body],
        )

    # ================================================================= RECEIVE session
    def receive_view():
        status = ft.Text(size=20, weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.CENTER)
        body = ft.Column(horizontal_alignment=C, alignment=M, spacing=20, expand=True)

        def render(*controls, text=""):
            status.value = text
            body.controls = list(controls)
            page.update()

        async def retry(e=None):
            previous = state.receive
            if previous and not previous.receive_done.is_set():
                render(ft.ProgressRing(), text="Preparing to receive again...")
                await asyncio.to_thread(previous.receive_done.wait)
            state.task = asyncio.create_task(run())

        retry_btn = ft.Button("Try again", icon=ft.Icons.REFRESH, on_click=retry)
        home_btn = ft.Button("Done", icon=ft.Icons.HOME, on_click=nav("/"))

        async def run():
            dest, pin = state.dest, state.recv_pin
            os.makedirs(dest, exist_ok=True)
            previous = state.receive
            if previous and not previous.receive_done.is_set():
                render(ft.ProgressRing(), text="Preparing to receive...")
                await asyncio.to_thread(previous.receive_done.wait)
            s = ReceiveSession()
            discovery = ReceiveDiscoveryChannel(s)
            s.discovery = discovery
            state.receive = s
            progress_active = True
            receive_started = threading.Event()
            kwargs = {"pin": pin} if pin else {}

            def do_broadcast():
                try:
                    discovery.broadcast(**kwargs)
                except OSError as ex:
                    s.broadcast_error = ex
                except Exception as ex:
                    s.broadcast_error = ex

            async def update_progress(received, total):
                if (
                    not progress_active
                    or state.receive is not s
                    or state.route != "/receive/session"
                ):
                    return
                status.value = f"Receiving files... ({received}/{total})"
                body.controls = [
                    ft.ProgressBar(value=received / total if total else 0),
                    ft.Text(f"Saving to: {dest}", size=12, text_align=ft.TextAlign.CENTER),
                ]
                page.update()

            async def update_started():
                if (
                    not progress_active
                    or state.receive is not s
                    or state.route != "/receive/session"
                ):
                    return
                status.value = "Connected — receiving files..."
                body.controls = [
                    ft.ProgressRing(),
                    ft.Text(f"Saving to: {dest}", size=12, text_align=ft.TextAlign.CENTER),
                ]
                page.update()

            def on_start():
                receive_started.set()
                page.run_task(update_started)

            def on_progress(received, total):
                s.received_files = received
                s.total_files = total
                page.run_task(update_progress, received, total)

            def do_receive():
                try:
                    sock.receive(
                        dest=dest,
                        on_start=on_start,
                        on_progress=on_progress,
                    )
                except Exception as ex:
                    s.receive_error = ex
                finally:
                    s.receive_done.set()

            render(
                ft.ProgressRing(),
                ft.Text(f"Saving to: {dest}", size=12, text_align=ft.TextAlign.CENTER),
                text="Waiting for a sender...",
            )
            threading.Thread(target=do_broadcast, daemon=True).start()
            threading.Thread(target=do_receive, daemon=True).start()

            deadline = asyncio.get_running_loop().time() + RECEIVE_TIMEOUT
            while (
                not s.receive_done.is_set()
                and not receive_started.is_set()
                and asyncio.get_running_loop().time() < deadline
                and s.broadcast_error is None
            ):
                remaining = max(
                    0, math.ceil(deadline - asyncio.get_running_loop().time())
                )
                status.value = f"Waiting for a sender... ({remaining}s)"
                page.update()
                await asyncio.sleep(min(1, remaining or 1))
            if s.broadcast_error:
                await asyncio.to_thread(stop_channel, discovery)
                progress_active = False
                if isinstance(s.broadcast_error, OSError):
                    render(ft.Text("Connected to a WiFi?"), retry_btn, text="Network unavailable")
                else:
                    render(ft.Text(str(s.broadcast_error), color=ft.Colors.RED_600), retry_btn, text="Broadcast failed")
                return
            while not s.receive_done.is_set():
                await asyncio.sleep(0.25)
            await asyncio.to_thread(stop_channel, s.discovery)
            progress_active = False

            if isinstance(s.broadcast_error, OSError) or isinstance(s.receive_error, OSError):
                render(ft.Text("Connected to a WiFi?"), retry_btn, text="Network unavailable")
            elif s.broadcast_error:
                render(ft.Text(str(s.broadcast_error), color=ft.Colors.RED_600), retry_btn, text="Broadcast failed")
            elif s.receive_error:
                render(ft.Text(str(s.receive_error), color=ft.Colors.RED_600), retry_btn, text="Receive failed")
            elif s.received_files == 0:
                render(ft.Text("No files received within 20 seconds."), retry_btn, text="No files received")
            elif s.received_files < s.total_files:
                render(
                    ft.Text(f"Received {s.received_files} of {s.total_files} files."),
                    retry_btn,
                    text="Receive incomplete",
                )
            else:
                render(
                    ft.Icon(ft.Icons.CHECK_CIRCLE, color=ft.Colors.GREEN_600, size=72),
                    ft.Text(f"Saved to: {dest}", size=12, text_align=ft.TextAlign.CENTER),
                    home_btn,
                    text="Files received!",
                )

        state.on_enter = run
        return ft.View(
            route="/receive/session",
            appbar=appbar("Receive"),
            horizontal_alignment=C,
            controls=[ft.Container(status, padding=ft.Padding.only(top=20)), body],
        )

    # ================================================================= SETTINGS
    def settings_view():
        path_text = ft.Text(state.dest, selectable=True)

        async def change(e):
            p = await ft.FilePicker().get_directory_path()
            if p:
                state.dest = p
                path_text.value = p
                await save_dest()
                page.update()

        async def reset(e):
            state.dest = state.default_dest
            path_text.value = state.dest
            await save_dest()
            page.update()

        return ft.View(
            route="/settings",
            appbar=appbar("Settings"),
            padding=20,
            controls=[
                ft.Text("Destination folder", size=18, weight=ft.FontWeight.BOLD),
                path_text,
                ft.Row([
                    ft.Button("Change", icon=ft.Icons.FOLDER_OPEN, on_click=change),
                    ft.TextButton("Reset to default", on_click=reset),
                ]),
            ],
        )

    # ================================================================= ROUTER
    BUILDERS = {
        "/": home_view,
        "/send": files_view,
        "/send/pin": send_pin_view,
        "/send/search": search_view,
        "/receive": receive_pin_view,
        "/receive/session": receive_view,
        "/settings": settings_view,
    }

    async def route_change(e=None):
        route = page.route if page.route in BUILDERS else "/"

        if state.task:
            state.task.cancel()
            state.task = None
        # leaving an unfinished receive session: shut it down
        if state.route == "/receive/session" and route != "/receive/session":
            s = state.receive
            if s and not s.receive_done.is_set():
                stop_channel(s.discovery)
                cancel_receive()
        state.route = route

        chain, r = [], route
        while r:
            chain.append(r)
            r = PARENT.get(r)

        state.on_enter = None
        page.views.clear()
        for r in reversed(chain):
            page.views.append(BUILDERS[r]())
        page.update()

        if state.on_enter:
            state.task = asyncio.create_task(state.on_enter())

    async def view_pop(e):  # Android back button / swipe-back on iOS
        parent = PARENT.get(state.route)
        if parent:
            await page.push_route(parent)

    # ---- make sure channel.stop() runs when a desktop window is closed ----
    if page.platform in DESKTOP:
        page.window.prevent_close = True

        async def on_window_event(e):
            if e.type == ft.WindowEventType.CLOSE:
                await asyncio.to_thread(stop_channel)
                await page.window.destroy()

        page.window.on_event = on_window_event

    page.on_route_change = route_change
    page.on_view_pop = view_pop
    await route_change()

if __name__ == "__main__":
    ft.run(main)