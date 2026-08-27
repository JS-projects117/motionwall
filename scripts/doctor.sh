#!/usr/bin/env bash
# Motionwall diagnostics - run after logging back in, paste the output if the
# wallpaper is not showing.
echo "== session =="; echo "type: ${XDG_SESSION_TYPE:-?}  shell: $(gnome-shell --version 2>/dev/null)"
echo "== extension =="; gnome-extensions info motionwall@motionwall 2>&1 | grep -E 'State|Name|Version|Error' || echo "not found"
echo "== enabled list =="; gsettings get org.gnome.shell enabled-extensions | tr ',' '\n' | grep -i motionwall || echo "not enabled"
echo "== config =="; python3 - <<'PY' 2>/dev/null
import json, os
p=os.path.expanduser('~/.config/motionwall/config.json')
d=json.load(open(p))
print({k:d.get(k) for k in ('video','enabled','paused','scaling','mute','hwdec')})
print('video exists:', os.path.isfile(d.get('video','')))
PY
echo "== mpv renderer processes =="; pgrep -af 'motionwall-wallpaper|x11-name=motionwall' | grep -v pgrep || echo "no mpv wallpaper process"
echo "== status =="; ~/.local/bin/motionwall status 2>&1
echo "== recent shell log (motionwall) =="; journalctl --user -b --no-pager 2>/dev/null | grep -iE 'motionwall' | tail -40 || echo "(need: journalctl --user -b | grep motionwall)"
