"""D-Bus introspection XML for the daemon."""

from . import DAEMON_INTERFACE

DAEMON_XML = f"""
<node>
  <interface name="{DAEMON_INTERFACE}">
    <method name="SetWallpaper"><arg type="s" name="path" direction="in"/></method>
    <method name="Pause"/>
    <method name="Resume"/>
    <method name="Toggle"/>
    <method name="Stop"/>
    <method name="Quit"/>
    <method name="ReloadConfig"/>
    <method name="GetStatus"><arg type="s" name="json" direction="out"/></method>
    <signal name="StatusChanged"><arg type="s" name="state"/></signal>
    <property name="Version" type="s" access="read"/>
  </interface>
</node>
"""
