from types import SimpleNamespace as NS

from atracsys_sync.drag_drop_scope import install_drop_filters, owned_event_type


class Widget:
    def __init__(self,window=None,accepts=False):
        self.owner=window or self;self.accepts=accepts;self.filters=[];self.children=[]
    def window(self):
        if getattr(self,'deleted',False):raise RuntimeError('C++ object deleted')
        return self.owner
    def acceptDrops(self):return self.accepts
    def setAcceptDrops(self,value):self.accepts=value
    def installEventFilter(self,controller):self.filters.append(controller)
    def findChildren(self,kind):return self.children


class UnsafeEvent:
    def type(self):raise AssertionError('A foreign native event must never be inspected')


def test_drop_scope_includes_child_inputs_but_not_other_windows():
    window=Widget();zone=Widget(window);input=Widget(window,True)
    label=Widget(window);dialog=Widget(accepts=True)
    window.children=[zone,input,input,label,dialog]
    controller=object()
    targets=install_drop_filters(window,zone,controller,Widget)
    assert set(targets)=={window,zone,input}
    assert input.filters==[controller] and zone.acceptDrops()
    assert not label.filters and not dialog.filters


def test_nonwidget_event_is_rejected_before_native_access():
    window=Widget()
    assert owned_event_type(object(),UnsafeEvent(),window,Widget) is None


def test_other_dialog_event_is_rejected_before_native_access():
    assert owned_event_type(Widget(),UnsafeEvent(),Widget(),Widget) is None


def test_destroyed_widget_is_rejected_before_native_access():
    class Destroyed(Widget):
        deleted=True
    assert owned_event_type(Destroyed(),UnsafeEvent(),Widget(),Widget) is None


def test_python_window_attribute_does_not_shadow_qwidget_ownership_check():
    window=Widget();panel=Widget(window,True);panel.window=window
    assert owned_event_type(panel,NS(type=lambda:42),window,Widget)==42


def test_owned_drop_event_retains_original_type():
    window=Widget();child=Widget(window,True)
    assert owned_event_type(child,NS(type=lambda:42),window,Widget)==42
