# Qt event-filter crash regression

The unmodified baseline reproduced a Windows access violation in QtGui.pyd.
Python's native fault handler located the failing GUI-thread call at
`_RetrospectiveController.eventFilter`, reading `event.type()` before checking
the event receiver. The CSV drop controller had installed its filter on the
whole QApplication and therefore intercepted unrelated native QWindow events.

The controller now installs filters only on the main QWidget, its CSV drop
zone and existing child widgets accepting drops. Receiver type and ownership
are checked before the native event wrapper is accessed. Temporary native
windows, other dialogs and destroyed widgets are excluded. CSV drag/drop on
the main window, drop zone and child inputs remains supported.

Regression coverage includes pure scope/lifetime guards, real QWindow and
dialog creation/destruction, synthetic CSV drag/move/drop events, and repeated
complete packaged mock recordings and report generation. Native fault logs
are enabled only in selftest mode. No live device is opened by these tests.

Successful stress runs demonstrate removal of the reproduced access path;
they do not establish that every possible third-party Qt failure is impossible.
