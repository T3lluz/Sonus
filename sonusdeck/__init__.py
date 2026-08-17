"""SonusDeck: a hotkey volume panel for PipeWire.

Layout mirrors SonarDeck so the two stay easy to compare:

    config.py    identity, channel list, settings
    theme.py     colours, metrics, fonts
    graph.py     virtual Game/Chat/Media/Aux sinks (PipeWire loopbacks)
    pipewire.py  reading and driving the graph
    effects.py   EasyEffects, the per-device equaliser
    shortcut.py  KDE global shortcut registration
    ipc.py       single instance and the toggle channel
    ui/          strip, appmixer, widgets, panel
    app.py       entry point
"""

__version__ = "0.1.0"
