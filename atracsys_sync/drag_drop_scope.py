"""Limit CSV drop handling to owned QWidget targets, never native QWindows."""


def belongs_to_window(widget, window, widget_type):
    if not isinstance(widget, widget_type):
        return False
    try:
        # Some panels use ``self.window`` for their MainWindow reference.
        # Invoke QWidget's method directly rather than that Python attribute.
        return widget_type.window(widget) is window
    except RuntimeError:  # Qt has already destroyed this widget.
        return False


def install_drop_filters(window, drop_zone, controller, widget_type):
    drop_zone.setAcceptDrops(True)
    seen=set();targets=[]
    for widget in (window,drop_zone,*window.findChildren(widget_type)):
        if id(widget) in seen or not belongs_to_window(widget,window,widget_type):
            continue
        seen.add(id(widget))
        if widget is window or widget is drop_zone or widget.acceptDrops():
            widget.installEventFilter(controller)
            targets.append(widget)
    return tuple(targets)


def owned_event_type(widget,event,window,widget_type):
    # This check must precede every access to the native event wrapper.
    if not belongs_to_window(widget,window,widget_type):
        return None
    return event.type()
