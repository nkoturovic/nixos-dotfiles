
Firefox scaling

Firefox fix scaling actually
display.os-zoom-behavior

Wrong: ====
You can set layout.css.devPixelsPerPx to 1.0 (default is -1) on the about:config page. Adjust its value in 0.1 or 0.05 steps (1.1 to enlarge or 0.9 to reduce) until icons or text looks right. You may need values above 2.0 if you have a high resolution display but make sure not to use values too large or too small.

modifying layout.css.devPixelsPerPx affects user interface and webpages (global zoom)
Firefox has a Zoom section in Settings to set the default zoom level for webpages.

Settings -> General -> Language and Appearance -> Zoom
You can open the about:config page via the location/address bar. If you get the warning page, you can click the "Accept the Risk and Continue" button.

^^: Set it to 1.33
Wrong: ====

Install: qt5-qtstyleplugins
Update: /usr/libexec/sway-systemd/session.sh

Disabled services: docker docker.socket

QT_QPA_PLATFORMTHEME=KDE (if using plasma)(or qt5t if not)
to: /etc/environment

QT_QPA_PLATFORMTHEME=KDE

if [[ "$XDG_SESSION_DESKTOP" == "Sway" ]] ; then
    XDG_CURRENT_DESKTOP="sway"
fi
