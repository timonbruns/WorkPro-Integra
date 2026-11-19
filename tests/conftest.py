"""pytest-Konfiguration.

* HA-Tests: Custom Integrations freigeben.
* Lokale UDP-Sockets (simuliertes Gerät auf 127.0.0.1) erlauben – das
  HA-Testpaket sperrt Netzwerkzugriffe sonst grundsätzlich.
"""
import pytest

try:
    import pytest_socket
except ImportError:  # reine Client-Tests ohne HA-Testpaket
    pytest_socket = None


@pytest.fixture(autouse=True)
def _allow_localhost_udp():
    if pytest_socket is not None:
        pytest_socket.enable_socket()
    yield


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request):
    if "hass" in request.fixturenames:
        request.getfixturevalue("enable_custom_integrations")
    yield
