"""Sonus: a hotkey volume panel for PipeWire.

    config.py    identity, channel list, settings
    theme.py     colours, metrics, fonts
    graph.py     virtual Game/Chat/Media/Aux sinks (PipeWire filter-chains)
    pipewire.py  reading and driving the graph
    effects.py   EasyEffects post-mix slot (no second EQ)
    eq.py        per-category 10-band equaliser
    shortcut.py  KDE global shortcut registration
    ipc.py       single instance and the toggle channel
    ui/          strip, appmixer, eqpanel, widgets, panel
    app.py       entry point
"""

__version__ = "0.1.0"
