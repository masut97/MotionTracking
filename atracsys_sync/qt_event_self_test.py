"""Native-window churn and CSV drop regression, entirely synthetic."""
from pathlib import Path


def run(output, *, cycles=250, require_scoped=True):
    from PySide6.QtCore import QEvent,QMimeData,QPoint,QPointF,QUrl,Qt
    from PySide6.QtGui import QWindow,QEnterEvent,QDragEnterEvent,QDragMoveEvent,QDropEvent,QDragLeaveEvent
    from PySide6.QtWidgets import QApplication,QWidget,QFrame,QTabWidget,QLabel,QPushButton,QLineEdit,QDialog
    from .layout import _RetrospectiveController

    class Counter(_RetrospectiveController):
        foreign=0
        def eventFilter(self,watched,event):
            if isinstance(watched,QWindow): self.foreign+=1
            return super().eventFilter(watched,event)

    app=QApplication.instance()
    owner=QWidget();owner.setAcceptDrops(True)
    zone=QFrame(owner);field=QLineEdit(owner)
    tabs=QTabWidget(owner);tabs.addTab(QWidget(),'Live');tabs.addTab(QWidget(),'Analysis')
    controller=Counter(owner,tabs,1,QLabel(owner),QPushButton(owner),zone)
    dropped=[];controller.start=lambda paths:dropped.extend(paths)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    source=output/'synthetic_FE.csv';source.write_text('synthetic,value\n0,0\n',encoding='utf-8')
    mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(source))])
    actions=Qt.DropAction.CopyAction;buttons=Qt.MouseButton.LeftButton;modifiers=Qt.KeyboardModifier.NoModifier
    try:
        for target in (owner,zone,field):
            enter=QDragEnterEvent(QPoint(4,4),actions,mime,buttons,modifiers)
            QApplication.sendEvent(target,enter)
            assert enter.isAccepted() and tabs.currentIndex()==1
            move=QDragMoveEvent(QPoint(5,5),actions,mime,buttons,modifiers)
            QApplication.sendEvent(target,move);assert move.isAccepted()
            drop=QDropEvent(QPointF(5,5),actions,mime,buttons,modifiers)
            QApplication.sendEvent(target,drop);assert drop.isAccepted()
            assert not zone.property('dragActive')
        assert dropped==[source.resolve()]*3
        QApplication.sendEvent(zone,QDragLeaveEvent())
        for i in range(cycles):
            native=QWindow();native.create()
            event=QEnterEvent(QPointF(1,1),QPointF(1,1),QPointF(1,1))
            QApplication.sendEvent(native,event)
            native.destroy()
            dialog=QDialog()
            dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen,True)
            dialog.winId();dialog.show();app.processEvents()
            dialog.close();dialog.deleteLater();native.deleteLater()
            QApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        if require_scoped:
            assert controller.foreign==0,'CSV filter intercepted a native QWindow event'
        return {'passed':True,'native_window_cycles':cycles,'foreign_native_events':controller.foreign,
                'csv_drop_main_window_zone_and_input':True,'hardware_used':False}
    finally:
        # The baseline diagnostic can still have an old application-wide filter.
        app.removeEventFilter(controller)
        owner.close();owner.deleteLater()
        QApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
