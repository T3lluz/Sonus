registerShortcut("SonusDeckToggle", "Toggle SonusDeck", "HOTKEY", function () {
    callDBus("dev.t3lluz.SonusDeck", "/Panel", "dev.t3lluz.SonusDeck", "Toggle");
});
