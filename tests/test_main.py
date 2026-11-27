from sock import DiscoveryChannel
from src.main import ReceiveDiscoveryChannel, ReceiveSession


def test_receive_broadcast_reports_oserror(monkeypatch):
    session = ReceiveSession()
    discovery = ReceiveDiscoveryChannel(session)

    def fail_broadcast(*args):
        assert len(args) == 2
        raise OSError("network unavailable")

    monkeypatch.setattr(DiscoveryChannel, "_broadcast", fail_broadcast)
    discovery.broadcast()
    thread = discovery._thread
    assert thread is not None
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert isinstance(session.broadcast_error, OSError)
